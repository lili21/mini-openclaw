import asyncio
import json
import logging
import os
import ssl
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from agent.core import AgentResponse
from agent.media import (
    download_feishu_file,
    download_feishu_image,
    image_to_base64_url,
    parse_file_content,
)

if os.getenv("DISABLE_SSL_VERIFY", "").lower() == "true":
    import websockets
    import websockets.asyncio.client

    _ssl_context = ssl.create_default_context()
    _ssl_context.check_hostname = False
    _ssl_context.verify_mode = ssl.CERT_NONE

    _original_connect = websockets.connect

    def _patched_connect(uri, **kwargs):
        if uri.startswith("wss://"):
            kwargs["ssl"] = _ssl_context
        return _original_connect(uri, **kwargs)

    websockets.connect = _patched_connect
    if hasattr(websockets.asyncio.client, "connect"):
        websockets.asyncio.client.connect = _patched_connect

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    CreateMessageReactionRequest,
    CreateMessageReactionRequestBody,
    DeleteMessageReactionRequest,
    PatchMessageRequest,
    PatchMessageRequestBodyBuilder,
    UpdateMessageRequest,
    UpdateMessageRequestBodyBuilder,
    Emoji,
    ListMessageReactionRequest,
)

from adapters.base import BaseAdapter
from agent.tools import save_memory
from config import (
    get_owner_chat_id,
    save_owner_chat_id,
    get_user_model,
    save_user_model,
    AVAILABLE_MODELS,
)
from storage.session import archive_session, list_sessions, load_session

logger = logging.getLogger(__name__)


@dataclass
class FeishuMessage:
    msg_type: str
    message: Any
    chat_id: str
    user_id: str
    message_id: str


class FeishuAdapter(BaseAdapter):
    """飞书适配器 - 使用 lark-oapi SDK 的 WebSocket 客户端"""

    platform_name = "feishu"

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        agent,
        encrypt_key: Optional[str] = None,
        verification_token: Optional[str] = None,
    ):
        self.agent = agent
        self.app_id = app_id
        self.app_secret = app_secret
        self._processed_messages = OrderedDict()
        self._max_cache_size = 1000
        self._pending_media: dict[str, dict] = {}
        self.bot_open_id = os.getenv("FEISHU_BOT_OPEN_ID")
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._user_queues: dict[str, asyncio.Queue] = {}
        self._user_workers: dict[str, asyncio.Task] = {}
        self._stream_output_enabled = self._get_bool_env("FEISHU_STREAM_OUTPUT", True)
        self._stream_interval_seconds = (
            self._get_int_env("FEISHU_STREAM_OUTPUT_INTERVAL_MS", 1200) / 1000
        )
        self._stream_flush_chars = self._get_int_env(
            "FEISHU_STREAM_OUTPUT_FLUSH_CHARS", 100
        )
        self._stream_flush_each_chunk = self._get_bool_env(
            "FEISHU_STREAM_OUTPUT_FLUSH_EACH_CHUNK", False
        )
        self._stream_include_reasoning = self._get_bool_env(
            "FEISHU_STREAM_OUTPUT_INCLUDE_REASONING", False
        )
        self._stream_card_max_chars = 3200
        logger.info(
            "[Feishu] stream output config: "
            f"enabled={self._stream_output_enabled}, "
            f"interval={self._stream_interval_seconds}s, "
            f"flush_chars={self._stream_flush_chars}, "
            f"flush_each_chunk={self._stream_flush_each_chunk}, "
            f"include_reasoning={self._stream_include_reasoning}"
        )

        self.api_client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .log_level(lark.LogLevel.DEBUG)
            .build()
        )

        def on_message(data: lark.im.v1.P2ImMessageReceiveV1):
            logger.info(
                f"Received message event: {lark.JSON.marshal(data.event.message.message_id) if data.event and data.event.message else 'unknown'}"
            )
            logger.debug(f"{lark.JSON.marshal(data)}")
            self._handle_message_event(data)

        event_handler = (
            lark.EventDispatcherHandler.builder(
                encrypt_key or "",
                verification_token or "",
                lark.LogLevel.DEBUG,
            )
            .register_p2_im_message_receive_v1(on_message)
            .build()
        )

        self.ws_client = lark.ws.Client(
            app_id=app_id,
            app_secret=app_secret,
            log_level=lark.LogLevel.DEBUG,
            event_handler=event_handler,
            auto_reconnect=True,
        )

        logger.info(f"Adapter initialized with app_id: {app_id[:10]}...")

    @staticmethod
    def _get_bool_env(name: str, default: bool = False) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _get_int_env(name: str, default: int) -> int:
        value = os.getenv(name)
        try:
            return int(value) if value is not None else default
        except ValueError:
            logger.warning(
                f"Invalid integer for {name}: {value}, use default {default}"
            )
            return default

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        self._event_loop = loop

    async def _get_or_create_user_queue(self, user_id: str) -> asyncio.Queue:
        if user_id not in self._user_queues:
            self._user_queues[user_id] = asyncio.Queue()
            worker = asyncio.create_task(self._user_worker(user_id))
            self._user_workers[user_id] = worker
            logger.info(f"[Feishu] created queue and worker for user {user_id}")
        return self._user_queues[user_id]

    async def _user_worker(self, user_id: str):
        queue = self._user_queues[user_id]
        logger.info(f"[Feishu] worker started for user {user_id}")
        while True:
            try:
                msg: FeishuMessage = await queue.get()
                try:
                    await self._process_message(msg)
                except Exception as e:
                    logger.error(
                        f"[Feishu] error processing message for user {user_id}: {e}"
                    )
                finally:
                    queue.task_done()
            except asyncio.CancelledError:
                logger.info(f"[Feishu] worker cancelled for user {user_id}")
                break
            except Exception as e:
                logger.error(f"[Feishu] worker error for user {user_id}: {e}")

    async def _process_message(self, msg: FeishuMessage):
        if msg.msg_type == "text":
            await self._process_text_message(msg)
        elif msg.msg_type == "file":
            await self._process_file_message(msg)

    async def _process_text_message(self, msg: FeishuMessage):
        content = json.loads(msg.message.content)
        text = content.get("text", "").strip()

        if not text:
            return

        if text.startswith("/"):
            await self._handle_command(text, msg.chat_id, msg.user_id)
            return

        # needs_thinking = await self.agent.check_intent(text)
        needs_thinking = False

        reaction_type = "THINKING" if needs_thinking else "OneSecond"
        await asyncio.to_thread(
            self._add_message_reaction, msg.message_id, reaction_type
        )

        try:
            pending = self._pending_media.get(msg.chat_id)
            if pending and pending.get("type") == "image":
                image_data = pending.get("image_data")
                if image_data is None:
                    await self._send_message_async(
                        msg.chat_id, "图片数据异常，请重新发送图片"
                    )
                    return
                self._pending_media.pop(msg.chat_id, None)

                image_url = image_to_base64_url(image_data)
                multimodal_content = [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ]
                model = get_user_model(self.platform_name, msg.chat_id)
                response = await self._run_agent_response_streaming(
                    msg.chat_id,
                    multimodal_content,
                    model,
                    needs_thinking,
                )
                if response is None:
                    response = await self.agent.run(
                        self.platform_name,
                        msg.chat_id,
                        multimodal_content,
                        model,
                        needs_thinking,
                    )
                    await self._send_agent_response(msg.chat_id, response)
            else:
                model = get_user_model(self.platform_name, msg.chat_id)
                response = await self._run_agent_response_streaming(
                    msg.chat_id, text, model, needs_thinking
                )
                if response is None:
                    response = await self.agent.run(
                        self.platform_name,
                        msg.chat_id,
                        text,
                        model,
                        needs_thinking,
                    )
                    await self._send_agent_response(msg.chat_id, response)
        finally:
            await asyncio.to_thread(
                self._remove_message_reaction, msg.message_id, reaction_type
            )

    async def _process_file_message(self, msg: FeishuMessage):
        content = json.loads(msg.message.content)
        file_key = content.get("file_key")
        filename = content.get("file_name", "unknown_file")

        if not file_key:
            logger.warning("No file_key in message")
            return

        needs_thinking = True
        reaction_type = "THINKING" if needs_thinking else "OneSecond"
        await asyncio.to_thread(
            self._add_message_reaction, msg.message_id, reaction_type
        )

        try:
            logger.info(f"Processing file: {filename}")
            filepath, file_size = await asyncio.to_thread(
                download_feishu_file, self.api_client, file_key, filename
            )

            if filepath is None:
                if file_size and file_size > 0:
                    size_mb = file_size / (1024 * 1024)
                    await self._send_message_async(
                        msg.chat_id,
                        f"文件太大了 ({size_mb:.1f}MB)，超过 10MB 限制，无法处理",
                    )
                else:
                    await self._send_message_async(
                        msg.chat_id, "文件下载失败，请稍后重试"
                    )
                return

            await self._send_message_async(
                msg.chat_id, f"📁 已收到文件 {filename}，正在解析..."
            )

            parsed_content = await asyncio.to_thread(parse_file_content, filepath)
            if parsed_content is None:
                await self._send_message_async(
                    msg.chat_id, "文件解析失败，不支持的格式"
                )
                return

            prompt = f"""用户上传了一个文件「{filename}」，以下是文件内容：

{parsed_content}

请总结这个文件的主要内容。"""

            model = get_user_model(self.platform_name, msg.chat_id)
            response = await self._run_agent_response_streaming(
                msg.chat_id, prompt, model, needs_thinking
            )
            if response is None:
                response = await self.agent.run(
                    self.platform_name, msg.chat_id, prompt, model, needs_thinking
                )
                await self._send_agent_response(msg.chat_id, response)
        finally:
            await asyncio.to_thread(
                self._remove_message_reaction, msg.message_id, reaction_type
            )

    async def _run_agent_response_streaming(
        self,
        chat_id: str,
        content: str | list[dict],
        model: str,
        needs_thinking: bool,
    ) -> AgentResponse | None:
        if not self._stream_output_enabled:
            logger.info(
                "[Feishu] stream output disabled by FEISHU_STREAM_OUTPUT, fallback to agent.run"
            )
            response = await self.agent.run(
                self.platform_name, chat_id, content, model, needs_thinking
            )
            await self._send_agent_response(chat_id, response)
            return response

        stream_state = {
            "message_id": None,
            "reasoning": "",
            "content": "",
            "last_flush": 0.0,
            "last_len": 0,
            "use_interactive_card": True,
            "flush_running": False,
            "flush_pending": False,
            "flush_task": None,
        }
        flush_lock = asyncio.Lock()

        def _current_payload_length() -> int:
            length = len(stream_state["content"])
            if self._stream_include_reasoning:
                length += len(stream_state["reasoning"])
            return length

        def _should_flush() -> bool:
            now = time.time()
            if stream_state["message_id"] is None:
                return True
            if self._stream_flush_each_chunk:
                return True
            if now - stream_state["last_flush"] >= self._stream_interval_seconds:
                return True
            if (
                _current_payload_length() - stream_state["last_len"]
                >= self._stream_flush_chars
            ):
                return True
            return False

        async def _flush_interactive_message() -> bool:
            if not stream_state["use_interactive_card"]:
                return False

            if stream_state["message_id"] is None:
                message_id = await self._send_interactive_message_async(
                    chat_id,
                    stream_state["reasoning"],
                    stream_state["content"] or "正在生成回复...",
                    status="思考中",
                    finished=False,
                    include_reasoning=self._stream_include_reasoning,
                )
                if message_id is None:
                    stream_state["use_interactive_card"] = False
                    return False
                stream_state["message_id"] = message_id
            else:
                success = await self._update_interactive_message_async(
                    stream_state["message_id"],
                    stream_state["reasoning"],
                    stream_state["content"] or "正在生成回复...",
                    status="思考中",
                    finished=False,
                    include_reasoning=self._stream_include_reasoning,
                )
                if not success:
                    stream_state["use_interactive_card"] = False
                    return False

            stream_state["last_flush"] = time.time()
            stream_state["last_len"] = _current_payload_length()
            return True

        async def _flush_worker() -> None:
            try:
                while (
                    stream_state["flush_pending"]
                    and stream_state["use_interactive_card"]
                ):
                    stream_state["flush_pending"] = False

                    async with flush_lock:
                        if not stream_state["use_interactive_card"]:
                            return
                        flushed = await _flush_interactive_message()
                        if not flushed:
                            return
                        if not stream_state["flush_pending"]:
                            return
            except Exception as e:
                logger.error(f"Error flushing interactive stream card: {e}")
                stream_state["use_interactive_card"] = False

            finally:
                stream_state["flush_running"] = False
                stream_state["flush_task"] = None

        def _request_flush():
            if not stream_state["use_interactive_card"]:
                return

            stream_state["flush_pending"] = True
            if stream_state["flush_running"]:
                return

            stream_state["flush_running"] = True
            stream_state["flush_task"] = asyncio.create_task(_flush_worker())

        async def on_chunk(content_delta: str, reasoning_delta: str):
            if not content_delta and not reasoning_delta:
                return

            if not stream_state["use_interactive_card"]:
                return

            if reasoning_delta:
                stream_state["reasoning"] += reasoning_delta

            if content_delta:
                stream_state["content"] += content_delta

            if not _should_flush():
                return

            _request_flush()

        try:
            response = await self.agent.run_stream(
                self.platform_name,
                chat_id,
                content,
                model,
                needs_thinking,
                on_chunk=on_chunk,
            )
        except Exception as e:
            logger.error(f"Error running stream response: {e}")
            has_error_handled = False
            if (
                stream_state["use_interactive_card"]
                and stream_state["message_id"] is not None
            ):
                updated = await self._update_interactive_message_async(
                    stream_state["message_id"],
                    stream_state["reasoning"],
                    stream_state["content"] or "（生成失败，尝试重新发送）",
                    status="生成失败",
                    finished=True,
                    include_reasoning=self._stream_include_reasoning,
                )
                has_error_handled = has_error_handled or updated

            if not has_error_handled:
                logger.warning(
                    "[Feishu] stream failed before final response, fallback to non-stream"
                )
            return None

        if (
            stream_state["use_interactive_card"]
            and stream_state["message_id"] is not None
        ):
            if stream_state["flush_task"] is not None:
                stream_state["flush_pending"] = True
                await stream_state["flush_task"]

            updated = await self._update_interactive_message_async(
                stream_state["message_id"],
                response.reasoning or stream_state["reasoning"],
                response.content or stream_state["content"],
                status="生成完成",
                finished=True,
                include_reasoning=self._stream_include_reasoning,
            )
            if not updated:
                await self._send_agent_response(
                    chat_id,
                    response,
                )
            return response

        if response:
            await self._send_agent_response(chat_id, response)
        return response

    def start(self):
        logger.info("Starting WebSocket connection...")
        logger.info(f"App ID: {self.app_id}")
        logger.info(
            f"SSL Verify: {'DISABLED' if os.getenv('DISABLE_SSL_VERIFY', '').lower() == 'true' else 'ENABLED'}"
        )
        self.ws_client.start()

    async def send_message(self, chat_id: str, text: str):
        await self._send_message_async(chat_id, text)

    async def _send_message_async(self, chat_id: str, text: str):
        try:
            request = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(json.dumps({"text": text}, ensure_ascii=False))
                    .build()
                )
                .build()
            )

            response = await asyncio.to_thread(
                self.api_client.im.v1.message.create, request
            )

            if not response.success():
                logger.error(
                    f"Failed to send message: code={response.code}, "
                    f"msg={response.msg}, log_id={response.get_log_id()}"
                )
        except Exception as e:
            logger.error(f"Error sending message: {e}")

    def _truncate_card_text(self, text: str, max_len: int | None = None) -> str:
        limit = max_len or self._stream_card_max_chars
        if not text:
            return ""
        if len(text) <= limit:
            return text
        return text[: limit - 20] + "\n...（已截断）"

    def _build_interactive_card_content(
        self,
        reasoning: str,
        content: str,
        status: str,
        finished: bool,
        include_header: bool = True,
        include_reasoning: bool = False,
    ) -> str:
        safe_reasoning = self._truncate_card_text(reasoning)
        safe_content = self._truncate_card_text(content)

        sections = [
            f"**{status}**  |  {'已完成' if finished else '进行中'}",
            "",
        ]

        if include_reasoning and safe_reasoning:
            sections.extend(["### 思考过程", safe_reasoning, ""])

        sections.extend(["### 回复内容", safe_content or "（正在生成...）"])

        card_text = "\n".join(sections)

        card = {
            "schema": "2.0",
            "config": {
                "wide_screen_mode": True,
            },
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "content": card_text,
                    }
                ]
            },
        }

        if include_header:
            card["header"] = {
                "title": {
                    "tag": "plain_text",
                    "content": "猪猪",
                },
                "template": "blue",
            }

        return json.dumps(card, ensure_ascii=False)

    def _interactive_card_payloads(
        self,
        reasoning: str,
        content: str,
        status: str,
        finished: bool,
        include_reasoning: bool,
    ) -> list[str]:
        return [
            self._build_interactive_card_content(
                reasoning,
                content,
                status,
                finished,
                include_header=True,
                include_reasoning=include_reasoning,
            ),
            self._build_interactive_card_content(
                reasoning,
                content,
                status,
                finished,
                include_header=False,
                include_reasoning=include_reasoning,
            ),
        ]

    def _extract_message_id(self, response) -> str | None:
        if not response:
            return None

        data = getattr(response, "data", None)
        if isinstance(data, dict):
            return data.get("message_id") or data.get("messageId")

        if data is not None:
            return getattr(data, "message_id", None) or getattr(data, "messageId", None)

        return None

    async def _send_interactive_message_async(
        self,
        chat_id: str,
        reasoning: str,
        content: str,
        status: str,
        finished: bool,
        include_reasoning: bool,
    ) -> str | None:
        last_response = None
        for idx, payload in enumerate(
            self._interactive_card_payloads(
                reasoning,
                content,
                status,
                finished,
                include_reasoning=include_reasoning,
            ),
            start=1,
        ):
            request = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("interactive")
                    .content(payload)
                    .build()
                )
                .build()
            )

            response = await asyncio.to_thread(
                self.api_client.im.v1.message.create, request
            )
            if response.success():
                if idx > 1:
                    logger.info(
                        "Interactive card send fallback succeeded with simpler payload"
                    )
                return self._extract_message_id(response)
            last_response = response

        logger.error(
            f"Failed to send interactive message: code={getattr(last_response, 'code', None)}, "
            f"msg={getattr(last_response, 'msg', None)}, log_id={getattr(last_response, 'get_log_id', lambda: None)() if last_response else None}"
        )
        return None

    async def _update_interactive_message_async(
        self,
        message_id: str,
        reasoning: str,
        content: str,
        status: str,
        finished: bool,
        include_reasoning: bool,
    ) -> bool:
        last_update_response = None
        last_patch_response = None
        for payload in self._interactive_card_payloads(
            reasoning,
            content,
            status,
            finished,
            include_reasoning=include_reasoning,
        ):
            update_request = (
                UpdateMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    UpdateMessageRequestBodyBuilder()
                    .msg_type("interactive")
                    .content(payload)
                    .build()
                )
                .build()
            )

            response = await asyncio.to_thread(
                self.api_client.im.v1.message.update, update_request
            )
            last_update_response = response
            if response.success():
                return True

            patch_request = (
                PatchMessageRequest.builder()
                .message_id(message_id)
                .request_body(PatchMessageRequestBodyBuilder().content(payload).build())
                .build()
            )

            patch_response = await asyncio.to_thread(
                self.api_client.im.v1.message.patch, patch_request
            )
            last_patch_response = patch_response

            if patch_response.success():
                return True

        logger.error(
            "Failed to update interactive message: "
            f"update_code={getattr(last_update_response, 'code', None)}, "
            f"update_msg={getattr(last_update_response, 'msg', None)}, "
            f"patch_code={getattr(last_patch_response, 'code', None)}, "
            f"patch_msg={getattr(last_patch_response, 'msg', None)}"
        )
        return False

    async def send_to_owner(self, text: str):
        chat_id = get_owner_chat_id()
        if chat_id:
            await self.send_message(chat_id, text)
        else:
            logger.warning("Owner chat_id not set yet")

    async def handle_message(self, user_id: str, text: str) -> AgentResponse:
        if get_owner_chat_id() is None:
            save_owner_chat_id(user_id)
        model = get_user_model(self.platform_name, user_id)
        return await self.agent.run(self.platform_name, user_id, text, model)

    def _handle_message_event(self, data: lark.im.v1.P2ImMessageReceiveV1):
        logger.debug("Processing message event...")
        try:
            event = data.event
            message = event.message
            sender = event.sender

            message_id = message.message_id
            if message_id in self._processed_messages:
                logger.debug(f"Skipping duplicate message: {message_id}")
                return

            self._processed_messages[message_id] = True
            if len(self._processed_messages) > self._max_cache_size:
                self._processed_messages.popitem(last=False)
                logger.debug("Cache full, removed oldest message ID")

            if sender.sender_type == "app":
                logger.debug("Skipping bot's own message")
                return

            if hasattr(message, "chat_type") and message.chat_type == "group":
                if not self._is_bot_mentioned(message):
                    logger.debug("Skipping group message without @bot mention")
                    return
                logger.info("Processing group message with @bot mention")

            user_id = sender.sender_id.open_id if sender.sender_id else "unknown"
            chat_id = message.chat_id

            if get_owner_chat_id() is None:
                save_owner_chat_id(chat_id)

            msg_type = message.message_type

            if msg_type == "text":
                self._queue_message(msg_type, message, chat_id, user_id, message_id)
            elif msg_type == "image":
                self._handle_image_message_sync(message, chat_id, message_id)
            elif msg_type == "file":
                self._queue_message(msg_type, message, chat_id, user_id, message_id)
            else:
                logger.debug(f"Unsupported message type: {msg_type}")

        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)

    def _queue_message(
        self, msg_type: str, message, chat_id: str, user_id: str, message_id: str
    ):
        if not self._event_loop:
            logger.error("Event loop not set, cannot queue message")
            return

        async def enqueue():
            queue = await self._get_or_create_user_queue(user_id)
            msg = FeishuMessage(
                msg_type=msg_type,
                message=message,
                chat_id=chat_id,
                user_id=user_id,
                message_id=message_id,
            )
            await queue.put(msg)
            logger.debug(f"[Feishu] queued {msg_type} message for user {user_id}")

        asyncio.run_coroutine_threadsafe(enqueue(), self._event_loop)

    def _handle_image_message_sync(self, message, chat_id: str, message_id: str):
        content = json.loads(message.content)
        image_key = content.get("image_key")

        if not image_key:
            logger.warning("No image_key in message")
            return

        logger.info(f"Processing image: {image_key}")
        image_data, filepath = download_feishu_image(self.api_client, image_key)

        if image_data is None:
            self._send_message_sync(chat_id, "图片下载失败，请稍后重试")
            return

        self._pending_media[chat_id] = {
            "type": "image",
            "image_key": image_key,
            "image_data": image_data,
            "filepath": filepath,
        }

        self._send_message_sync(chat_id, "📷 已收到图片！请告诉我你想了解什么？")

    async def _handle_command(self, command: str, chat_id: str, user_id: str):
        parts = command.split()
        cmd = parts[0].lower()

        if cmd == "/new":
            await self._handle_new_session(chat_id, user_id)
        elif cmd == "/sessions":
            await self._handle_list_sessions(chat_id, user_id)
        elif cmd == "/model":
            args = parts[1:] if len(parts) > 1 else []
            await self._handle_model(chat_id, user_id, args)

    async def _handle_new_session(self, chat_id: str, user_id: str):
        try:
            messages = await asyncio.to_thread(
                load_session, self.platform_name, user_id
            )

            if messages:
                prompt = f"""请从以下对话中提取关键信息，包括：
1. 用户提到的重要事实或偏好
2. 讨论的项目或任务
3. 任何需要记住的后续事项

对话内容：
{json.dumps(messages, ensure_ascii=False, indent=2)}

请用简洁的语言列出这些关键信息，每条不超过 20 字。"""

                model = get_user_model(self.platform_name, user_id)
                response = await self.agent.client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                )
                summary = response.choices[0].message.content

                timestamp = datetime.now().strftime("%Y%m%d")
                await asyncio.to_thread(save_memory, f"session_{timestamp}", summary)

            await asyncio.to_thread(archive_session, self.platform_name, user_id)
            await self._send_message_async(
                chat_id, "已创建新会话啦！之前的话题已保存到记忆里～"
            )

        except Exception as e:
            logger.error(f"Error handling /new command: {e}")
            await self._send_message_async(chat_id, "创建新会话时出错了，请稍后再试")

    async def _handle_list_sessions(self, chat_id: str, user_id: str):
        try:
            sessions = await asyncio.to_thread(
                list_sessions, self.platform_name, user_id
            )

            if not sessions:
                await self._send_message_async(chat_id, "还没有历史会话记录哦～")
                return

            lines = ["📁 历史会话："]
            for s in sessions[:10]:
                lines.append(f"- {s['created']}: {s['filename']}")

            await self._send_message_async(chat_id, "\n".join(lines))

        except Exception as e:
            logger.error(f"Error handling /sessions command: {e}")
            await self._send_message_async(chat_id, "获取历史会话时出错了")

    async def _handle_model(self, chat_id: str, user_id: str, args: list[str]):
        try:
            if not args:
                current_model = get_user_model(self.platform_name, user_id)
                lines = [f"当前模型: {current_model}", "", "可用模型:"]
                for i, model in enumerate(AVAILABLE_MODELS, 1):
                    marker = " (当前)" if model == current_model else ""
                    lines.append(f"{i}. {model}{marker}")
                await self._send_message_async(chat_id, "\n".join(lines))
                return

            new_model = args[0]
            if new_model not in AVAILABLE_MODELS:
                await self._send_message_async(
                    chat_id,
                    f"无效的模型: {new_model}\n可用模型: {', '.join(AVAILABLE_MODELS)}",
                )
                return

            save_user_model(self.platform_name, user_id, new_model)
            await self._send_message_async(chat_id, f"已切换到模型: {new_model}")

        except Exception as e:
            logger.error(f"Error handling /model command: {e}")
            await self._send_message_async(chat_id, "切换模型时出错了")

    def _send_message_sync(self, chat_id: str, text: str):
        try:
            request = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(json.dumps({"text": text}, ensure_ascii=False))
                    .build()
                )
                .build()
            )

            response = self.api_client.im.v1.message.create(request)

            if not response.success():
                logger.error(
                    f"Failed to send message: code={response.code}, "
                    f"msg={response.msg}, log_id={response.get_log_id()}"
                )

        except Exception as e:
            logger.error(f"Error sending message: {e}")

    async def _send_agent_response(self, chat_id: str, response: AgentResponse):
        if response.reasoning:
            formatted_text = f"""💭 思考过程：
{response.reasoning}

━━━━━━━━━━

{response.content}"""
        else:
            formatted_text = response.content

        await self._send_message_async(chat_id, formatted_text)

    def _add_message_reaction(self, message_id: str, emoji_type: str) -> bool:
        try:
            request = (
                CreateMessageReactionRequest.builder()
                .message_id(message_id)
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                    .build()
                )
                .build()
            )

            response = self.api_client.im.v1.message_reaction.create(request)

            if response.success():
                logger.info(f"Reaction {emoji_type} added to message {message_id}")
                return True
            else:
                logger.error(
                    f"Failed to add reaction: code={response.code}, msg={response.msg}"
                )
                return False

        except Exception as e:
            logger.error(f"Error adding reaction: {e}")
            return False

    def _remove_message_reaction(self, message_id: str, reaction_type: str) -> bool:
        try:
            list_request = (
                ListMessageReactionRequest.builder()
                .message_id(message_id)
                .reaction_type(reaction_type)
                .page_size(100)
                .build()
            )

            list_response = self.api_client.im.v1.message_reaction.list(list_request)

            if not list_response.success():
                logger.error(f"Failed to list reactions: code={list_response.code}")
                return False

            if list_response.data and list_response.data.items:
                for item in list_response.data.items:
                    if item.reaction_id:
                        delete_request = (
                            DeleteMessageReactionRequest.builder()
                            .message_id(message_id)
                            .reaction_id(item.reaction_id)
                            .build()
                        )

                        delete_response = self.api_client.im.v1.message_reaction.delete(
                            delete_request
                        )

                        if delete_response.success():
                            logger.debug(
                                f"Reaction {reaction_type} removed from message {message_id}"
                            )
                            return True

            return False

        except Exception as e:
            logger.error(f"Error removing reaction: {e}")
            return False

    def _is_bot_mentioned(self, message) -> bool:
        if not self.bot_open_id:
            logger.warning("Bot open_id not configured, skipping mention check")
            return False

        if not hasattr(message, "mentions") or not message.mentions:
            return False

        for mention in message.mentions:
            if hasattr(mention, "id") and mention.id == self.bot_open_id:
                return True

        return False

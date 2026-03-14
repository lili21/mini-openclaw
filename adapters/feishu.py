import json
import logging
import os
import ssl
from collections import OrderedDict
from datetime import datetime
from typing import Optional

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

        # 消息处理回调函数（使用官方示例的函数式写法）
        def on_message(data: lark.im.v1.P2ImMessageReceiveV1):
            logger.info(
                f"Received message event: {lark.JSON.marshal(data.event.message.message_id) if data.event and data.event.message else 'unknown'}"
            )
            logger.debug(f"{lark.JSON.marshal(data)}")
            self._handle_message_event(data)

        # 创建事件处理器 - 日志等级设为 DEBUG
        event_handler = (
            lark.EventDispatcherHandler.builder(
                encrypt_key or "",
                verification_token or "",
                lark.LogLevel.DEBUG,
            )
            .register_p2_im_message_receive_v1(on_message)
            .build()
        )

        # 创建 WebSocket 客户端 - 日志等级设为 DEBUG
        self.ws_client = lark.ws.Client(
            app_id=app_id,
            app_secret=app_secret,
            log_level=lark.LogLevel.DEBUG,
            event_handler=event_handler,
            auto_reconnect=True,
        )

        # REST API 客户端 - 日志等级设为 DEBUG
        self.api_client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .log_level(lark.LogLevel.DEBUG)
            .build()
        )
        logger.info(f"Adapter initialized with app_id: {app_id[:10]}...")

    def start(self):
        """启动 WebSocket 连接（阻塞式）"""
        logger.info("Starting WebSocket connection...")
        logger.info(f"App ID: {self.app_id}")
        logger.info(
            f"SSL Verify: {'DISABLED' if os.getenv('DISABLE_SSL_VERIFY', '').lower() == 'true' else 'ENABLED'}"
        )
        self.ws_client.start()

    async def send_message(self, chat_id: str, text: str):
        """发送消息到飞书"""
        logger.debug(f"Sending message to chat_id: {chat_id}")
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

        if response.success():
            logger.debug("Message sent successfully")
        else:
            logger.error(
                f"Failed to send message: code={response.code}, "
                f"msg={response.msg}, log_id={response.get_log_id()}"
            )

    async def send_to_owner(self, text: str):
        """发送消息给主人"""
        chat_id = get_owner_chat_id()
        if chat_id:
            logger.debug(f"Sending message to owner: {chat_id}")
            await self.send_message(chat_id, text)
        else:
            logger.warning("Owner chat_id not set yet")

    async def handle_message(self, user_id: str, text: str) -> AgentResponse:
        """处理消息并返回回复"""
        if get_owner_chat_id() is None:
            logger.info(f"Setting owner to user_id: {user_id}")
            save_owner_chat_id(user_id)
        model = get_user_model(self.platform_name, user_id)
        return self.agent.run(self.platform_name, user_id, text, model)

    def _handle_message_event(self, data: lark.im.v1.P2ImMessageReceiveV1):
        """处理收到的消息事件"""
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
                self._handle_text_message(message, chat_id, user_id)
            elif msg_type == "image":
                self._handle_image_message(message, chat_id)
            elif msg_type == "file":
                self._handle_file_message(message, chat_id)
            else:
                logger.debug(f"Unsupported message type: {msg_type}")

        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)

    def _handle_text_message(self, message, chat_id: str, user_id: str):
        """处理文本消息"""
        content = json.loads(message.content)
        text = content.get("text", "").strip()

        if not text:
            return

        if text.startswith("/"):
            self._handle_command(text, chat_id, user_id)
            return

        pending = self._pending_media.get(chat_id)
        if pending and pending.get("type") == "image":
            image_data = pending.get("image_data")
            self._pending_media.pop(chat_id, None)

            image_url = image_to_base64_url(image_data)
            multimodal_content = [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]
            model = get_user_model(self.platform_name, chat_id)
            response = self.agent.run(
                self.platform_name, chat_id, multimodal_content, model
            )
        else:
            model = get_user_model(self.platform_name, chat_id)
            response = self.agent.run(self.platform_name, chat_id, text, model)

        self._send_agent_response(chat_id, response)

    def _handle_image_message(self, message, chat_id: str):
        """处理图片消息"""
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

    def _handle_file_message(self, message, chat_id: str):
        """处理文件消息"""
        content = json.loads(message.content)
        file_key = content.get("file_key")
        filename = content.get("file_name", "unknown_file")

        if not file_key:
            logger.warning("No file_key in message")
            return

        logger.info(f"Processing file: {filename}")
        filepath, file_size = download_feishu_file(self.api_client, file_key, filename)

        if filepath is None:
            if file_size and file_size > 0:
                size_mb = file_size / (1024 * 1024)
                self._send_message_sync(
                    chat_id, f"文件太大了 ({size_mb:.1f}MB)，超过 10MB 限制，无法处理"
                )
            else:
                self._send_message_sync(chat_id, "文件下载失败，请稍后重试")
            return

        self._send_message_sync(chat_id, f"📁 已收到文件 {filename}，正在解析...")

        parsed_content = parse_file_content(filepath)
        if parsed_content is None:
            self._send_message_sync(chat_id, "文件解析失败，不支持的格式")
            return

        prompt = f"""用户上传了一个文件「{filename}」，以下是文件内容：

{parsed_content}

请总结这个文件的主要内容。"""

        model = get_user_model(self.platform_name, chat_id)
        response = self.agent.run(self.platform_name, chat_id, prompt, model)
        self._send_agent_response(chat_id, response)

    def _handle_command(self, command: str, chat_id: str, user_id: str):
        """处理命令"""
        parts = command.split()
        cmd = parts[0].lower()

        if cmd == "/new":
            self._handle_new_session(chat_id, user_id)
        elif cmd == "/sessions":
            self._handle_list_sessions(chat_id, user_id)
        elif cmd == "/model":
            args = parts[1:] if len(parts) > 1 else []
            self._handle_model(chat_id, user_id, args)

    def _handle_new_session(self, chat_id: str, user_id: str):
        """处理 /new 命令"""
        try:
            messages = load_session(self.platform_name, user_id)

            if messages:
                prompt = f"""请从以下对话中提取关键信息，包括：
1. 用户提到的重要事实或偏好
2. 讨论的项目或任务
3. 任何需要记住的后续事项

对话内容：
{json.dumps(messages, ensure_ascii=False, indent=2)}

请用简洁的语言列出这些关键信息，每条不超过 20 字。"""

                model = get_user_model(self.platform_name, user_id)
                summary = (
                    self.agent.client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    .choices[0]
                    .message.content
                )

                timestamp = datetime.now().strftime("%Y%m%d")
                save_memory(f"session_{timestamp}", summary)

            archive_session(self.platform_name, user_id)
            self._send_message_sync(
                chat_id, "已创建新会话啦！之前的话题已保存到记忆里～"
            )

        except Exception as e:
            logger.error(f"Error handling /new command: {e}")
            self._send_message_sync(chat_id, "创建新会话时出错了，请稍后再试")

    def _handle_list_sessions(self, chat_id: str, user_id: str):
        """处理 /sessions 命令"""
        try:
            sessions = list_sessions(self.platform_name, user_id)

            if not sessions:
                self._send_message_sync(chat_id, "还没有历史会话记录哦～")
                return

            lines = ["📁 历史会话："]
            for s in sessions[:10]:
                lines.append(f"- {s['created']}: {s['filename']}")

            self._send_message_sync(chat_id, "\n".join(lines))

        except Exception as e:
            logger.error(f"Error handling /sessions command: {e}")
            self._send_message_sync(chat_id, "获取历史会话时出错了")

    def _handle_model(self, chat_id: str, user_id: str, args: list[str]):
        """处理 /model 命令"""
        try:
            if not args:
                current_model = get_user_model(self.platform_name, user_id)
                lines = [f"当前模型: {current_model}", "", "可用模型:"]
                for i, model in enumerate(AVAILABLE_MODELS, 1):
                    marker = " (当前)" if model == current_model else ""
                    lines.append(f"{i}. {model}{marker}")
                self._send_message_sync(chat_id, "\n".join(lines))
                return

            new_model = args[0]
            if new_model not in AVAILABLE_MODELS:
                self._send_message_sync(
                    chat_id,
                    f"无效的模型: {new_model}\n可用模型: {', '.join(AVAILABLE_MODELS)}",
                )
                return

            save_user_model(self.platform_name, user_id, new_model)
            self._send_message_sync(chat_id, f"已切换到模型: {new_model}")

        except Exception as e:
            logger.error(f"Error handling /model command: {e}")
            self._send_message_sync(chat_id, "切换模型时出错了")

    def _send_message_sync(self, chat_id: str, text: str):
        """同步发送消息"""
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

    def _send_agent_response(self, chat_id: str, response: AgentResponse):
        """发送 Agent 响应，包含思考过程和最终回复"""
        if response.reasoning:
            formatted_text = f"""💭 思考过程：
{response.reasoning}

━━━━━━━━━━

{response.content}"""
        else:
            formatted_text = response.content

        self._send_message_sync(chat_id, formatted_text)

    def _is_bot_mentioned(self, message) -> bool:
        """检查消息是否 @ 了机器人"""
        if not self.bot_open_id:
            logger.warning("Bot open_id not configured, skipping mention check")
            return False

        if not hasattr(message, "mentions") or not message.mentions:
            return False

        for mention in message.mentions:
            if hasattr(mention, "id") and mention.id == self.bot_open_id:
                return True

        return False

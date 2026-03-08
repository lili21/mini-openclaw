import json
import os
import ssl
from datetime import datetime
from typing import Optional

# SSL 禁用必须在导入 lark_oapi 之前执行
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
from config import get_owner_chat_id, save_owner_chat_id
from storage.session import archive_session, list_sessions, load_session


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

        # 消息处理回调函数（使用官方示例的函数式写法）
        def on_message(data: lark.im.v1.P2ImMessageReceiveV1):
            print(
                f"[Feishu] 📥 Received message event: {lark.JSON.marshal(data.event.message.message_id) if data.event and data.event.message else 'unknown'}"
            )
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
        print(f"[Feishu] Adapter initialized with app_id: {app_id[:10]}...")

    def start(self):
        """启动 WebSocket 连接（阻塞式）"""
        print(f"[Feishu] 🔌 Starting WebSocket connection...")
        print(f"[Feishu] 📋 App ID: {self.app_id}")
        print(
            f"[Feishu] 🔒 SSL Verify: {'DISABLED' if os.getenv('DISABLE_SSL_VERIFY', '').lower() == 'true' else 'ENABLED'}"
        )
        self.ws_client.start()

    async def send_message(self, chat_id: str, text: str):
        """发送消息到飞书"""
        print(f"[Feishu] 📤 Sending message to chat_id: {chat_id}")
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
            print(f"[Feishu] ✅ Message sent successfully")
        else:
            print(
                f"[Feishu] ❌ Failed to send message: code={response.code}, "
                f"msg={response.msg}, log_id={response.get_log_id()}"
            )

    async def send_to_owner(self, text: str):
        """发送消息给主人"""
        chat_id = get_owner_chat_id()
        if chat_id:
            print(f"[Feishu] 📤 Sending message to owner: {chat_id}")
            await self.send_message(chat_id, text)
        else:
            print("[Feishu] ⚠️ Owner chat_id not set yet")

    async def handle_message(self, user_id: str, text: str) -> str:
        """处理消息并返回回复"""
        if get_owner_chat_id() is None:
            print(f"[Feishu] 👤 Setting owner to user_id: {user_id}")
            save_owner_chat_id(user_id)
        return self.agent.run(self.platform_name, user_id, text)

    def _handle_message_event(self, data: lark.im.v1.P2ImMessageReceiveV1):
        """处理收到的消息事件"""
        print(f"[Feishu] 🔄 Processing message event...")
        try:
            event = data.event
            message = event.message
            sender = event.sender

            # 只处理文本消息
            if message.message_type != "text":
                return

            # 解析消息内容
            content = json.loads(message.content)
            text = content.get("text", "").strip()

            if not text:
                return

            # 获取用户信息
            user_id = sender.sender_id.open_id if sender.sender_id else "unknown"
            chat_id = message.chat_id

            # 保存主人 ID
            if get_owner_chat_id() is None:
                save_owner_chat_id(user_id)

            # 处理命令
            if text.startswith("/"):
                self._handle_command(text, chat_id, user_id)
                return

            # 正常对话处理
            response = self.agent.run(self.platform_name, user_id, text)

            # 发送回复
            self._send_message_sync(chat_id, response)

        except Exception as e:
            print(f"Error handling message: {e}")
            import traceback

            traceback.print_exc()

    def _handle_command(self, command: str, chat_id: str, user_id: str):
        """处理命令"""
        cmd = command.split()[0].lower()

        if cmd == "/new":
            self._handle_new_session(chat_id, user_id)
        elif cmd == "/sessions":
            self._handle_list_sessions(chat_id, user_id)

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

                summary = (
                    self.agent.client.chat.completions.create(
                        model=self.agent.model,
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
            print(f"Error handling /new command: {e}")
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
            print(f"Error handling /sessions command: {e}")
            self._send_message_sync(chat_id, "获取历史会话时出错了")

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
                print(
                    f"Failed to send message: code={response.code}, "
                    f"msg={response.msg}, log_id={response.get_log_id()}"
                )

        except Exception as e:
            print(f"Error sending message: {e}")

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, CommandHandler

from adapters.base import BaseAdapter
from config import get_owner_chat_id, save_owner_chat_id
from storage.session import archive_session, list_sessions, load_session
import json

class TelegramAdapter(BaseAdapter):
    platform_name = "telegram"

    def __init__(self, token: str, agent):
        self.token = token
        self.agent = agent
        self.app = None

    def start(self):
        self.app = Application.builder().token(self.token).build()
        self.app.add_handler(CommandHandler("new", self._handle_new))
        self.app.add_handler(CommandHandler("sessions", self._handle_sessions))
        self.app.add_handler(MessageHandler(filters.TEXT, self._handle_update))
        self.app.run_polling()

    async def send_message(self, chat_id: str, text: str):
        if self.app:
            await self.app.bot.send_message(chat_id=int(chat_id), text=text)

    async def send_to_owner(self, text: str):
        chat_id = get_owner_chat_id()
        if chat_id:
            await self.send_message(chat_id, text)

    async def handle_message(self, user_id: str, text: str) -> str:
        if get_owner_chat_id() is None:
            save_owner_chat_id(user_id)
        
        return self.agent.run(self.platform_name, user_id, text)

    async def _handle_new(self, update: Update, context):
        user_id = str(update.effective_user.id)
        
        messages = load_session(self.platform_name, user_id)
        
        if messages:
            prompt = f"""请从以下对话中提取关键信息，包括：
1. 用户提到的重要事实或偏好
2. 讨论的项目或任务
3. 任何需要记住的后续事项

对话内容：
{json.dumps(messages, ensure_ascii=False, indent=2)}

请用简洁的语言列出这些关键信息，每条不超过20字。"""

            summary = self.agent.client.chat.completions.create(
                model=self.agent.model,
                messages=[{"role": "user", "content": prompt}]
            ).choices[0].message.content

            from agent.tools import save_memory
            timestamp = update.message.date.strftime("%Y%m%d")
            save_memory(f"session_{timestamp}", summary)
        
        archive_session(self.platform_name, user_id)
        
        await update.message.reply_text("已创建新会话啦！之前的话题已保存到记忆里～")

    async def _handle_sessions(self, update: Update, context):
        user_id = str(update.effective_user.id)
        
        sessions = list_sessions(self.platform_name, user_id)
        
        if not sessions:
            await update.message.reply_text("还没有历史会话记录哦～")
            return
        
        lines = ["📁 历史会话："]
        for s in sessions[:10]:
            lines.append(f"- {s['created']}: {s['filename']}")
        
        await update.message.reply_text("\n".join(lines))

    async def _handle_update(self, update: Update, context):
        if not update.message or not update.message.text:
            return
        
        user_id = str(update.effective_user.id)
        user_message = update.message.text

        if get_owner_chat_id() is None:
            save_owner_chat_id(user_id)

        response = await self.handle_message(user_id, user_message)
        await update.message.reply_text(response)

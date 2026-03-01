from telegram import Update
from telegram.ext import Application, MessageHandler, filters

from adapters.base import BaseAdapter

class TelegramAdapter(BaseAdapter):
    platform_name = "telegram"

    def __init__(self, token: str, agent):
        self.token = token
        self.agent = agent
        self.app = None

    async def start(self):
        self.app = Application.builder().token(self.token).build()
        self.app.add_handler(MessageHandler(filters.TEXT, self._handle_update))
        await self.app.run_polling()

    async def send_message(self, chat_id: str, text: str):
        if self.app:
            await self.app.bot.send_message(chat_id=int(chat_id), text=text)

    async def handle_message(self, user_id: str, text: str) -> str:
        return await self.agent.run(self.platform_name, user_id, text)

    async def _handle_update(self, update: Update, context):
        if not update.message or not update.message.text:
            return
        
        user_id = str(update.effective_user.id)
        user_message = update.message.text

        response = await self.handle_message(user_id, user_message)
        await update.message.reply_text(response)

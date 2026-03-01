import os
from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update
from telegram.ext import Application, MessageHandler, filters

load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("BASE_URL")
)

async def handle_message(update: Update, context):
    user_message = update.message.text

    response = client.chat.completions.create(
        model="qwen3.5-plus",
        messages=[{"role": "user", "content": user_message}]
    )

    await update.message.reply_text(response.choices[0].message.content)

app = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
app.add_handler(MessageHandler(filters.TEXT, handle_message))
app.run_polling()

import os
import asyncio
from dotenv import load_dotenv
from openai import OpenAI

from agent.core import Agent
from adapters.telegram import TelegramAdapter

load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("BASE_URL")
)

agent = Agent(client)

async def main():
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    
    if telegram_token:
        adapter = TelegramAdapter(telegram_token, agent)
        await adapter.start()
    else:
        print("No adapter configured. Set TELEGRAM_BOT_TOKEN in .env")

if __name__ == "__main__":
    asyncio.run(main())

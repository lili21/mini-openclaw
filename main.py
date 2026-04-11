import asyncio
import logging
import os
import threading
import time

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

if os.getenv("DISABLE_SSL_VERIFY", "").lower() == "true":
    import ssl

    ssl._create_default_https_context = ssl._create_unverified_context
    os.environ["PYTHONHTTPSVERIFY"] = "0"
    logger.warning("SSL verification disabled (development mode only)")

from agent.core import Agent  # noqa: E402
from adapters.telegram import TelegramAdapter  # noqa: E402
from adapters.feishu import FeishuAdapter  # noqa: E402
from scheduler import Scheduler  # noqa: E402

client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY"), base_url=os.getenv("BASE_URL")
)

agent = Agent(client)

telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
feishu_app_id = os.getenv("FEISHU_APP_ID")
feishu_app_secret = os.getenv("FEISHU_APP_SECRET")

adapters = []
schedulers = []
threads = []
event_loop = None


def run_event_loop():
    global event_loop
    event_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(event_loop)
    logger.info("Event loop started in background thread")
    event_loop.run_forever()


if telegram_token:
    logger.info("Initializing Telegram adapter...")
    tg_adapter = TelegramAdapter(telegram_token, agent)
    adapters.append(("Telegram", tg_adapter))

if feishu_app_id and feishu_app_secret:
    logger.info("Initializing Feishu adapter...")
    feishu_adapter = FeishuAdapter(
        app_id=feishu_app_id,
        app_secret=feishu_app_secret,
        agent=agent,
    )
    adapters.append(("Feishu", feishu_adapter))

if not adapters:
    logger.error(
        "No adapter configured. Set TELEGRAM_BOT_TOKEN or FEISHU_APP_ID/FEISHU_APP_SECRET in .env"
    )
    exit(1)

loop_thread = threading.Thread(
    target=run_event_loop, name="EventLoopThread", daemon=True
)
loop_thread.start()

while event_loop is None:
    time.sleep(0.1)

logger.info(f"Event loop ready: {event_loop}")

for name, adapter in adapters:
    if hasattr(adapter, "set_event_loop"):
        adapter.set_event_loop(event_loop)
        logger.info(f"Set event loop for {name} adapter")

# for name, adapter in adapters:
#     logger.info(f"Starting scheduler for {name}...")
#     scheduler = Scheduler(adapter, agent)
#     schedulers.append(scheduler)
#     scheduler.start()

for name, adapter in adapters:
    logger.info(f"Starting {name} adapter...")
    t = threading.Thread(target=adapter.start, name=f"{name}Adapter", daemon=True)
    t.start()
    threads.append((name, t))

logger.info(f"All adapters started: {[name for name, _ in adapters]}")
logger.info("Press Ctrl+C to stop")

try:
    while True:
        for name, t in threads:
            if not t.is_alive():
                logger.warning(f"{name} adapter thread died, restarting...")
                for adapter_name, adapter in adapters:
                    if adapter_name == name:
                        new_t = threading.Thread(
                            target=adapter.start, name=f"{name}Adapter", daemon=True
                        )
                        new_t.start()
                        threads = [(n, new_t if n == name else tt) for n, tt in threads]
                        break
        time.sleep(5)
except KeyboardInterrupt:
    logger.info("Shutting down...")
    for scheduler in schedulers:
        scheduler.stop()
    if event_loop:
        event_loop.call_soon_threadsafe(event_loop.stop)
    logger.info("Goodbye!")

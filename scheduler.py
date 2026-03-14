import logging
import schedule
import time
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adapters.telegram import TelegramAdapter
    from agent.core import Agent

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, adapter: "TelegramAdapter", agent: "Agent"):
        self.adapter = adapter
        self.agent = agent
        self.running = False
        self.thread = None

    def start(self):

        schedule.every().day.at("09:00").do(self._run_news_job)
        schedule.every().friday.at("18:00").do(self._run_weekly_job)

        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("Scheduler started: 每天9点新闻，每周5的18点周报")

    def stop(self):
        self.running = False

    def _run(self):
        while self.running:
            schedule.run_pending()
            time.sleep(60)

    def _run_news_job(self):
        from jobs.news import NewsJob

        logger.info("Running daily news job...")
        job = NewsJob()
        message = job.run(self.agent)

        import asyncio

        asyncio.run(self.adapter.send_to_owner(message))

    def _run_weekly_job(self):
        from jobs.weekly import WeeklyJob

        logger.info("Running weekly job...")
        job = WeeklyJob()
        message = job.run(self.agent)

        import asyncio

        asyncio.run(self.adapter.send_to_owner(message))

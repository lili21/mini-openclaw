import asyncio
import json
import logging
from dataclasses import dataclass

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, CommandHandler

from agent.core import AgentResponse
from adapters.base import BaseAdapter
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
class UserMessage:
    update: Update
    user_id: str
    text: str


class TelegramAdapter(BaseAdapter):
    platform_name = "telegram"

    def __init__(self, token: str, agent):
        self.token = token
        self.agent = agent
        self.app = None
        self._user_queues: dict[str, asyncio.Queue] = {}
        self._user_workers: dict[str, asyncio.Task] = {}

    def start(self):
        self.app = Application.builder().token(self.token).build()
        self.app.add_handler(CommandHandler("new", self._handle_new))
        self.app.add_handler(CommandHandler("sessions", self._handle_sessions))
        self.app.add_handler(CommandHandler("model", self._handle_model))
        self.app.add_handler(MessageHandler(filters.TEXT, self._handle_update))
        self.app.run_polling()

    async def _get_or_create_user_queue(self, user_id: str) -> asyncio.Queue:
        if user_id not in self._user_queues:
            self._user_queues[user_id] = asyncio.Queue()
            worker = asyncio.create_task(self._user_worker(user_id))
            self._user_workers[user_id] = worker
            logger.info(f"[Telegram] created queue and worker for user {user_id}")
        return self._user_queues[user_id]

    async def _user_worker(self, user_id: str):
        queue = self._user_queues[user_id]
        logger.info(f"[Telegram] worker started for user {user_id}")
        while True:
            try:
                msg: UserMessage = await queue.get()
                try:
                    await self._process_message(msg)
                except Exception as e:
                    logger.error(
                        f"[Telegram] error processing message for user {user_id}: {e}"
                    )
                finally:
                    queue.task_done()
            except asyncio.CancelledError:
                logger.info(f"[Telegram] worker cancelled for user {user_id}")
                break
            except Exception as e:
                logger.error(f"[Telegram] worker error for user {user_id}: {e}")

    async def _process_message(self, msg: UserMessage):
        update = msg.update
        user_id = msg.user_id
        user_message = msg.text

        if get_owner_chat_id() is None:
            save_owner_chat_id(user_id)

        needs_thinking = await self.agent.check_intent(user_message)

        try:
            if needs_thinking:
                await update.message.set_reaction("🧠")
            else:
                await update.message.set_reaction("👀")
        except Exception:
            pass

        try:
            model = get_user_model(self.platform_name, user_id)
            response = await self.agent.run(
                self.platform_name, user_id, user_message, model, needs_thinking
            )

            if response.reasoning:
                formatted_text = f"""💭 思考过程：
{response.reasoning}

━━━━━━━━━━

{response.content}"""
            else:
                formatted_text = response.content

            await update.message.reply_text(formatted_text)
        finally:
            try:
                await update.message.set_reaction(reaction=None)
            except Exception:
                pass

    async def send_message(self, chat_id: str, text: str):
        if self.app:
            await self.app.bot.send_message(chat_id=int(chat_id), text=text)

    async def send_to_owner(self, text: str):
        chat_id = get_owner_chat_id()
        if chat_id:
            await self.send_message(chat_id, text)

    async def handle_message(self, user_id: str, text: str) -> AgentResponse:
        if get_owner_chat_id() is None:
            save_owner_chat_id(user_id)

        model = get_user_model(self.platform_name, user_id)
        return await self.agent.run(self.platform_name, user_id, text, model)

    async def _handle_new(self, update: Update, context):
        user_id = str(update.effective_user.id)

        messages = await asyncio.to_thread(load_session, self.platform_name, user_id)

        if messages:
            prompt = f"""请从以下对话中提取关键信息，包括：
1. 用户提到的重要事实或偏好
2. 讨论的项目或任务
3. 任何需要记住的后续事项

对话内容：
{json.dumps(messages, ensure_ascii=False, indent=2)}

请用简洁的语言列出这些关键信息，每条不超过20字。"""

            model = get_user_model(self.platform_name, user_id)
            response = await self.agent.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
            )
            summary = response.choices[0].message.content

            from agent.tools import save_memory

            timestamp = update.message.date.strftime("%Y%m%d")
            await asyncio.to_thread(save_memory, f"session_{timestamp}", summary)

        await asyncio.to_thread(archive_session, self.platform_name, user_id)

        await update.message.reply_text("已创建新会话啦！之前的话题已保存到记忆里～")

    async def _handle_sessions(self, update: Update, context):
        user_id = str(update.effective_user.id)

        sessions = await asyncio.to_thread(list_sessions, self.platform_name, user_id)

        if not sessions:
            await update.message.reply_text("还没有历史会话记录哦～")
            return

        lines = ["📁 历史会话："]
        for s in sessions[:10]:
            lines.append(f"- {s['created']}: {s['filename']}")

        await update.message.reply_text("\n".join(lines))

    async def _handle_model(self, update: Update, context):
        user_id = str(update.effective_user.id)
        args = context.args

        if not args:
            current_model = get_user_model(self.platform_name, user_id)
            lines = [f"当前模型: {current_model}", "", "可用模型:"]
            for i, model in enumerate(AVAILABLE_MODELS, 1):
                marker = " (当前)" if model == current_model else ""
                lines.append(f"{i}. {model}{marker}")
            await update.message.reply_text("\n".join(lines))
            return

        new_model = args[0]
        if new_model not in AVAILABLE_MODELS:
            await update.message.reply_text(
                f"无效的模型: {new_model}\n可用模型: {', '.join(AVAILABLE_MODELS)}"
            )
            return

        save_user_model(self.platform_name, user_id, new_model)
        await update.message.reply_text(f"已切换到模型: {new_model}")

    async def _handle_update(self, update: Update, context):
        if not update.message or not update.message.text:
            return

        user_id = str(update.effective_user.id)
        user_message = update.message.text

        queue = await self._get_or_create_user_queue(user_id)
        msg = UserMessage(update=update, user_id=user_id, text=user_message)
        await queue.put(msg)
        logger.debug(f"[Telegram] queued message for user {user_id}")

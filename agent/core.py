import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from openai import AsyncOpenAI

from agent.intent import should_enable_thinking
from agent.prompt import get_system_prompt
from agent.tools import TOOLS_SCHEMA, TOOL_FUNCTIONS
from config import THINKING_MODELS
from storage.session import load_session, append_to_session, compress_session

logger = logging.getLogger(__name__)


@dataclass
class AgentResponse:
    content: str
    reasoning: str | None = None
    needs_thinking: bool = False


class Agent:
    def __init__(self, client: AsyncOpenAI, model: str = "qwen3.5-plus"):
        self.client = client
        self.model = model
        self.max_iterations = 20

    async def check_intent(self, content: str | list[dict]) -> bool:
        """
        检查用户消息意图，判断是否需要深度思考

        Args:
            content: 用户消息内容

        Returns:
            True 表示需要深度思考，False 表示不需要
        """
        return await should_enable_thinking(content, self.client)

    async def run(
        self,
        platform: str,
        user_id: str,
        content: str | list[dict],
        model: str | None = None,
        needs_thinking: bool | None = None,
    ) -> AgentResponse:
        """
        生成回复

        Args:
            platform: 平台名称
            user_id: 用户ID
            content: 用户消息内容
            model: 指定模型（可选）
            needs_thinking: 是否需要深度思考（可选，如果不提供则自动判断）

        Returns:
            AgentResponse 包含回复内容和思考过程
        """
        actual_model = model or self.model
        content_preview = (
            content[:50]
            if isinstance(content, str)
            else f"[多模态消息，{len(content)}个元素]"
        )
        logger.info(
            f"[Agent] start run - platform={platform}, user_id={user_id}, model={actual_model}, message={content_preview}..."
        )

        # 如果没有提供 needs_thinking，则自动判断
        if needs_thinking is None:
            needs_thinking = await should_enable_thinking(content, self.client)

        logger.info(f"[Agent] intent judgment: needs_thinking={needs_thinking}")

        messages = load_session(platform, user_id)
        full_messages = [{"role": "system", "content": get_system_prompt()}] + messages

        # 追加当前日期时间
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(content, str):
            content_with_time = f"{content}\n\n[当前时间: {current_time}]"
        elif isinstance(content, list):
            content_with_time = content + [
                {"type": "text", "text": f"\n\n[当前时间: {current_time}]"}
            ]
        else:
            content_with_time = content

        user_msg = {"role": "user", "content": content_with_time}
        user_msg_for_session = {"role": "user", "content": content}
        full_messages.append(user_msg)

        # 准备调用参数
        extra_kwargs: dict[str, Any] = {}
        if needs_thinking and actual_model in THINKING_MODELS:
            extra_kwargs["extra_body"] = {"enable_thinking": True}
            logger.info(f"[Agent] deep thinking enabled for model: {actual_model}")

        for i in range(self.max_iterations):
            logger.info(f"[Agent] iteration {i + 1}/{self.max_iterations}")
            response = await self.client.chat.completions.create(
                model=actual_model,
                messages=full_messages,
                tools=TOOLS_SCHEMA,
                **extra_kwargs,
            )

            choice = response.choices[0]
            message = choice.message

            if message.tool_calls:
                logger.info(
                    f"[Agent] tool_calls: {[tc.function.name for tc in message.tool_calls]}"
                )
                full_messages.append(message.model_dump())

                for tool_call in message.tool_calls:
                    func_name = tool_call.function.name
                    func_args = json.loads(tool_call.function.arguments)

                    logger.info(
                        f"[Agent] executing tool: {func_name}, args={func_args}"
                    )
                    func: Callable = TOOL_FUNCTIONS[func_name]
                    result = await asyncio.to_thread(func, **func_args)

                    full_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result,
                        }
                    )
            else:
                assistant_content = message.content or "（无回复内容）"
                reasoning_content = getattr(message, "reasoning_content", None)

                logger.info(f"[Agent] final response: {assistant_content[:100]}...")
                if reasoning_content:
                    logger.info(f"[Agent] reasoning: {reasoning_content[:100]}...")

                await asyncio.to_thread(
                    append_to_session, platform, user_id, user_msg_for_session
                )
                await asyncio.to_thread(
                    append_to_session,
                    platform,
                    user_id,
                    {"role": "assistant", "content": assistant_content},
                )

                asyncio.create_task(
                    compress_session(platform, user_id, self.client, actual_model)
                )

                logger.info(
                    f"[Agent] run complete - platform={platform}, user_id={user_id}"
                )
                return AgentResponse(
                    content=assistant_content,
                    reasoning=reasoning_content,
                    needs_thinking=needs_thinking,
                )

        logger.warning(
            f"[Agent] max iterations reached - platform={platform}, user_id={user_id}"
        )
        return AgentResponse(content="已达到最大迭代次数")

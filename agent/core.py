import json
import logging
from openai import OpenAI

from agent.prompt import get_system_prompt
from agent.tools import TOOLS_SCHEMA, TOOL_FUNCTIONS
from storage.session import load_session, append_to_session, compress_session

logger = logging.getLogger(__name__)


class Agent:
    def __init__(self, client: OpenAI, model: str = "qwen3.5-plus"):
        self.client = client
        self.model = model
        self.max_iterations = 10

    def run(self, platform: str, user_id: str, user_message: str) -> str:
        logger.info(
            f"[Agent] start run - platform={platform}, user_id={user_id}, message={user_message[:50]}..."
        )
        messages = load_session(platform, user_id)
        full_messages = [{"role": "system", "content": get_system_prompt()}] + messages

        user_msg = {"role": "user", "content": user_message}
        full_messages.append(user_msg)

        for i in range(self.max_iterations):
            logger.info(f"[Agent] iteration {i + 1}/{self.max_iterations}")
            response = self.client.chat.completions.create(
                model=self.model, messages=full_messages, tools=TOOLS_SCHEMA
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
                    result = TOOL_FUNCTIONS[func_name](**func_args)

                    full_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result,
                        }
                    )
            else:
                assistant_content = message.content
                logger.info(f"[Agent] final response: {assistant_content[:100]}...")

                # 只保存当前对话轮次的新消息（user 和 assistant），不保存历史消息
                append_to_session(platform, user_id, user_msg)
                append_to_session(
                    platform,
                    user_id,
                    {"role": "assistant", "content": assistant_content},
                )

                compress_session(platform, user_id, self.client, self.model)
                logger.info(
                    f"[Agent] run complete - platform={platform}, user_id={user_id}"
                )
                return assistant_content

        logger.warning(
            f"[Agent] max iterations reached - platform={platform}, user_id={user_id}"
        )
        return "已达到最大迭代次数"

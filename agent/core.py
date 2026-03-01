import json
from openai import OpenAI

from agent.prompt import SYSTEM_PROMPT
from agent.tools import TOOLS_SCHEMA, TOOL_FUNCTIONS
from storage.session import load_session, append_to_session

class Agent:
    def __init__(self, client: OpenAI, model: str = "qwen3.5-plus"):
        self.client = client
        self.model = model
        self.max_iterations = 10

    async def run(self, platform: str, user_id: str, user_message: str) -> str:
        messages = load_session(platform, user_id)
        full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

        user_msg = {"role": "user", "content": user_message}
        full_messages.append(user_msg)

        for _ in range(self.max_iterations):
            response = self.client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                tools=TOOLS_SCHEMA
            )

            choice = response.choices[0]
            message = choice.message

            if message.tool_calls:
                full_messages.append(message)

                for tool_call in message.tool_calls:
                    func_name = tool_call.function.name
                    func_args = json.loads(tool_call.function.arguments)

                    result = TOOL_FUNCTIONS[func_name](**func_args)

                    full_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result
                    })
            else:
                assistant_content = message.content
                full_messages.append({"role": "assistant", "content": assistant_content})

                for msg in full_messages:
                    if msg.get("role") in ["user", "assistant"]:
                        append_to_session(platform, user_id, msg)

                return assistant_content

        return "已达到最大迭代次数"

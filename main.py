import os
import re
import json
import subprocess
from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update
from telegram.ext import Application, MessageHandler, filters

load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("BASE_URL")
)

SESSIONS_DIR = os.path.expanduser("~/.mini-openclaw/sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)

SYSTEM_PROMPT = """你是猪猪，一个20岁刚毕业的日语专业大学生。虽然年轻，但很能干，学习能力强，态度积极。你说话亲切、有活力，偶尔会用一些年轻人的表达方式。

你有一个工具箱，可以帮助用户完成各种任务。当需要执行操作时，使用工具来完成任务。

可用的工具：
- run_command: 执行终端命令
- read_file: 读取文件内容
- write_file: 写入文件内容
- web_search: 搜索网页内容

只有在真正需要使用工具时才调用工具，不需要时可以直接回复用户。"""

DANGEROUS_PATTERNS = [
    r"\brm\b.*-rf",
    r"\bsudo\b",
    r"\bchmod\b",
    r"curl.*\|.*sh",
    r"wget.*\|.*sh",
    r"\bkill\b",
    r"\bmkfs\b",
    r"\bdd\b.*of=",
]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "执行终端命令并返回输出结果",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入内容到文件（覆盖已有内容）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "要写入的内容"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "搜索网页内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"}
                },
                "required": ["query"]
            }
        }
    }
]

def is_command_safe(command: str) -> bool:
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return False
    return True

def run_command(command: str) -> str:
    if not is_command_safe(command):
        return f"危险命令被拒绝: {command}"
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        output = result.stdout or result.stderr or "(命令执行完成，无输出)"
        return output[:5000]
    except Exception as e:
        return f"命令执行失败: {str(e)}"

def read_file(path: str) -> str:
    if ".." in path or path.startswith("/"):
        return f"路径不安全: {path}"
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            return content[:10000]
    except FileNotFoundError:
        return f"文件不存在: {path}"
    except Exception as e:
        return f"读取失败: {str(e)}"

def write_file(path: str, content: str) -> str:
    if ".." in path or path.startswith("/"):
        return f"路径不安全: {path}"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"写入成功: {path}"
    except Exception as e:
        return f"写入失败: {str(e)}"

def web_search(query: str) -> str:
    return "暂未实现"

TOOL_FUNCTIONS = {
    "run_command": run_command,
    "read_file": read_file,
    "write_file": write_file,
    "web_search": web_search,
}

def get_session_path(user_id):
    return os.path.join(SESSIONS_DIR, f"{user_id}.jsonl")

def load_session(user_id):
    path = get_session_path(user_id)
    messages = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    messages.append(json.loads(line))
    return messages

def append_to_session(user_id, message):
    path = get_session_path(user_id)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(message, ensure_ascii=False) + "\n")

def save_session(user_id, messages):
    path = get_session_path(user_id)
    with open(path, "w", encoding="utf-8") as f:
        for message in messages:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")

async def handle_message(update: Update, context):
    user_id = str(update.effective_user.id)
    user_message = update.message.text

    messages = load_session(user_id)
    full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    user_msg = {"role": "user", "content": user_message}
    full_messages.append(user_msg)

    max_iterations = 10
    for _ in range(max_iterations):
        response = client.chat.completions.create(
            model="qwen3.5-plus",
            messages=full_messages,
            tools=TOOLS
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
                    append_to_session(user_id, msg)

            await update.message.reply_text(assistant_content)
            return

    await update.message.reply_text("已达到最大迭代次数")

app = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
app.add_handler(MessageHandler(filters.TEXT, handle_message))
app.run_polling()

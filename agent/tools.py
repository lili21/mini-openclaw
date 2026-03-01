import os
import re
import json
import subprocess
from typing import Any, Callable

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
            command, shell=True, capture_output=True, text=True, timeout=30
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
    try:
        import os
        from dotenv import load_dotenv

        load_dotenv()

        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key:
            return "未配置 TAVILY_API_KEY"

        import requests

        response = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": api_key, "query": query, "max_results": 5},
            timeout=15,
        )
        result = response.json()

        if result and "results" in result:
            items = []
            for i, item in enumerate(result["results"][:5], 1):
                title = item.get("title", "")
                content = item.get("content", "")[:200]
                if title:
                    items.append(f"{i}. {title}\n   {content}...")
            return "\n".join(items) if items else "未找到相关内容"
        return "未找到相关内容"
    except Exception as e:
        return f"搜索失败: {str(e)}"


MEMORY_DIR = os.path.expanduser("~/.mini-openclaw/memory")
os.makedirs(MEMORY_DIR, exist_ok=True)


def save_memory(key: str, content: str) -> str:
    safe_key = re.sub(r"[^\w\-_.]", "_", key)
    path = os.path.join(MEMORY_DIR, f"{safe_key}.md")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"记忆已保存: {key}"
    except Exception as e:
        return f"保存失败: {str(e)}"


def search_memory(query: str) -> str:
    try:
        results = []
        for filename in os.listdir(MEMORY_DIR):
            if filename.endswith(".md"):
                path = os.path.join(MEMORY_DIR, filename)
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                    if query.lower() in content.lower():
                        results.append(f"## {filename[:-3]}\n{content[:500]}")
        if results:
            return "\n\n".join(results)
        return "未找到相关记忆"
    except Exception as e:
        return f"搜索失败: {str(e)}"


def load_skill(name: str) -> str:
    try:
        from agent.skills.loader import get_skill

        skill = get_skill(name)
        if not skill:
            return f"技能不存在: {name}"
        if not skill.content:
            return f"无法加载技能内容: {name}"
        return f"# {skill.name}\n\n{skill.content}"
    except Exception as e:
        return f"加载技能失败: {str(e)}"


TOOL_FUNCTIONS: dict[str, Callable[..., str]] = {
    "run_command": run_command,
    "read_file": read_file,
    "write_file": write_file,
    "web_search": web_search,
    "save_memory": save_memory,
    "search_memory": search_memory,
    "load_skill": load_skill,
}

TOOLS_SCHEMA = [
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
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件内容",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "文件路径"}},
                "required": ["path"],
            },
        },
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
                    "content": {"type": "string", "description": "要写入的内容"},
                },
                "required": ["path", "content"],
            },
        },
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
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "保存重要信息到长期记忆（如用户偏好、关键事实、项目详情）",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "记忆的标识符/标题"},
                    "content": {"type": "string", "description": "要保存的具体内容"},
                },
                "required": ["key", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "从长期记忆中搜索相关内容，用于回忆之前会话的上下文",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": "加载技能指令，当需要使用某个技能时先调用此工具获取技能的详细指令",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "技能名称（如 news, weekly）",
                    }
                },
                "required": ["name"],
            },
        },
    },
]

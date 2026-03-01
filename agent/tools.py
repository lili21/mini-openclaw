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

TOOL_FUNCTIONS: dict[str, Callable[..., str]] = {
    "run_command": run_command,
    "read_file": read_file,
    "write_file": write_file,
    "web_search": web_search,
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

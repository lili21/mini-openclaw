# AGENTS.md - AI Agent Guidelines

This document provides guidelines for AI coding agents working in this codebase.

## Project Overview

mini-openclaw is a Python-based chatbot agent that integrates with Telegram and Feishu (飞书) messaging platforms. It uses OpenAI-compatible APIs for LLM functionality and provides tools for command execution, file operations, web search, and memory management.

## Build/Lint/Test Commands

### Package Management
This project uses `uv` for dependency management.

```bash
# Install dependencies
uv sync

# Add a new dependency
uv add <package-name>

# Run the application
uv run python main.py
```

### Linting
The project uses Ruff for linting and formatting.

```bash
# Run linting
uv run ruff check .

# Run linting with auto-fix
uv run ruff check . --fix

# Format code
uv run ruff format .

# Check formatting without changes
uv run ruff format . --check
```

### Testing
No test framework is currently configured. When adding tests, use pytest:

```bash
# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_example.py

# Run a single test function
uv run pytest tests/test_example.py::test_function_name -v
```

## Code Style Guidelines

### Imports
Organize imports in the following order, separated by blank lines:
1. Standard library imports
2. Third-party imports
3. Local imports

```python
import os
import json
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI

from agent.core import Agent
from adapters.base import BaseAdapter
```

### Formatting
- Use 4 spaces for indentation (no tabs)
- Maximum line length: 88 characters (Ruff default)
- Use double quotes for strings
- Use f-strings for string formatting

### Type Hints
- Use Python 3.11+ type hint syntax (e.g., `list[dict]`, `dict[str, str]`)
- Use `Optional[str]` for optional return types
- Use type hints for function parameters and return types

```python
def load_session(platform: str, user_id: str) -> list[dict]:
    ...

def get_skill(name: str) -> Optional[Skill]:
    ...
```

### Naming Conventions
- Functions and variables: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Private methods: prefix with underscore `_private_method`
- Module-level "private" variables: prefix with underscore `_VARIABLE`

```python
MAX_RECENT_MESSAGES = 10

class TelegramAdapter(BaseAdapter):
    platform_name = "telegram"
    
    def _handle_message_event(self, data):
        ...

def compress_session(platform: str, user_id: str, client, model: str):
    ...
```

### Classes
- Use dataclasses for simple data containers
- Use ABC (Abstract Base Classes) for interfaces
- Define class attributes at the class level for constants

```python
@dataclass
class Skill:
    name: str
    description: str
    path: str
    content: Optional[str] = None

class BaseAdapter(ABC):
    platform_name: str
    
    @abstractmethod
    def start(self):
        pass
```

### Error Handling
- Use specific exception types when possible
- Log errors before returning error messages
- Return user-friendly error strings for tool functions

```python
try:
    result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
    return result.stdout or result.stderr or "(命令执行完成，无输出)"
except Exception as e:
    return f"命令执行失败: {str(e)}"
```

### Logging
- Use the `logging` module, not `print()` for production code
- Get logger with `logger = logging.getLogger(__name__)`

```python
import logging

logger = logging.getLogger(__name__)

def run(self, platform: str, user_id: str, user_message: str) -> str:
    logger.info(f"[Agent] start run - platform={platform}, user_id={user_id}")
```

### Comments
- Write comments in Chinese (中文注释) to match the existing codebase
- Use docstrings for public functions and classes

```python
def _handle_message_event(self, data):
    """处理收到的消息事件"""
    # 消息去重：检查是否已处理过该消息
    ...
```

### File Structure
- Each adapter should inherit from `BaseAdapter` in `adapters/base.py`
- Each job should inherit from `Job` in `jobs/base.py`
- Tools are defined in `agent/tools.py` with `TOOL_FUNCTIONS` and `TOOLS_SCHEMA`
- Skills are stored in `skills/<name>/SKILL.md` with YAML frontmatter

### Environment Variables
- Use `python-dotenv` for loading `.env` files
- Define all required env vars in `.env.example`
- Access via `os.getenv("VAR_NAME")` with optional default

```python
from dotenv import load_dotenv
load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
```

### Async Patterns
- Use `async/await` for adapter methods that need to be async
- For calling async from sync context, use `asyncio.run()`

```python
import asyncio

async def send_message(self, chat_id: str, text: str):
    ...

def _run_news_job(self):
    asyncio.run(self.adapter.send_to_owner(message))
```

## Architecture Notes

### Adapters
- `BaseAdapter`: Abstract base class defining the adapter interface
- `TelegramAdapter`: Telegram bot integration using python-telegram-bot
- `FeishuAdapter`: Feishu/Lark integration using lark-oapi WebSocket client

### Agent
- `Agent`: Core LLM interaction class with tool calling support
- `TOOLS_SCHEMA`: OpenAI function definitions
- `TOOL_FUNCTIONS`: Mapping of tool names to Python functions

### Jobs
- Scheduled tasks that run periodically (news, weekly reports)
- Each job inherits from `Job` base class

### Skills
- Extensible skill system stored in `skills/` directory
- Each skill has a `SKILL.md` file with YAML frontmatter
- Skills are dynamically loaded and injected into the system prompt

### Storage
- Session data stored in `~/.mini-openclaw/sessions/`
- Memory stored in `~/.mini-openclaw/memory/`
- Uses JSONL format for session files
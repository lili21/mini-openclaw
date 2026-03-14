import json
import os

CONFIG_DIR = os.path.expanduser("~/.mini-openclaw")
os.makedirs(CONFIG_DIR, exist_ok=True)

OWNER_FILE = os.path.join(CONFIG_DIR, "owner.txt")
USER_MODEL_FILE = os.path.join(CONFIG_DIR, "user_models.json")

AVAILABLE_MODELS = [
    "qwen3.5-plus",
    "qwen3-max-2026-01-23",
    "qwen3-coder-next",
    "qwen3-coder-plus",
    "kimi-k2.5",
    "glm-5",
    "glm-4.7",
    "MiniMax-M2.5",
]
DEFAULT_MODEL = "qwen3.5-plus"

THINKING_MODELS = ["kimi-k2.5"]

MAX_FILE_SIZE = 10 * 1024 * 1024
UPLOAD_DIR = os.path.join(CONFIG_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def get_owner_chat_id() -> str | None:
    if os.path.exists(OWNER_FILE):
        with open(OWNER_FILE, "r") as f:
            return f.read().strip()
    return None


def save_owner_chat_id(chat_id: str):
    with open(OWNER_FILE, "w") as f:
        f.write(str(chat_id))


def _load_user_models() -> dict:
    if os.path.exists(USER_MODEL_FILE):
        with open(USER_MODEL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_user_models(data: dict):
    with open(USER_MODEL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_user_model(platform: str, user_id: str) -> str:
    user_models = _load_user_models()
    key = f"{platform}_{user_id}"
    return user_models.get(key, DEFAULT_MODEL)


def save_user_model(platform: str, user_id: str, model: str):
    user_models = _load_user_models()
    key = f"{platform}_{user_id}"
    user_models[key] = model
    _save_user_models(user_models)

import os
import json

SESSIONS_DIR = os.path.expanduser("~/.mini-openclaw/sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)

def get_session_path(platform: str, user_id: str) -> str:
    return os.path.join(SESSIONS_DIR, f"{platform}_{user_id}.jsonl")

def load_session(platform: str, user_id: str) -> list[dict]:
    path = get_session_path(platform, user_id)
    messages = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    messages.append(json.loads(line))
    return messages

def append_to_session(platform: str, user_id: str, message: dict):
    path = get_session_path(platform, user_id)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(message, ensure_ascii=False) + "\n")

def save_session(platform: str, user_id: str, messages: list[dict]):
    path = get_session_path(platform, user_id)
    with open(path, "w", encoding="utf-8") as f:
        for message in messages:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")

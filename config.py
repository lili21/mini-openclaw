import os

CONFIG_DIR = os.path.expanduser("~/.mini-openclaw")
os.makedirs(CONFIG_DIR, exist_ok=True)

OWNER_FILE = os.path.join(CONFIG_DIR, "owner.txt")

def get_owner_chat_id() -> str | None:
    if os.path.exists(OWNER_FILE):
        with open(OWNER_FILE, "r") as f:
            return f.read().strip()
    return None

def save_owner_chat_id(chat_id: str):
    with open(OWNER_FILE, "w") as f:
        f.write(str(chat_id))

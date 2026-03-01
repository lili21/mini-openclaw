import os
import json
from datetime import datetime
from typing import Optional

SESSIONS_DIR = os.path.expanduser("~/.mini-openclaw/sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)

MAX_RECENT_MESSAGES = 10
COMPRESSION_THRESHOLD = 3000

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

def count_chars(messages: list[dict]) -> int:
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)

def append_to_session(platform: str, user_id: str, message: dict):
    path = get_session_path(platform, user_id)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(message, ensure_ascii=False) + "\n")

def save_session(platform: str, user_id: str, messages: list[dict]):
    path = get_session_path(platform, user_id)
    with open(path, "w", encoding="utf-8") as f:
        for message in messages:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")

def compress_session(platform: str, user_id: str, client, model: str):
    messages = load_session(platform, user_id)
    
    if count_chars(messages) < COMPRESSION_THRESHOLD:
        return
    
    summary_msg = None
    recent_msgs = []
    
    for msg in messages:
        if msg.get("content", "").startswith("【Summary】"):
            summary_msg = msg
        else:
            recent_msgs.append(msg)
    
    recent_msgs = recent_msgs[-MAX_RECENT_MESSAGES:]
    
    if summary_msg:
        existing_summary = summary_msg["content"]
    else:
        existing_summary = ""
    
    prompt = f"""请用50-100字简洁总结以下对话摘要（如果有用则保留），并补充新的对话内容：

【已有摘要】
{existing_summary}

【新增对话】
{json.dumps(recent_msgs[-MAX_RECENT_MESSAGES:], ensure_ascii=False, indent=2)}

请输出更新后的摘要，格式：【Summary】+ 摘要内容。"""

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}]
    )
    
    new_summary = response.choices[0].message.content
    if not new_summary.startswith("【Summary】"):
        new_summary = "【Summary】" + new_summary
    
    save_session(platform, user_id, [
        {"role": "assistant", "content": new_summary}
    ] + recent_msgs)

def archive_session(platform: str, user_id: str) -> Optional[str]:
    current_path = get_session_path(platform, user_id)
    if not os.path.exists(current_path):
        return None
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = os.path.join(SESSIONS_DIR, f"{platform}_{user_id}_{timestamp}.jsonl")
    os.rename(current_path, archive_path)
    return archive_path

def list_sessions(platform: str, user_id: str) -> list[dict]:
    prefix = f"{platform}_{user_id}_"
    sessions = []
    
    if os.path.exists(SESSIONS_DIR):
        for filename in os.listdir(SESSIONS_DIR):
            if filename.startswith(prefix) and filename.endswith(".jsonl"):
                filepath = os.path.join(SESSIONS_DIR, filename)
                mtime = os.path.getmtime(filepath)
                sessions.append({
                    "filename": filename,
                    "created": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                })
    
    sessions.sort(key=lambda x: x["created"], reverse=True)
    return sessions

import os
import json
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

def get_session_path(user_id):
    return os.path.join(SESSIONS_DIR, f"{user_id}.jsonl")

def load_session(user_id):
    """Load conversation history from disk."""
    path = get_session_path(user_id)
    messages = []
    if os.path.exists(path):
        with open(path, "r") as f:
            for line in f:
                if line.strip():
                    messages.append(json.loads(line))
    return messages

def append_to_session(user_id, message):
    """Append a single message to the session file."""
    path = get_session_path(user_id)
    with open(path, "a") as f:
        f.write(json.dumps(message) + "\n")

def save_session(user_id, messages):
    """Overwrite the session file with the full message list."""
    path = get_session_path(user_id)
    with open(path, "w") as f:
        for message in messages:
            f.write(json.dumps(message) + "\n")

async def handle_message(update: Update, context):
    user_id = str(update.effective_user.id)
    user_message = update.message.text

    # Load existing conversation
    messages = load_session(user_id)

    # Add new user message
    user_msg = {"role": "user", "content": user_message}
    messages.append(user_msg)
    append_to_session(user_id, user_msg)

    response = client.chat.completions.create(
        model="qwen3.5-plus",
        messages=[{"role": "user", "content": user_message}]
    )

    # Save assistant response
    assistant_msg = {"role": "assistant", "content": response.content[0].text}
    append_to_session(user_id, assistant_msg)

    await update.message.reply_text(response.choices[0].message.content)

app = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
app.add_handler(MessageHandler(filters.TEXT, handle_message))
app.run_polling()

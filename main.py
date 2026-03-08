import os
# import ssl
import threading
import time
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# 禁用 SSL 验证（仅开发测试环境使用）
# if os.getenv("DISABLE_SSL_VERIFY", "").lower() == "true":
#     print("⚠️  SSL verification disabled (development mode only)")
#     ssl._create_default_https_context = ssl._create_unverified_context
#     os.environ["PYTHONHTTPSVERIFY"] = "0"

# 必须在导入适配器之前设置好环境变量
from agent.core import Agent
from adapters.telegram import TelegramAdapter
from adapters.feishu import FeishuAdapter
from scheduler import Scheduler

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), base_url=os.getenv("BASE_URL"))

agent = Agent(client)

telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
feishu_app_id = os.getenv("FEISHU_APP_ID")
feishu_app_secret = os.getenv("FEISHU_APP_SECRET")

adapters = []
schedulers = []
threads = []

# 初始化 Telegram 适配器
if telegram_token:
    print("Initializing Telegram adapter...")
    tg_adapter = TelegramAdapter(telegram_token, agent)
    adapters.append(("Telegram", tg_adapter))

# 初始化 Feishu 适配器
if feishu_app_id and feishu_app_secret:
    print("Initializing Feishu adapter...")
    feishu_adapter = FeishuAdapter(
        app_id=feishu_app_id,
        app_secret=feishu_app_secret,
        agent=agent,
    )
    adapters.append(("Feishu", feishu_adapter))

if not adapters:
    print(
        "No adapter configured. Set TELEGRAM_BOT_TOKEN or FEISHU_APP_ID/FEISHU_APP_SECRET in .env"
    )
    exit(1)

# 为每个适配器创建并启动调度器
for name, adapter in adapters:
    print(f"Starting scheduler for {name}...")
    scheduler = Scheduler(adapter, agent)
    schedulers.append(scheduler)
    scheduler.start()

# 为每个适配器启动线程
for name, adapter in adapters:
    print(f"Starting {name} adapter...")
    t = threading.Thread(target=adapter.start, name=f"{name}Adapter", daemon=True)
    t.start()
    threads.append((name, t))

print(f"\n✅ All adapters started: {[name for name, _ in adapters]}")
print("Press Ctrl+C to stop\n")

# 主线程保持运行
try:
    while True:
        # 检查线程是否还在运行
        for name, t in threads:
            if not t.is_alive():
                print(f"⚠️  {name} adapter thread died, restarting...")
                # 找到对应的适配器并重启
                for adapter_name, adapter in adapters:
                    if adapter_name == name:
                        new_t = threading.Thread(
                            target=adapter.start, name=f"{name}Adapter", daemon=True
                        )
                        new_t.start()
                        threads = [(n, new_t if n == name else tt) for n, tt in threads]
                        break
        time.sleep(5)
except KeyboardInterrupt:
    print("\n\nShutting down...")
    # 停止所有调度器
    for scheduler in schedulers:
        scheduler.stop()
    print("Goodbye!")

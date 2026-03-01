import os
import json
from datetime import datetime, timedelta

class WeeklyJob:
    name = "周报"
    
    def run(self, agent) -> str:
        try:
            sessions_dir = os.path.expanduser("~/.mini-openclaw/sessions")
            
            today = datetime.now()
            week_ago = today - timedelta(days=7)
            
            all_conversations = []
            
            if os.path.exists(sessions_dir):
                for filename in os.listdir(sessions_dir):
                    if filename.endswith(".jsonl"):
                        filepath = os.path.join(sessions_dir, filename)
                        try:
                            mtime = os.path.getmtime(filepath)
                            file_date = datetime.fromtimestamp(mtime)
                            
                            if file_date >= week_ago:
                                messages = []
                                with open(filepath, "r", encoding="utf-8") as f:
                                    for line in f:
                                        if line.strip():
                                            msg = json.loads(line)
                                            if msg.get("role") == "user":
                                                content = msg.get("content", "")
                                                if not content.startswith("【Summary】"):
                                                    messages.append(content)
                                if messages:
                                    all_conversations.extend(messages)
                        except Exception:
                            continue
            
            if not all_conversations:
                return "这周还没有聊过天呢~"
            
            conversations_text = "\n".join(all_conversations[-50:])
            
            prompt = f"""请用中文总结以下对话内容，列出这周做的主要事情：

{conversations_text}

请用简洁的语言总结3-5件主要的事情。"""
            
            summary = agent.client.chat.completions.create(
                model=agent.model,
                messages=[{"role": "user", "content": prompt}]
            ).choices[0].message.content
            
            return f"📊 本周回顾\n\n{summary}"
            
        except Exception as e:
            return f"生成周报失败: {str(e)}"

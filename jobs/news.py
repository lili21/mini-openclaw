import os
from dotenv import load_dotenv
from firecrawl import Firecrawl

load_dotenv()

class NewsJob:
    name = "每日热点新闻"
    
    def __init__(self):
        self.firecrawl = Firecrawl(api_key=os.getenv("FIRECRAWL_API_KEY"))
    
    def run(self, agent) -> str:
        try:
            hn_result = self.firecrawl.search(
                query="top stories technology",
                limit=10,
                source="web"
            )
            
            news_items = []
            if hn_result and hasattr(hn_result, 'data'):
                for i, item in enumerate(hn_result.data[:10], 1):
                    title = item.get('title', '')
                    url = item.get('url', '')
                    if title:
                        news_items.append(f"{i}. {title}")
            
            if not news_items:
                return "今天没有获取到新闻~"
            
            news_text = "\n".join(news_items)
            
            prompt = f"""请用中文总结以下热点新闻的关键信息，要求简洁、有价值：

{news_text}

请总结3-5条最重要的新闻，每条用1-2句话说明。"""
            
            summary = agent.client.chat.completions.create(
                model=agent.model,
                messages=[{"role": "user", "content": prompt}]
            ).choices[0].message.content
            
            return f"📰 今日热点新闻\n\n{summary}"
            
        except Exception as e:
            return f"获取新闻失败: {str(e)}"

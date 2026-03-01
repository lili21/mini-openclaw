from abc import ABC, abstractmethod

class BaseAdapter(ABC):
    platform_name: str

    @abstractmethod
    async def start(self):
        """启动适配器"""
        pass

    @abstractmethod
    async def send_message(self, chat_id: str, text: str):
        """发送消息"""
        pass

    @abstractmethod
    async def handle_message(self, user_id: str, text: str) -> str:
        """处理消息 - 调用 agent 并返回回复"""
        pass

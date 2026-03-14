from abc import ABC, abstractmethod

from agent.core import AgentResponse


class BaseAdapter(ABC):
    platform_name: str

    @abstractmethod
    def start(self):
        """启动适配器"""
        pass

    @abstractmethod
    async def send_message(self, chat_id: str, text: str):
        """发送消息"""
        pass

    @abstractmethod
    async def handle_message(self, user_id: str, text: str) -> AgentResponse:
        """处理消息 - 调用 agent 并返回回复"""
        pass

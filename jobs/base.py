from abc import ABC, abstractmethod

class Job(ABC):
    name: str
    
    @abstractmethod
    def run(self, agent) -> str:
        """执行任务，返回消息内容"""
        pass

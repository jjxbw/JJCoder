import threading
from abc import ABC, abstractmethod

FILE_MUTATION_LOCK = threading.Lock()

class Tool(ABC):
    name: str
    description: str
    parameters: dict

    @abstractmethod
    def execute(self, **kwargs) -> str:
        ...

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
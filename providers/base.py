from abc import ABC, abstractmethod

class ProviderAdapter(ABC):
    @abstractmethod
    async def chat(self, prompt: str, api_key: str) -> str:
        pass

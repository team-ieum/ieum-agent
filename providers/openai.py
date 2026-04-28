from openai import AsyncOpenAI
from providers.base import ProviderAdapter

class OpenAIAdapter(ProviderAdapter):
    async def chat(self, prompt: str, api_key: str) -> str:
        client = AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content

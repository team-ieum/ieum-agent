import anthropic
from providers.base import ProviderAdapter

class ClaudeAdapter(ProviderAdapter):
    async def chat(self, prompt: str, api_key: str) -> str:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text

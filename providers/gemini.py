from google import genai
from providers.base import ProviderAdapter

class GeminiAdapter(ProviderAdapter):
    async def chat(self, prompt: str, api_key: str) -> str:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=prompt
        )
        return response.text

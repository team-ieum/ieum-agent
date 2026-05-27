from functools import cached_property
from typing import Optional, Any
from google.genai import Client
from google.genai import types
from google.adk.models.google_llm import Gemini

class CustomGemini(Gemini):
    """API Key를 os.environ 없이 동적으로 주입받는 커스텀 Gemini 모델 객체"""
    api_key: Optional[str] = None

    @cached_property
    def api_client(self) -> Client:
        from google.genai import Client

        base_url = self.base_url
        kwargs: dict[str, Any] = {
            'http_options': types.HttpOptions(
                headers=self._tracking_headers(),
                retry_options=self.retry_options,
                base_url=base_url,
            )
        }
        if self.model.startswith('projects/'):
            kwargs['vertexai'] = True

        # os.environ 대신 생성자에서 전달받은 api_key 주입
        if self.api_key:
            kwargs['api_key'] = self.api_key

        return Client(**kwargs)

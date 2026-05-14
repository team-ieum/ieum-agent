from common.error_code import ErrorCode
from core.config import settings
from providers.claude import ClaudeAdapter
from providers.openai import OpenAIAdapter
from providers.gemini import GeminiAdapter


def get_adapter(provider: str):
    match provider.upper():
        case "CLAUDE": return ClaudeAdapter()
        case "OPENAI": return OpenAIAdapter()
        case "GEMINI": return GeminiAdapter()
        case _: raise ValueError(
            f"{ErrorCode.INVALID_PROVIDER.message} "
            f"(지원 목록: {', '.join(settings.SUPPORTED_PROVIDERS)})"
        )

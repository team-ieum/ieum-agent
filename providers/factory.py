from common.error_code import ErrorCode
from providers.claude import ClaudeAdapter
from providers.openai import OpenAIAdapter
from providers.gemini import GeminiAdapter


def get_adapter(provider: str):
    match provider.upper():
        case "CLAUDE": return ClaudeAdapter()
        case "OPENAI": return OpenAIAdapter()
        case "GEMINI": return GeminiAdapter()
        case _: raise ValueError(ErrorCode.INVALID_PROVIDER.message)

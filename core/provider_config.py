from core.config import settings


def _build_model_map() -> dict[str, str]:
    return {
        "CLAUDE": settings.CLAUDE_DEFAULT_MODEL,
        "OPENAI": settings.OPENAI_DEFAULT_MODEL,
        "GEMINI": settings.GEMINI_DEFAULT_MODEL,
    }


ENV_KEY_MAP: dict[str, str] = {
    "CLAUDE": "ANTHROPIC_API_KEY",
    "OPENAI": "OPENAI_API_KEY",
    "GEMINI": "GOOGLE_API_KEY",
}


# 하위 호환: 테스트에서 직접 import 가능하도록 모듈 레벨에서 노출
MODEL_MAP: dict[str, str] = _build_model_map()


def resolve_model(provider: str, model_override: str | None = None) -> str:
    if model_override:
        return model_override
    return _build_model_map().get(provider.upper(), settings.GEMINI_DEFAULT_MODEL)


def resolve_env_key(provider: str) -> str | None:
    return ENV_KEY_MAP.get(provider.upper())

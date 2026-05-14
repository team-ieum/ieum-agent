from core.config import settings


def _build_model_map() -> dict[str, str]:
    return {
        "CLAUDE": settings.CLAUDE_DEFAULT_MODEL,
        "OPENAI": settings.OPENAI_DEFAULT_MODEL,
        "GEMINI": settings.GEMINI_DEFAULT_MODEL,
    }


def _build_env_key_map() -> dict[str, str]:
    return {
        "CLAUDE": settings.CLAUDE_ENV_KEY,
        "OPENAI": settings.OPENAI_ENV_KEY,
        "GEMINI": settings.GEMINI_ENV_KEY,
    }


# 하위 호환: 테스트에서 직접 import 가능하도록 모듈 레벨에서 노출
MODEL_MAP: dict[str, str] = _build_model_map()
ENV_KEY_MAP: dict[str, str] = _build_env_key_map()


def resolve_model(provider: str, model_override: str | None = None) -> str:
    if model_override:
        return model_override
    return _build_model_map().get(provider.upper(), settings.GEMINI_DEFAULT_MODEL)


def resolve_env_key(provider: str) -> str | None:
    return _build_env_key_map().get(provider.upper())

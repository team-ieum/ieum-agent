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
    model = model_override or _build_model_map().get(provider.upper(), settings.GEMINI_DEFAULT_MODEL)
    # [정책] 구형 Gemini 2.x는 최신 기본 모델로 승격한다.
    # gemini-2.5-flash가 대량 조회+요약 단계에서 응답 지연/hang으로 노드 타임아웃을 유발했고,
    # 최신 stable인 gemini-3.5-flash는 agentic 성능이 우수하다. gemini-3.x 명시는 그대로 존중한다.
    if provider.upper() == "GEMINI" and model.startswith("gemini-2"):
        model = settings.GEMINI_DEFAULT_MODEL
    return model


def resolve_env_key(provider: str) -> str | None:
    return ENV_KEY_MAP.get(provider.upper())

import logging

from core.config import settings

logger = logging.getLogger(__name__)


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


def _is_deprecated(model: str) -> bool:
    """litellm 모델 카탈로그의 deprecation_date가 오늘 이전·당일이면 True.
    미등록·날짜 없음·형식 오류는 판정 불가라 False(통과)."""
    from datetime import date
    import litellm  # ADK 의존으로 이미 설치됨. 모듈 import 비용이 커서 호출 시점에만 끌어온다.

    raw = (litellm.model_cost.get(model) or {}).get("deprecation_date")
    if not raw:
        return False
    try:
        return date.fromisoformat(str(raw)) <= date.today()
    except ValueError:
        return False


def resolve_model(provider: str, model_override: str | None = None) -> str:
    default = _build_model_map().get(provider.upper(), settings.GEMINI_DEFAULT_MODEL)
    model = model_override or default
    # [정책] 사용자가 고른 모델(BYOK)은 존중한다. 기본 모델로 강등하는 경우는 둘뿐이다:
    #  1) gemini-2.5-flash — 대량 조회+요약 단계에서 응답 지연/hang으로 노드 타임아웃을 유발한 이력.
    #     다른 2.x(gemini-2.5-pro 등)는 건드리지 않는다(카탈로그에 올릴 수 있어야 한다).
    #  2) litellm 카탈로그상 폐기일이 지난 모델(예: claude-sonnet-4-20250514, 2026-06-15 폐기).
    #     저장된 워크플로우가 폐기 모델을 들고 있어도 실행이 깨지지 않게 한다. 미등록 모델은 통과.
    if provider.upper() == "GEMINI" and model == "gemini-2.5-flash":
        return default
    if _is_deprecated(model):
        logger.warning("폐기된 모델 강등 provider=%s model=%s -> %s", provider, model, default)
        return default
    return model


def resolve_env_key(provider: str) -> str | None:
    return ENV_KEY_MAP.get(provider.upper())

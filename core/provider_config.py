import logging
from datetime import date

# ADK 의존으로 이미 설치됨. 호출 시점 lazy import로 두면 프로세스 최초 litellm 로딩(원격 model_cost
# fetch 포함, 1~8초)이 동기 resolve_model 안에서 일어나 이벤트 루프를 통째로 멈춘다. 기동 시점에 치른다.
import litellm

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

# LiteLlm 라우팅용 접두(model_factory._LITELLM_PREFIX와 동일 집합 + cost 조회용 gemini/).
_ROUTING_PREFIXES = ("anthropic/", "openai/", "gemini/")

# 하위 호환: 테스트에서 직접 import 가능하도록 모듈 레벨에서 노출
MODEL_MAP: dict[str, str] = _build_model_map()

# 같은 (provider, model) 강등 경고는 프로세스당 1회만 — 채팅 한 턴에 하이드레이션이 3회 돌고
# 실행마다 또 돌아, 그대로 두면 노드당 수십 줄이 쌓여 실제 신호가 묻힌다.
_demotion_warned: set[tuple[str, str]] = set()


def _bare_model(model: str) -> str:
    for p in _ROUTING_PREFIXES:
        if model.startswith(p):
            return model[len(p):]
    return model


def _catalog_key(provider: str, model: str) -> str:
    """litellm.model_cost 조회 키. Gemini는 bare 키가 Vertex 행이라 폐기일이 다르다 —
    IEUM이 쓰는 AI Studio 행은 'gemini/<model>'이다(cost_model_name과 같은 규칙)."""
    bare = _bare_model(model)
    return f"gemini/{bare}" if provider.upper() == "GEMINI" else bare


def _is_deprecated(provider: str, model: str) -> bool:
    """litellm 모델 카탈로그의 deprecation_date가 오늘 이전·당일이면 True.
    미등록·날짜 없음·형식 오류는 판정 불가라 False(통과)."""
    raw = (litellm.model_cost.get(_catalog_key(provider, model)) or {}).get("deprecation_date")
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
    #  1) gemini-2.5-flash 계열(-lite·-preview 포함) — 대량 조회+요약 단계에서 응답 지연/hang으로
    #     노드 타임아웃을 유발한 이력. gemini-2.5-pro 등은 건드리지 않는다(카탈로그에 올릴 수 있어야 한다).
    #  2) litellm 카탈로그상 폐기일이 지난 모델(예: claude-sonnet-4-20250514, 2026-06-15 폐기).
    #     저장된 워크플로우가 폐기 모델을 들고 있어도 실행이 깨지지 않게 한다. 미등록 모델은 통과.
    #     단, provider 기본 모델 자체는 강등 대상이 아니다(자기 자신으로 강등 = 무의미 + 경고 스팸).
    #     기본값 위생은 운영자/.env의 책임이다.
    if provider.upper() == "GEMINI" and _bare_model(model).startswith("gemini-2.5-flash"):
        return default
    if model != default and _is_deprecated(provider, model):
        key = (provider.upper(), model)
        if key not in _demotion_warned:
            _demotion_warned.add(key)
            logger.warning("폐기된 모델 강등 provider=%s model=%s -> %s", provider, model, default)
        return default
    return model


def resolve_env_key(provider: str) -> str | None:
    return ENV_KEY_MAP.get(provider.upper())

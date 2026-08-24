"""BE ProviderRegistry 카탈로그(IEUM-BE-67)와 agent resolve_model의 계약.

카탈로그의 모든 id는 승격·강등 없이 자기 자신으로 실행돼야 한다. 여기서 깨지면
사용자가 고른 모델이 조용히 다른 모델로 바뀐다(또는 채팅 편집마다 기본값으로 스냅백).

CATALOG는 ieum-backend api/.../provider/service/ProviderRegistry.java의 복제다 —
크로스레포라 코드로 묶을 수 없다. BE 카탈로그가 바뀌면 여기도 바꾼다.
실 litellm.model_cost를 그대로 쓴다(폐기일 판정이 목적이라 monkeypatch하지 않는다).
"""
import pytest

from core.config import settings
from core.provider_config import _is_deprecated, resolve_model

CATALOG = [
    ("CLAUDE", "claude-haiku-4-5"),
    ("CLAUDE", "claude-sonnet-5"),
    ("CLAUDE", "claude-opus-5"),
    ("OPENAI", "gpt-5.6-luna"),
    ("OPENAI", "gpt-5.6-terra"),
    ("OPENAI", "gpt-5.6-sol"),
    ("GEMINI", "gemini-3.5-flash-lite"),
    ("GEMINI", "gemini-3.7-flash"),
    ("GEMINI", "gemini-2.5-pro"),
]

BE_DEFAULTS = {"CLAUDE": "claude-sonnet-5", "OPENAI": "gpt-5.6-terra", "GEMINI": "gemini-3.7-flash"}


@pytest.mark.parametrize("provider,model", CATALOG)
def test_catalog_model_resolves_to_itself(provider, model):
    assert resolve_model(provider, model) == model


@pytest.mark.parametrize("provider,model", CATALOG)
def test_catalog_model_not_deprecated(provider, model):
    assert _is_deprecated(provider, model) is False


def test_be_defaults_match_agent_code_defaults():
    """BE ModelInfo.default == agent 코드 기본값. .env가 덮어쓰면 settings가 달라지므로
    코드 기본값(model_fields default)과 비교한다 — 배포 .env 위생은 운영 몫."""
    code_defaults = {
        "CLAUDE": type(settings).model_fields["CLAUDE_DEFAULT_MODEL"].default,
        "OPENAI": type(settings).model_fields["OPENAI_DEFAULT_MODEL"].default,
        "GEMINI": type(settings).model_fields["GEMINI_DEFAULT_MODEL"].default,
    }
    assert code_defaults == BE_DEFAULTS

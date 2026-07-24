"""core/model_factory.py 단위 테스트.

provider/role/api_key + 자체 LLM 엔드포인트 설정 여부에 따른 model 파라미터 분기를 검증한다.
핵심 규칙:
- 자체 LLM은 SELF_HOSTED_LLM_BASE_URL이 설정되어 활성일 때만 동작(미설정 시 전원 키 경로).
- 활성 + 허용 role(ROLE_ADMIN/ROLE_TESTER) + 등록 API 키 없음 → 자체 LLM(OpenAI 호환 LiteLlm).
- 자격이 있어도 키를 등록하면 그 키를 우선한다(키 우선, 없으면 자체 LLM).
LiteLlm 경로는 litellm 미설치 환경에서도 돌도록 sys.modules에 가짜 모듈을 주입해 검증한다.
"""
import sys
from unittest.mock import MagicMock, patch

import pytest

from core.config import settings
from core.model_factory import build_model_param, uses_env_key, is_self_hosted_eligible


def _patch_litellm():
    """`from google.adk.models.lite_llm import LiteLlm`가 가짜를 가져오도록 모듈을 주입한다."""
    fake_module = MagicMock()
    return patch.dict(sys.modules, {"google.adk.models.lite_llm": fake_module}), fake_module


@pytest.fixture
def active(monkeypatch):
    """자체 LLM 엔드포인트가 설정된 활성 상태를 만든다."""
    monkeypatch.setattr(settings, "SELF_HOSTED_LLM_BASE_URL", "http://llm:8001/v1")
    monkeypatch.setattr(settings, "SELF_HOSTED_LLM_MODEL", "ieum-ft-1")
    monkeypatch.setattr(settings, "SELF_HOSTED_LLM_API_KEY", "")


# ---------- 활성 + 허용 role + 키 없음 → 자체 LLM ----------

@pytest.mark.parametrize("role", ["ROLE_TESTER", "ROLE_ADMIN"])
@pytest.mark.parametrize("api_key", [None, ""])
def test_self_hosted_for_eligible_role_without_key(active, role, api_key):
    ctx, fake_module = _patch_litellm()
    with ctx:
        build_model_param("CLAUDE", "claude-sonnet-4-20250514", api_key, role)
    fake_module.LiteLlm.assert_called_once()
    _, kwargs = fake_module.LiteLlm.call_args
    assert kwargs["model"] == "openai/ieum-ft-1"
    assert kwargs["api_base"] == "http://llm:8001/v1"


def test_self_hosted_regardless_of_provider(active):
    # provider와 무관하게 허용 role + 키 없음이면 자체 LLM (OPENAI provider 노드도)
    ctx, fake_module = _patch_litellm()
    with ctx:
        build_model_param("OPENAI", "gpt-4o", None, "ROLE_TESTER")
    fake_module.LiteLlm.assert_called_once()


def test_self_hosted_overrides_gemini(active):
    # GEMINI provider여도 자격+무키면 자체 LLM (CustomGemini(api_key=None)으로 빠지지 않음)
    ctx, fake_module = _patch_litellm()
    with ctx:
        build_model_param("GEMINI", "gemini-2.5-pro", None, "ROLE_TESTER")
    fake_module.LiteLlm.assert_called_once()


# ---------- 비활성(엔드포인트 미설정) → 키 경로 ----------

def test_inactive_when_not_configured_returns_model_string():
    # SELF_HOSTED_LLM_BASE_URL 기본 "" → 비활성 → 모델명 문자열
    assert build_model_param("CLAUDE", "claude-sonnet-4-20250514", None, "ROLE_TESTER") == "claude-sonnet-4-20250514"


def test_inactive_when_model_missing(monkeypatch):
    # BASE_URL만 있고 MODEL이 비면 비활성 → 키 경로 (openai/ 빈 모델명 방지)
    monkeypatch.setattr(settings, "SELF_HOSTED_LLM_BASE_URL", "http://llm:8001/v1")
    monkeypatch.setattr(settings, "SELF_HOSTED_LLM_MODEL", "")
    assert is_self_hosted_eligible("ROLE_TESTER") is False
    assert build_model_param("CLAUDE", "claude-sonnet-4-20250514", None, "ROLE_TESTER") == "claude-sonnet-4-20250514"


# ---------- 키 우선 (자격 있어도 키 등록 시 키 사용) — Task 2: 이제는 LiteLlm 직접 주입 ----------

@pytest.mark.parametrize("role", ["ROLE_TESTER", "ROLE_ADMIN"])
def test_eligible_role_with_key_uses_key(active, role):
    # Task 2: 자격 있어도 키 있으면 LiteLlm으로 직접 주입(env 미오염)
    ctx, fake_module = _patch_litellm()
    with ctx:
        build_model_param("CLAUDE", "claude-sonnet-4-20250514", "my-key", role)
    fake_module.LiteLlm.assert_called_once()
    _, kwargs = fake_module.LiteLlm.call_args
    assert kwargs["model"] == "anthropic/claude-sonnet-4-20250514"
    assert kwargs["api_key"] == "my-key"


# ---------- 기존(API 키) 경로 보존 — Task 2: api_key 있으면 LiteLlm 직접 주입 ----------

def test_user_role_returns_litellm_when_has_key(active):
    # ROLE_USER는 자체 LLM 자격 없음 + api_key 있음 → Task 2: LiteLlm 직접 주입
    ctx, fake_module = _patch_litellm()
    with ctx:
        build_model_param("CLAUDE", "claude-sonnet-4-20250514", "key", "ROLE_USER")
    fake_module.LiteLlm.assert_called_once()
    _, kwargs = fake_module.LiteLlm.call_args
    assert kwargs["model"] == "anthropic/claude-sonnet-4-20250514"
    assert kwargs["api_key"] == "key"


def test_no_role_returns_litellm_when_has_key(active):
    # role=None은 자체 LLM 자격 없음 + api_key 있음 → Task 2: LiteLlm 직접 주입
    ctx, fake_module = _patch_litellm()
    with ctx:
        build_model_param("CLAUDE", "claude-sonnet-4-20250514", "key", None)
    fake_module.LiteLlm.assert_called_once()
    _, kwargs = fake_module.LiteLlm.call_args
    assert kwargs["model"] == "anthropic/claude-sonnet-4-20250514"
    assert kwargs["api_key"] == "key"


def test_gemini_returns_custom_gemini():
    from core.custom_gemini import CustomGemini
    result = build_model_param("GEMINI", "gemini-2.5-pro", "key", "ROLE_TESTER")
    assert isinstance(result, CustomGemini)


# ---------- uses_env_key ----------

def test_uses_env_key_self_hosted_active(active):
    assert uses_env_key("CLAUDE", None, "ROLE_TESTER") is False   # 자체 LLM → 주입 불필요
    assert uses_env_key("CLAUDE", "key", "ROLE_TESTER") is False  # LiteLlm 직접 주입 → 주입 불필요
    assert uses_env_key("CLAUDE", "key", "ROLE_USER") is False    # LiteLlm 직접 주입 → 주입 불필요


def test_uses_env_key_inactive():
    # 미설정 + 키 있음 → LiteLlm 직접 주입(주입 불필요)
    assert uses_env_key("CLAUDE", "key", "ROLE_TESTER") is False
    # 키 없음 → 주입할 키가 없으므로 False (TypeError 방어)
    assert uses_env_key("CLAUDE", None, "ROLE_TESTER") is False


@pytest.mark.parametrize("api_key,role", [("key", None), (None, "ROLE_TESTER")])
def test_uses_env_key_gemini(api_key, role):
    assert uses_env_key("GEMINI", api_key, role) is False


def test_commercial_with_key_no_env_injection():
    # LiteLlm 인스턴스 주입으로 바뀌었으므로 env 주입 불필요
    assert uses_env_key("CLAUDE", "sk-user-key", "ROLE_USER") is False
    assert uses_env_key("OPENAI", "sk-user-key", "ROLE_USER") is False


# ---------- is_self_hosted_eligible ----------

def test_eligible_when_active(active):
    assert is_self_hosted_eligible("ROLE_TESTER") is True
    assert is_self_hosted_eligible("ROLE_ADMIN") is True
    assert is_self_hosted_eligible("ROLE_USER") is False
    assert is_self_hosted_eligible(None) is False


def test_eligible_false_when_not_configured():
    # 엔드포인트 미설정 시 자격 role이어도 False (키 필수 유지)
    assert is_self_hosted_eligible("ROLE_TESTER") is False


# ---------- _litellm_model_name ----------

from core.model_factory import _litellm_model_name


@pytest.mark.parametrize("provider,model,expected", [
    ("CLAUDE", "claude-sonnet-4-5", "anthropic/claude-sonnet-4-5"),
    ("OPENAI", "gpt-4o", "openai/gpt-4o"),
    ("CLAUDE", "anthropic/claude-sonnet-4-5", "anthropic/claude-sonnet-4-5"),  # 중복 prefix 방지
    ("UNKNOWN", "some-model", "some-model"),  # 매핑 없음 → 원본
])
def test_litellm_model_name(provider, model, expected):
    assert _litellm_model_name(provider, model) == expected


# ---------- Claude/OpenAI + 키 있음 → LiteLlm 인스턴스 주입 ----------

@pytest.mark.parametrize("provider,model,expected_model", [
    ("CLAUDE", "claude-sonnet-4-5", "anthropic/claude-sonnet-4-5"),
    ("OPENAI", "gpt-4o", "openai/gpt-4o"),
])
def test_commercial_with_key_uses_litellm_instance(provider, model, expected_model):
    ctx, fake_module = _patch_litellm()
    with ctx:
        build_model_param(provider, model, "sk-user-key", "ROLE_USER")
    fake_module.LiteLlm.assert_called_once()
    _, kwargs = fake_module.LiteLlm.call_args
    assert kwargs["model"] == expected_model
    assert kwargs["api_key"] == "sk-user-key"
    assert "api_base" not in kwargs  # 상용은 api_base 없음(self-hosted와 구분)


@pytest.mark.parametrize("api_key", [None, ""])
def test_commercial_without_key_falls_back_to_string(api_key):
    # 키 없으면 LiteLlm 인스턴스가 아니라 모델명 문자열(env fallback) 반환
    result = build_model_param("CLAUDE", "claude-sonnet-4-5", api_key, "ROLE_USER")
    assert result == "claude-sonnet-4-5"


def test_gemini_still_customgemini():
    # GEMINI는 변경 없음 — CustomGemini 유지
    result = build_model_param("GEMINI", "gemini-3.5-flash", "gkey", "ROLE_USER")
    from core.custom_gemini import CustomGemini
    assert isinstance(result, CustomGemini)

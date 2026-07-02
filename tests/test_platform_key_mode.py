"""베타 플랫폼 키 모드 (X-Key-Mode: platform) 테스트."""
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from api.middleware.credential import get_llm_credentials
from core.config import settings


def test_platform_gemini_api_key_setting_exists():
    # 기본값은 빈 문자열(= platform 모드 비활성). .env 없이도 str 타입 보장.
    assert isinstance(settings.PLATFORM_GEMINI_API_KEY, str)


async def _call_middleware(**overrides):
    kwargs = dict(
        x_llm_provider="CLAUDE",
        x_llm_api_key=None,
        x_user_id="user-1",
        x_user_role=None,
        x_google_access_token=None,
        x_notion_token=None,
        x_github_token=None,
        x_key_mode=None,
    )
    kwargs.update(overrides)
    return await get_llm_credentials(**kwargs)


@pytest.mark.asyncio
async def test_platform_mode_swaps_to_platform_gemini_key():
    with patch.object(settings, "PLATFORM_GEMINI_API_KEY", "pk-test"):
        creds = await _call_middleware(x_key_mode="platform")
    # provider가 원래 CLAUDE여도 GEMINI + 플랫폼 키로 강제된다
    assert creds["provider"] == "GEMINI"
    assert creds["api_key"] == "pk-test"
    assert creds["key_mode"] == "platform"


@pytest.mark.asyncio
async def test_platform_mode_user_key_wins():
    # 사용자 키가 함께 오면 사용자 키 우선 (스왑·key_mode 미적용)
    with patch.object(settings, "PLATFORM_GEMINI_API_KEY", "pk-test"):
        creds = await _call_middleware(x_key_mode="platform", x_llm_api_key="sk-user")
    assert creds["provider"] == "CLAUDE"
    assert creds["api_key"] == "sk-user"
    assert creds["key_mode"] is None


@pytest.mark.asyncio
async def test_platform_mode_rejected_when_key_unconfigured():
    # 플랫폼 키 미설정이면 기존 MISSING_CREDENTIAL 400 유지 (조용한 폴백 금지)
    with patch.object(settings, "PLATFORM_GEMINI_API_KEY", ""):
        with pytest.raises(HTTPException) as exc:
            await _call_middleware(x_key_mode="platform")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_no_key_mode_header_keeps_existing_behavior():
    # 회귀 가드: 헤더 없음 + 키 없음 → 기존 400
    with pytest.raises(HTTPException) as exc:
        await _call_middleware()
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_no_key_mode_header_with_user_key_unchanged():
    # 회귀 가드: 기존 BYOK 경로 무변경, key_mode는 None
    creds = await _call_middleware(x_llm_api_key="sk-user")
    assert creds["provider"] == "CLAUDE"
    assert creds["api_key"] == "sk-user"
    assert creds["key_mode"] is None

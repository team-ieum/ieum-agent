"""베타 플랫폼 키 모드 (X-Key-Mode: platform) 테스트."""
from core.config import settings


def test_platform_gemini_api_key_setting_exists():
    # 기본값은 빈 문자열(= platform 모드 비활성). .env 없이도 str 타입 보장.
    assert isinstance(settings.PLATFORM_GEMINI_API_KEY, str)

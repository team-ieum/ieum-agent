import logging

from fastapi import Header, HTTPException

from common.error_code import ErrorCode
from core.config import settings
from core.model_factory import is_self_hosted_eligible

logger = logging.getLogger(__name__)


async def get_llm_credentials(
    x_llm_provider: str = Header(..., alias="X-LLM-Provider"),
    x_llm_api_key: str | None = Header(None, alias="X-LLM-Api-Key"),
    x_user_id: str = Header(..., alias="X-User-Id"),
    x_user_role: str | None = Header(None, alias="X-User-Role"),
    x_google_access_token: str | None = Header(None, alias="X-Google-Access-Token"),
    x_notion_token: str | None = Header(None, alias="X-Notion-Token"),
    x_github_token: str | None = Header(None, alias="X-GitHub-Token"),
    x_key_mode: str | None = Header(None, alias="X-Key-Mode"),
):
    provider = x_llm_provider
    api_key = x_llm_api_key
    key_mode = None
    self_hosted_eligible = is_self_hosted_eligible(x_user_role)

    # 베타 platform 모드: BE가 베타 자격·쿼터 통과를 확인하고 위임한 요청.
    # 우선순위(BYOK > self-hosted > platform)를 미들웨어에서도 방어한다 —
    # 사용자 키가 없고 self-hosted 자격도 아닐 때만 플랫폼 Gemini 키로 대체한다.
    # PLATFORM_GEMINI_API_KEY 미설정이면 스왑하지 않아 아래 필수 검증에서 기존 400으로 거부된다(조용한 폴백 금지).
    if x_key_mode == "platform" and not x_llm_api_key and not self_hosted_eligible:
        if settings.PLATFORM_GEMINI_API_KEY:
            provider = "GEMINI"
            api_key = settings.PLATFORM_GEMINI_API_KEY
            key_mode = "platform"
        else:
            # 서버 미프로비저닝이 "유저가 키 안 보냄" 400과 byte 동일하게 떨어지므로 운영자 추적용 흔적을 남긴다.
            logger.error(
                "X-Key-Mode: platform 요청이지만 PLATFORM_GEMINI_API_KEY 미설정 — user %s 요청이 400으로 거부된다",
                x_user_id,
            )
    elif x_key_mode and x_key_mode != "platform":
        # 미인식 값을 조용히 무시하면 keyMode 감사필드가 null로 기록돼 BE 정산 추적이 오염된다.
        logger.warning("미인식 X-Key-Mode=%r — 무시하고 기존 키 경로로 진행", x_key_mode)

    # 자체 LLM 자격이 있는 요청(개발/테스트 계정)은 키 미등록 시 자체 엔드포인트로 라우팅되므로 LLM API 키가 없어도 허용한다.
    # (엔드포인트 미설정 시 is_self_hosted_eligible은 False이므로 키가 필수로 유지된다.)
    api_key_optional = self_hosted_eligible
    if not provider or (not api_key and not api_key_optional):
        raise HTTPException(status_code=ErrorCode.MISSING_CREDENTIAL.status_code, detail=ErrorCode.MISSING_CREDENTIAL.message)
    return {
        "provider": provider,
        "api_key": api_key,
        "user_id": x_user_id,
        "user_role": x_user_role,
        "key_mode": key_mode,
        "google_access_token": x_google_access_token,
        "notion_token": x_notion_token,
        "github_token": x_github_token,
    }

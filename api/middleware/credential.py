from fastapi import Header, HTTPException

from common.error_code import ErrorCode
from core.model_factory import is_self_hosted_eligible


async def get_llm_credentials(
    x_llm_provider: str = Header(..., alias="X-LLM-Provider"),
    x_llm_api_key: str | None = Header(None, alias="X-LLM-Api-Key"),
    x_user_id: str = Header(..., alias="X-User-Id"),
    x_user_role: str | None = Header(None, alias="X-User-Role"),
    x_google_access_token: str | None = Header(None, alias="X-Google-Access-Token"),
    x_notion_token: str | None = Header(None, alias="X-Notion-Token"),
    x_github_token: str | None = Header(None, alias="X-GitHub-Token"),
):
    # 자체 LLM 자격이 있는 요청(개발/테스트 계정)은 키 미등록 시 자체 엔드포인트로 라우팅되므로 LLM API 키가 없어도 허용한다.
    # (엔드포인트 미설정 시 is_self_hosted_eligible은 False이므로 키가 필수로 유지된다.)
    api_key_optional = is_self_hosted_eligible(x_user_role)
    if not x_llm_provider or (not x_llm_api_key and not api_key_optional):
        raise HTTPException(status_code=ErrorCode.MISSING_CREDENTIAL.status_code, detail=ErrorCode.MISSING_CREDENTIAL.message)
    return {
        "provider": x_llm_provider,
        "api_key": x_llm_api_key,
        "user_id": x_user_id,
        "user_role": x_user_role,
        "google_access_token": x_google_access_token,
        "notion_token": x_notion_token,
        "github_token": x_github_token,
    }

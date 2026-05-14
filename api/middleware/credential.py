from fastapi import Header, HTTPException

from common.error_code import ErrorCode


async def get_llm_credentials(
    x_llm_provider: str = Header(..., alias="X-LLM-Provider"),
    x_llm_api_key: str = Header(..., alias="X-LLM-Api-Key"),
    x_user_id: str = Header(..., alias="X-User-Id"),
):
    if not x_llm_provider or not x_llm_api_key:
        raise HTTPException(status_code=ErrorCode.MISSING_CREDENTIAL.status_code, detail=ErrorCode.MISSING_CREDENTIAL.message)
    return {"provider": x_llm_provider, "api_key": x_llm_api_key, "user_id": x_user_id}

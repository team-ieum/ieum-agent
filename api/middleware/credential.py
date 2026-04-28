from fastapi import Header, HTTPException

async def get_llm_credentials(
    x_llm_provider: str = Header(..., alias="X-LLM-Provider"),
    x_llm_api_key: str = Header(..., alias="X-LLM-Api-Key")
):
    if not x_llm_provider or not x_llm_api_key:
        raise HTTPException(status_code=400, detail="LLM 헤더가 누락되었습니다.")
    return {"provider": x_llm_provider, "api_key": x_llm_api_key}

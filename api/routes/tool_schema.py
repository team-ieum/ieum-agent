from fastapi import APIRouter

from tools.registry import tool_form_schema

router = APIRouter()


@router.get("/tools/schema")
async def get_tool_schema() -> dict:
    """노드 도구 설정 폼 스키마. BE가 사용자 인증 후 GET /api/v1/tools/schema로 그대로 전달한다.
    LLM 자격증명이 필요 없는 정적 메타라 get_llm_credentials를 걸지 않는다(/health와 같은 취급)."""
    return tool_form_schema()

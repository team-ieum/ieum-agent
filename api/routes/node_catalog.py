from fastapi import APIRouter

from core.node_catalog import node_catalog

router = APIRouter()


@router.get("/nodes/catalog")
async def get_node_catalog() -> dict:
    """노드 카탈로그. BE가 사용자 인증 후 GET /api/v1/nodes/catalog로 그대로 전달한다.
    LLM 자격증명이 필요 없는 정적 메타라 get_llm_credentials를 걸지 않는다(/health와 같은 취급)."""
    return node_catalog()

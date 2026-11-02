from enum import Enum
from typing import Any, Literal, Optional, List
from pydantic import BaseModel, ConfigDict, Field, field_validator
from api.schemas.response import UsageRecord


class ChatResponseType(str, Enum):
    WORKFLOW_MODIFIED    = "WORKFLOW_MODIFIED"
    CLARIFICATION_NEEDED = "CLARIFICATION_NEEDED"

class ClarificationOption(BaseModel):
    """사용자가 고를 수 있는 선택지. CLARIFICATION_NEEDED 질문의 선택지와 빌더 모델 카드(응답 type 무관)에 쓰인다.
    UI는 label 버튼을 렌더하고, 선택 시 value를 후속 채팅 메시지로 보낸다.
    (예: GitHub repo 선택, Slack/Discord 웹훅 선택, AI 노드 모델 카드)"""
    value: str                          # 선택 시 사용할 값 (예: "ieum/ieum-backend")
    label: str                          # 사용자에게 보일 텍스트 (예: "ieum-backend")
    description: Optional[str] = None   # 부가 설명 (선택)


# ── 대화형 노드 단위 생성(/v1/chat/stream, spec 2026-10-28 §2.1) ─────────────────────────────

class LooseNode(BaseModel):
    """대화형 생성의 노드. id·event 비공백만 본다 — BUILDING으로 저장된 미완성 노드(cron 없는 schedule, method 없는
    http 등)가 다음 턴에 돌아와도 422가 나지 않게 한다. 모르는 키(position·description·type 등)는 그대로 둔다."""
    model_config = ConfigDict(extra="allow")

    id: str
    event: str = Field(pattern=r"^\S+$")

    @field_validator("id")
    @classmethod
    def _id_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("노드 id가 비었습니다.")
        return v


class BuildChatRequest(BaseModel):
    """모르는 키(옛 BE가 보내던 연동·MCP·웹훅 목록 등)는 무시한다(pydantic 기본)."""
    prompt: str
    workflowId: Optional[str] = None    # backend가 채워 보낸다. 없으면 엔진이 거절한다
    currentNodes: Optional[List[LooseNode]] = None
    # 엣지도 모르는 키(BE 엣지 id 등)를 지우지 않고 돌려준다
    currentEdges: Optional[List[dict[str, Any]]] = None


class BuildChatResponse(BaseModel):
    """노드·엣지는 dict 그대로(요청에서 온 키 보존). buildState는 항상 싣는다 — DONE은 finish 확인 턴에만."""
    message: str
    type: ChatResponseType
    options: List[ClarificationOption] = []   # CLARIFICATION_NEEDED 질문 선택지 또는 빌더 모델 카드(type 무관), 그 외 빈 배열
    nodes: Optional[List[dict[str, Any]]] = None
    edges: Optional[List[dict[str, Any]]] = None
    rawPrompt: str
    workflowName: Optional[str] = None
    # 이 턴의 모든 LLM 호출을 합산한 토큰 사용량. backend의 베타 토큰 쿼터 차감에 쓰인다.
    # 모델이 토큰을 보고하지 않으면 None (/v1/execute의 AgentExecutionResult.usage와 동일 규약).
    usage: Optional[UsageRecord] = None
    buildState: Literal["BUILDING", "DONE"] = "BUILDING"

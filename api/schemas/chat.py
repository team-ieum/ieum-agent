from enum import Enum
from typing import Any, Literal, Optional, List
from pydantic import BaseModel, ConfigDict, Field, field_validator
from api.schemas.generate_workflow import WorkflowNode, WorkflowEdge, McpServerMeta, WebhookMeta
from api.schemas.request import McpServerConfig
from api.schemas.response import UsageRecord


class ChatResponseType(str, Enum):
    WORKFLOW_GENERATED   = "WORKFLOW_GENERATED"
    WORKFLOW_MODIFIED    = "WORKFLOW_MODIFIED"
    INTEGRATION_REQUIRED = "INTEGRATION_REQUIRED"
    CLARIFICATION_NEEDED = "CLARIFICATION_NEEDED"

class IntegrationProvider(str, Enum):
    GOOGLE  = "GOOGLE"
    NOTION  = "NOTION"
    SLACK   = "SLACK"
    DISCORD = "DISCORD"
    GITHUB  = "GITHUB"
    TRENDRADAR = "TRENDRADAR"

class IntegrationType(str, Enum):
    OAUTH   = "OAUTH"
    WEBHOOK = "WEBHOOK"

class ActionType(str, Enum):
    OAUTH = "OAUTH"

class CredentialInfo(BaseModel):
    id: str
    displayName: str

class AvailableIntegration(BaseModel):
    provider: IntegrationProvider
    credentials: List[CredentialInfo] = []

class UnavailableIntegration(BaseModel):
    provider: IntegrationProvider
    type: IntegrationType

class ChatRequest(BaseModel):
    prompt: str
    # 수정 대상 워크플로우 ID. backend가 채워 보낸다.
    # 있으면 워크플로우별 멀티턴 세션을 이어가고, 없으면(신규 생성) 매 요청 격리된 세션을 사용한다.
    workflowId: Optional[str] = None
    currentNodes: Optional[List[WorkflowNode]] = None
    currentEdges: Optional[List[WorkflowEdge]] = None
    availableIntegrations: List[AvailableIntegration] = []
    unavailableIntegrations: List[UnavailableIntegration] = []
    mcpServers: Optional[List[McpServerConfig]] = None
    # 사용자가 보유한 MCP 서버 카탈로그 메타(catalogId/name/description). backend가 채워 보낸다.
    # 생성/수정 단계에서 Designer가 적절한 AI 노드에 mcp 도구를 배정하는 데 사용한다(환각 방지).
    availableMcpServers: Optional[List[McpServerMeta]] = None
    # 사용자가 보유한 Slack/Discord 웹훅 자격증명 메타. backend가 채워 보낸다.
    # Designer가 slack/discord 노드에 webhookCredentialId를 배정하는 데 사용한다(환각 방지).
    availableWebhooks: Optional[List[WebhookMeta]] = None

class ChatAction(BaseModel):
    type: ActionType
    provider: IntegrationProvider

class ClarificationOption(BaseModel):
    """CLARIFICATION_NEEDED 응답에서 사용자가 고를 수 있는 선택지.
    UI는 label 버튼을 렌더하고, 선택 시 value를 후속 채팅 메시지로 보낸다.
    (예: GitHub repo 선택, Slack/Discord 웹훅 선택)"""
    value: str                          # 선택 시 사용할 값 (예: "ieum/ieum-backend")
    label: str                          # 사용자에게 보일 텍스트 (예: "ieum-backend")
    description: Optional[str] = None   # 부가 설명 (선택)

class ChatResponse(BaseModel):
    message: str
    type: ChatResponseType
    actions: List[ChatAction] = []
    options: List[ClarificationOption] = []   # CLARIFICATION_NEEDED일 때 선택지, 그 외 빈 배열
    changeDescription: Optional[str] = None
    nodes: Optional[List[WorkflowNode]] = None
    edges: Optional[List[WorkflowEdge]] = None
    rawPrompt: str
    workflowName: Optional[str] = None
    # designer·reviewer의 모든 LLM 호출을 합산한 토큰 사용량. backend의 베타 토큰 쿼터 차감에 쓰인다.
    # 모델이 토큰을 보고하지 않으면 None (/v1/execute의 AgentExecutionResult.usage와 동일 규약).
    usage: Optional[UsageRecord] = None


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


class BuildChatRequest(ChatRequest):
    currentNodes: Optional[List[LooseNode]] = None
    # 엣지도 모르는 키(BE 엣지 id 등)를 지우지 않고 돌려준다
    currentEdges: Optional[List[dict[str, Any]]] = None


class BuildChatResponse(ChatResponse):
    """노드·엣지는 dict 그대로(요청에서 온 키 보존). buildState는 항상 싣는다 — DONE은 finish 확인 턴에만."""
    nodes: Optional[List[dict[str, Any]]] = None
    edges: Optional[List[dict[str, Any]]] = None
    buildState: Literal["BUILDING", "DONE"] = "BUILDING"

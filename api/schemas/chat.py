from enum import Enum
from typing import Optional, List
from pydantic import BaseModel
from api.schemas.generate_workflow import WorkflowNode, WorkflowEdge, McpServerMeta, WebhookMeta
from api.schemas.request import McpServerConfig


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

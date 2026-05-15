from enum import Enum
from typing import Optional, List
from pydantic import BaseModel
from api.schemas.generate_workflow import WorkflowNode, WorkflowEdge


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
    currentNodes: Optional[List[WorkflowNode]] = None
    currentEdges: Optional[List[WorkflowEdge]] = None
    availableIntegrations: List[AvailableIntegration] = []
    unavailableIntegrations: List[UnavailableIntegration] = []

class ChatAction(BaseModel):
    type: ActionType
    provider: IntegrationProvider

class ChatResponse(BaseModel):
    message: str
    type: ChatResponseType
    actions: List[ChatAction] = []
    changeDescription: Optional[str] = None
    nodes: Optional[List[WorkflowNode]] = None
    edges: Optional[List[WorkflowEdge]] = None
    rawPrompt: str

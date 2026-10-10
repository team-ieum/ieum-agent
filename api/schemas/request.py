from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any


class ModelParameters(BaseModel):
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


class McpServerConfig(BaseModel):
    """커스텀 MCP 서버 설정. McpAgent에서 사용한다."""
    server_url: str
    headers: dict[str, str] = Field(default_factory=dict)


class AgentNodeRequest(BaseModel):
    nodeId: str
    workflowExecutionId: Optional[str] = None   # Spring Boot workflow_executions 연결용
    promptTemplateId: Optional[str] = None
    renderedPrompt: str
    systemMessage: Optional[str] = None
    agentType: str = "simple"                    # "simple" | "react"
    model: Optional[str] = None
    parameters: Optional[ModelParameters] = None
    tools: Optional[List[Dict[str, Any]]] = None
    workflowContext: Optional[Dict[str, Any]] = None
    mcp_servers: Optional[List[McpServerConfig]] = None  # 커스텀 MCP 서버 목록 (신규)


class ActionExecutionRequest(BaseModel):
    """POST /v1/actions/execute 요청. config는 노드 tools[0].config 그대로라 함수 인자 외의 키
    (webhookCredentialId, _names 등)도 들어 있다 — 실행 시 시그니처로 거른다."""
    nodeId: str
    toolKey: str
    config: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("config", mode="before")
    @classmethod
    def _null_config_is_empty(cls, value):
        return {} if value is None else value

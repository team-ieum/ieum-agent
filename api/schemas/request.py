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
    # 노드 config.outputFields 그대로(BE는 검사 없이 싣는다). 규칙 검사는 core.output_fields 한 곳 —
    # 여기서 타입을 좁히면 위반 값이 422가 돼 INVALID_OUTPUT_FIELDS(재시도 안 함)로 분류되지 않는다.
    outputFields: Optional[Any] = None


class ActionExecutionRequest(BaseModel):
    """POST /v1/actions/execute 요청. config는 노드 config의 칸 값 그대로라(BE가 brand만 뺀다, spec §4)
    함수 인자 외의 키(webhookCredentialId, _names 등)도 들어 있다 — 실행 시 시그니처로 거른다."""
    nodeId: str
    toolKey: str
    config: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("config", mode="before")
    @classmethod
    def _null_config_is_empty(cls, value):
        return {} if value is None else value

from pydantic import BaseModel, Field
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

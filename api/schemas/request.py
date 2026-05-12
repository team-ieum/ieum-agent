from pydantic import BaseModel
from typing import Optional, List, Dict, Any


class ModelParameters(BaseModel):
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


class AgentNodeRequest(BaseModel):
    nodeId: str
    promptTemplateId: Optional[str] = None
    renderedPrompt: str
    systemMessage: Optional[str] = None
    agentType: Optional[str] = "simple"   # "simple" | "react"
    model: Optional[str] = None           # 없으면 provider 기본값 사용
    parameters: Optional[ModelParameters] = None
    tools: Optional[List[Dict[str, Any]]] = None
    workflowContext: Optional[Dict[str, Any]] = None

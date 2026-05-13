from pydantic import BaseModel
from typing import Optional, List, Dict, Any


class GenerateWorkflowRequest(BaseModel):
    prompt: str


class WorkflowNode(BaseModel):
    id: str
    type: str                          # TRIGGER | AI | HTTP | CONDITION | TRANSFORM
    label: str
    config: Dict[str, Any]


class WorkflowEdge(BaseModel):
    source: str
    target: str
    conditionType: Optional[str] = None   # CONDITION 노드 분기: "true" | "false" | null


class GenerateWorkflowResponse(BaseModel):
    nodes: List[WorkflowNode]
    edges: List[WorkflowEdge]
    rawPrompt: str                     # 생성에 사용된 원본 사용자 요청

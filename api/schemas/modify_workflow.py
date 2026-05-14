from pydantic import BaseModel
from typing import List
from api.schemas.generate_workflow import WorkflowNode, WorkflowEdge


class ModifyWorkflowRequest(BaseModel):
    prompt: str
    currentNodes: List[WorkflowNode]
    currentEdges: List[WorkflowEdge]


class ModifyWorkflowResponse(BaseModel):
    nodes: List[WorkflowNode]
    edges: List[WorkflowEdge]
    rawPrompt: str
    changeDescription: str

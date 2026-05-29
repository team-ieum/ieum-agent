from pydantic import BaseModel, model_validator
from typing import Optional, List, Dict, Any


class GenerateWorkflowRequest(BaseModel):
    prompt: str


class WorkflowNode(BaseModel):
    id: str
    type: str                          # TRIGGER | AI | HTTP | CONDITION | TRANSFORM
    label: str
    config: Dict[str, Any]

    @model_validator(mode='after')
    def validate_config_by_type(self) -> 'WorkflowNode':
        node_type = self.type.upper()
        cfg = self.config or {}

        if node_type == "TRIGGER":
            trigger_type = cfg.get("triggerType")
            if not trigger_type:
                raise ValueError("TRIGGER 노드의 config에는 triggerType이 필수입니다.")
            if trigger_type not in ("SCHEDULE", "MANUAL", "WEBHOOK"):
                raise ValueError(f"유효하지 않은 triggerType입니다: {trigger_type}")
            if trigger_type == "SCHEDULE":
                cron = cfg.get("cron")
                if not cron or not isinstance(cron, str):
                    raise ValueError("SCHEDULE 트리거 노드에는 cron 표현식이 필수입니다.")

        elif node_type == "AI":
            if "llmProvider" not in cfg:
                raise ValueError("AI 노드의 config에는 llmProvider가 필수입니다.")
            agent_type = cfg.get("agentType")
            if agent_type and agent_type not in ("simple", "react"):
                raise ValueError(f"유효하지 않은 agentType입니다: {agent_type}")
            tools = cfg.get("tools")
            if tools is not None and not isinstance(tools, list):
                raise ValueError("AI 노드의 tools는 리스트 형식이어야 합니다.")

        elif node_type == "HTTP":
            method = cfg.get("method")
            if not method:
                raise ValueError("HTTP 노드의 config에는 method가 필수입니다.")
            if method.upper() not in ("GET", "POST", "PUT", "DELETE"):
                raise ValueError(f"유효하지 않은 HTTP method입니다: {method}")
            url = cfg.get("url")
            if not url or not isinstance(url, str) or not (url.startswith("http://") or url.startswith("https://")):
                raise ValueError("HTTP 노드에는 올바른 url이 필수입니다.")

        elif node_type == "CONDITION":
            for field in ("operator", "leftValue", "rightValue"):
                if field not in cfg:
                    raise ValueError(f"CONDITION 노드의 config에는 {field}가 필수입니다.")

        elif node_type == "TRANSFORM":
            mappings = cfg.get("mappings")
            if mappings is None or not isinstance(mappings, dict):
                raise ValueError("TRANSFORM 노드에는 mappings(dict)가 필수입니다.")

        else:
            raise ValueError(f"유효하지 않은 노드 type입니다: {self.type}")

        return self


class WorkflowEdge(BaseModel):
    source: str
    target: str
    conditionType: Optional[str] = None   # CONDITION 노드 분기: "true" | "false" | null


class GenerateWorkflowResponse(BaseModel):
    nodes: List[WorkflowNode]
    edges: List[WorkflowEdge]
    rawPrompt: str                     # 생성에 사용된 원본 사용자 요청


class PlanNode(BaseModel):
    id: str
    type: str                          # TRIGGER | AI | HTTP | CONDITION | TRANSFORM
    role: str                          # 노드가 수행할 구체적 역할 설명
    description: str                   # 상세 동작 설명
    tools: List[str] = []              # AI 노드가 사용할 도구 키 목록 (예: ["builtin:web_search"]). AI 노드가 아니면 빈 리스트.


class PlanEdge(BaseModel):
    source: str
    target: str


class WorkflowPlanSchema(BaseModel):
    nodes: List[PlanNode]
    edges: List[PlanEdge]
    justification: str                 # 이 계획을 수립한 타당성 및 논리 설명

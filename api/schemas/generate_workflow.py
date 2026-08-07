from pydantic import BaseModel, model_validator
from typing import Optional, List, Dict, Any


class McpServerMeta(BaseModel):
    """생성 단계에서 Planner에 노출하는 사용자 보유 MCP 서버 메타데이터.
    serverUrl/헤더 등 민감 정보는 제외하고, 매칭·식별에 필요한 정보만 담는다."""
    catalogId: str
    name: str
    description: Optional[str] = None


class WebhookMeta(BaseModel):
    """생성 단계에서 Designer에 노출하는 사용자 보유 Slack/Discord 웹훅 자격증명 메타데이터.
    webhook URL 등 민감 정보는 제외하고, 매칭·식별에 필요한 정보만 담는다."""
    webhookCredentialId: str
    provider: str
    displayName: Optional[str] = None


class GenerateWorkflowRequest(BaseModel):
    prompt: str
    # 사용자가 보유한 MCP 서버 카탈로그 메타(이름/설명/catalogId). backend가 채워 보낸다.
    # 비어 있으면 생성 단계에서 MCP를 배정하지 않는다(환각 방지).
    available_mcp_servers: Optional[List[McpServerMeta]] = None


class WorkflowNodeDraft(BaseModel):
    """LLM이 생성하는 노드 초안. 구조는 만들지 않고 templateId 선택 + 가변값(slots)만 채운다.
    시스템이 template_registry.hydrate_node로 완성된 WorkflowNode로 변환한다."""
    id: Optional[str] = None
    templateId: str
    slots: Dict[str, Any] = {}


class WorkflowNode(BaseModel):
    id: str
    type: str                          # TRIGGER | AI | HTTP | CONDITION | TRANSFORM
    label: str
    # 노드 카드에 표시할 사용자용 자연어 설명. 템플릿의 description 슬롯이 필수로 채우지만,
    # description 도입 이전에 저장된 워크플로우가 수정/채팅 요청으로 되돌아오므로 기본값을 둔다.
    description: str = ""
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
    templateId: str                    # 레지스트리 템플릿 id (Planner가 카탈로그에서 선택). node_type/도구는 템플릿이 결정.
    role: str                          # 노드가 수행할 구체적 역할 설명
    description: str                   # 상세 동작 설명


class PlanEdge(BaseModel):
    source: str
    target: str


class WorkflowPlanSchema(BaseModel):
    nodes: List[PlanNode]
    edges: List[PlanEdge]
    justification: str                 # 이 계획을 수립한 타당성 및 논리 설명

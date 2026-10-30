from pydantic import BaseModel, Field, field_validator, model_validator
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


# CONDITION config 키의 구표기 → 표준 표기(IEUM-AI-55). FE 명세와 BE executor가 보는 이름은
# left/right이고 agent는 이제 그 이름만 만든다. 다만 구표기로 저장된 워크플로우가 수정 요청
# (/v1/chat의 currentNodes)으로 되돌아오므로, 노드 입출력의 단일 관문인
# 이 스키마에서 한 번만 표준 표기로 옮긴다. 옮기기만 하므로 출력에는 구표기가 남지 않는다.
_LEGACY_CONDITION_KEYS = {"leftValue": "left", "rightValue": "right"}

# BE workflow-core TriggerType.java 미러. 레지스트리에서 파생하지 않는다(스키마는 BE 계약을 따른다) —
# 레지스트리 TRIGGER 템플릿의 trigger_type이 여기 있는지는 template_registry 로더가 검사한다.
# tuple인 이유: frozenset이면 list 같은 unhashable 값의 `in` 검사가 TypeError(→ 500)가 된다.
TRIGGER_TYPES = ("SCHEDULE", "MANUAL", "WEBHOOK", "GMAIL_NEW_EMAIL", "GITHUB_NEW_ISSUE")


class WorkflowNode(BaseModel):
    id: str
    # TRIGGER | AI | HTTP | CONDITION | TRANSFORM | APPROVAL | ACTION. 아는 event면 카탈로그 node_type으로 덮는다
    # (요청 값 무시, spec §4). 모르는 event(BE 카탈로그 ⊇ agent 카탈로그)일 때만 받은 값을 쓴다.
    type: Optional[str] = None
    label: str
    # 노드 카드에 표시할 사용자용 자연어 설명. 템플릿의 description 슬롯이 필수로 채우지만,
    # description 도입 이전에 저장된 워크플로우가 수정/채팅 요청으로 되돌아오므로 기본값을 둔다.
    description: str = ""
    # 카탈로그 이벤트 이름(앱.이벤트, spec §4). 노드의 유일한 정체성 — 없거나 공백이 섞이면 422(BE @NotBlank).
    event: str = Field(pattern=r"^\S+$")
    config: Dict[str, Any]

    @field_validator("description", mode="before")
    @classmethod
    def _null_description_to_empty(cls, v: Any) -> Any:
        """명시적 null을 빈 문자열로 받는다.

        기본값은 '키 부재'만 막는다. BE `NodeView`는 @JsonInclude(NON_NULL)이 아니라서 레거시
        노드의 조회 응답에 "description": null이 실려 나가고, FE가 그대로 currentNodes로
        되보내므로 기본값만으로는 이 PR 이전 저장분의 수정이 전부 422가 된다."""
        return "" if v is None else v

    @model_validator(mode='after')
    def validate_config_by_type(self) -> 'WorkflowNode':
        # 순환 import 회피: template_registry가 이 모듈의 TRIGGER_TYPES를 import한다.
        from core.template_registry import event_entry, load_templates
        if (load_templates().get(self.event) or {}).get("preset"):
            raise ValueError(f"프리셋 id는 event가 아닙니다 — ai.agent를 씁니다: {self.event}")
        entry = event_entry({"event": self.event})
        if entry is not None:
            self.type = entry["node_type"]
        elif not self.type:
            raise ValueError(f"모르는 event 노드에는 type이 필요합니다: {self.event}")
        node_type = self.type.upper()
        cfg = self.config or {}

        if node_type == "TRIGGER":
            if entry is not None and entry.get("trigger_type") == "SCHEDULE":
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
            for legacy, canonical in _LEGACY_CONDITION_KEYS.items():
                if legacy in cfg:
                    value = cfg.pop(legacy)
                    cfg.setdefault(canonical, value)  # 둘 다 있으면 표준 표기가 이긴다
            for field in ("operator", "left", "right"):
                if field not in cfg:
                    raise ValueError(f"CONDITION 노드의 config에는 {field}가 필수입니다.")

        elif node_type == "TRANSFORM":
            mappings = cfg.get("mappings")
            if mappings is None or not isinstance(mappings, dict):
                raise ValueError("TRANSFORM 노드에는 mappings(dict)가 필수입니다.")

        elif node_type == "APPROVAL":
            # 사람 승인 게이트(IEUM-BE-45). BE 런타임이 여기서 멈출 뿐 실행하지 않는다.
            # config는 승인자에게 보여 줄 message 하나이고 선택이다.
            message = cfg.get("message")
            if message is not None and not isinstance(message, str):
                raise ValueError("APPROVAL 노드의 message는 문자열이어야 합니다.")

        elif node_type == "ACTION":
            pass  # config는 칸 값뿐(spec §4). 칸 검사는 정적 검증이 카탈로그로 한다.

        else:
            raise ValueError(f"유효하지 않은 노드 type입니다: {self.type}")

        return self


class WorkflowEdge(BaseModel):
    source: str
    target: str
    conditionType: Optional[str] = None   # CONDITION 노드 분기: "true" | "false" | null


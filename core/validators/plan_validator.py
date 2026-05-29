import logging
from typing import List, Dict, Any
from api.schemas.generate_workflow import WorkflowPlanSchema

logger = logging.getLogger(__name__)

class PlanValidationError(ValueError):
    """플랜 검증 실패 시 발생하는 예외"""
    pass

class PlanValidator:
    """기획(Plan) 단계의 JSON 구조 및 설계 규칙 검증기"""

    # AI 전용 도구가 지원되는 서비스 이름 모음 (HTTP 노드로 계획 수립 금지)
    PROHIBITED_SERVICES = {"NOTION", "SLACK", "DISCORD", "GITHUB", "GOOGLE", "GMAIL", "SHEETS", "CALENDAR", "DRIVE"}

    @classmethod
    def _validate_plan_tool_names(cls, tools, node_id: str) -> None:
        """계획 AI 노드의 tools 이름이 실행기 레지스트리(_TOOL_MAP)에 존재하는지 검증한다.
        프리픽스 누락('notion_create_page' 등)이나 오타를 계획 단계에서 차단한다."""
        if not tools:
            return
        # tools 패키지는 google.adk를 최상위에서 import하므로 lazy import로 검증 비용/순환을 회피한다.
        from tools import _TOOL_MAP
        allowed = set(_TOOL_MAP.keys()) | {"mcp"}
        for name in tools:
            if name not in allowed:
                hint = f" '{name}'은(는) 'builtin:{name}' 형식이어야 합니다." if f"builtin:{name}" in allowed else ""
                raise PlanValidationError(
                    f"AI 계획 노드 '{node_id}'의 도구 이름 '{name}'이(가) 유효하지 않습니다."
                    f"{hint} 사용 가능한 도구 이름만 지정하십시오."
                )

    @classmethod
    def validate(cls, plan: WorkflowPlanSchema) -> None:
        """기획된 구조(Nodes, Edges)의 설계 규칙을 검증한다."""
        if not plan.nodes:
            raise PlanValidationError("기획된 플랜에 노드가 존재하지 않습니다.")

        node_ids = set()
        trigger_count = 0
        first_node_type = plan.nodes[0].type.upper() if plan.nodes else None

        # 1. 노드 정합성 체크
        for idx, node in enumerate(plan.nodes):
            nid = node.id
            ntype = node.type.upper()

            if not nid:
                raise PlanValidationError(f"{idx}번째 계획 노드에 id가 없습니다.")
            if nid in node_ids:
                raise PlanValidationError(f"계획 단계에서 중복된 노드 ID가 존재합니다: {nid}")
            node_ids.add(nid)

            if ntype == "TRIGGER":
                trigger_count += 1

            # AI 노드가 계획한 도구 이름이 실제 실행기 레지스트리에 존재하는지 검증
            elif ntype == "AI":
                cls._validate_plan_tool_names(node.tools, nid)

            # HTTP 노드에 도구 전용 서비스를 매핑하려고 했는지 체크
            elif ntype == "HTTP":
                role_upper = node.role.upper()
                desc_upper = node.description.upper()
                for service in cls.PROHIBITED_SERVICES:
                    if service in role_upper or service in desc_upper:
                        raise PlanValidationError(
                            f"HTTP 계획 노드 '{nid}'에서 전용 도구 서비스({service})를 직접 호출하도록 계획되었습니다. "
                            f"해당 서비스는 직접 HTTP 호출하지 말고, 도구를 포함한 AI 노드로 설계를 수정하십시오."
                        )

        # 2. TRIGGER 검증
        if trigger_count == 0:
            raise PlanValidationError("계획상 워크플로우에 TRIGGER 노드가 포함되어 있지 않습니다.")
        if trigger_count > 1:
            raise PlanValidationError(f"계획상 TRIGGER 노드는 1개만 허용됩니다. 현재 {trigger_count}개가 설계되었습니다.")
        if first_node_type != "TRIGGER":
            raise PlanValidationError("워크플로우 계획은 반드시 TRIGGER 노드로 시작해야 합니다. 첫 번째 노드가 TRIGGER가 아닙니다.")

        # 3. 엣지 무결성 검증
        for idx, edge in enumerate(plan.edges):
            source = edge.source
            target = edge.target

            if not source or not target:
                raise PlanValidationError(f"{idx}번째 계획 엣지에 source 또는 target이 누락되었습니다.")
            if source not in node_ids:
                raise PlanValidationError(f"계획 엣지({source} ➡️ {target})의 출발지 '{source}' 노드가 존재하지 않습니다.")
            if target not in node_ids:
                raise PlanValidationError(f"계획 엣지({source} ➡️ {target})의 도착지 '{target}' 노드가 존재하지 않습니다.")

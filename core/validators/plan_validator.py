import logging
from api.schemas.generate_workflow import WorkflowPlanSchema
from core.template_registry import template_ids, node_type_of_template

logger = logging.getLogger(__name__)

# 동적 MCP 서버 도구를 부착하는 의사 템플릿. 사용 가능한 MCP 카탈로그가 있을 때만 선택 가능.
_MCP_TEMPLATE_ID = "ai.mcp"


class PlanValidationError(ValueError):
    """플랜 검증 실패 시 발생하는 예외"""
    pass


class PlanValidator:
    """기획(Plan) 단계 검증기. Planner가 고른 templateId 기반으로 구조를 검증한다.

    노드의 node_type/도구는 templateId가 결정하므로(레지스트리 SSOT), Plan 검증에서는 도구 이름
    검증·정규화를 하지 않는다. 가변값(slots)은 이후 Builder가 채우고 hydrate/WorkflowValidator가 검증한다.

    아래 도구 이름 정규화 유틸(_is_runtime_subagent_tool/_canonicalize_tool_name 등)은 아직
    구 계약(full-node)을 쓰는 chat 경로(core/workflow_chat._canonicalize_node_tools)가 참조하므로
    보존한다. chat이 templateId+slots로 이전되면 함께 제거한다.
    """

    _GITHUB_TOOL_PREFIX = "github"

    # 서비스→도구 결정론 매핑(별칭 사전). LLM이 흔히 쓰는 변형/별칭을 정확한 _TOOL_MAP 키로 환원한다.
    _TOOL_ALIASES = {
        "search": "builtin:web_search",
        "websearch": "builtin:web_search",
        "web": "builtin:web_search",
        "google_search": "builtin:web_search",
        "http": "builtin:http_fetch",
        "fetch": "builtin:http_fetch",
        "httprequest": "builtin:http_fetch",
    }

    @classmethod
    def _is_runtime_subagent_tool(cls, name: str) -> bool:
        """실행 시 서브 에이전트가 처리하는 도구 이름인지 판별한다(노드 tools에서 제거 대상).
        - '*_agent'(web_agent, transform_agent 등) / 'github*'(github_agent 및 browse 도구)."""
        if not isinstance(name, str):
            return False
        lname = name.strip().lower()
        if ":" in lname:
            lname = lname.split(":", 1)[1]
        return lname.endswith("_agent") or lname.startswith(cls._GITHUB_TOOL_PREFIX)

    @classmethod
    def _canonicalize_tool_name(cls, name: str, allowed: set) -> str | None:
        """도구 이름을 실행기 레지스트리의 정확한 키로 환원한다. 환원 불가하면 None."""
        if name in allowed:
            return name
        prefixed = f"builtin:{name}"
        if prefixed in allowed:
            return prefixed
        alias = cls._TOOL_ALIASES.get(name.strip().lower())
        if alias and alias in allowed:
            return alias
        return None

    @classmethod
    def validate(cls, plan: WorkflowPlanSchema, allowed_mcp_catalog_ids: set | None = None) -> None:
        """기획된 구조(Nodes, Edges)의 설계 규칙을 검증한다.
        allowed_mcp_catalog_ids: 생성 단계에서 허용되는 MCP 카탈로그 ID 집합(없으면 MCP 전면 차단)."""
        allowed_mcp_catalog_ids = allowed_mcp_catalog_ids or set()
        if not plan.nodes:
            raise PlanValidationError("기획된 플랜에 노드가 존재하지 않습니다.")

        valid_ids = template_ids()
        node_ids = set()
        trigger_count = 0
        first_node_type = None

        for idx, node in enumerate(plan.nodes):
            nid = node.id
            if not nid:
                raise PlanValidationError(f"{idx}번째 계획 노드에 id가 없습니다.")
            if nid in node_ids:
                raise PlanValidationError(f"계획 단계에서 중복된 노드 ID가 존재합니다: {nid}")
            node_ids.add(nid)

            tid = node.templateId
            if tid not in valid_ids:
                raise PlanValidationError(
                    f"계획 노드 '{nid}'의 templateId '{tid}'가 레지스트리에 존재하지 않습니다. "
                    f"사용 가능한 templateId만 지정하십시오."
                )

            # MCP 의사 템플릿은 사용 가능한 카탈로그가 있을 때만 허용한다(환각 차단).
            if tid == _MCP_TEMPLATE_ID and not allowed_mcp_catalog_ids:
                raise PlanValidationError(
                    f"계획 노드 '{nid}'가 MCP 템플릿('{tid}')을 사용하지만 사용 가능한 MCP 서버가 없습니다. "
                    f"MCP 서버가 제공되지 않으면 MCP 노드를 계획하지 마십시오."
                )

            ntype = node_type_of_template(tid)
            if ntype == "TRIGGER":
                trigger_count += 1
            if idx == 0:
                first_node_type = ntype

        # TRIGGER 검증
        if trigger_count == 0:
            raise PlanValidationError("계획상 워크플로우에 TRIGGER 노드가 포함되어 있지 않습니다.")
        if trigger_count > 1:
            raise PlanValidationError(f"계획상 TRIGGER 노드는 1개만 허용됩니다. 현재 {trigger_count}개가 설계되었습니다.")
        if first_node_type != "TRIGGER":
            raise PlanValidationError("워크플로우 계획은 반드시 TRIGGER 노드로 시작해야 합니다. 첫 번째 노드가 TRIGGER가 아닙니다.")

        # 엣지 무결성 검증
        for idx, edge in enumerate(plan.edges):
            source = edge.source
            target = edge.target
            if not source or not target:
                raise PlanValidationError(f"{idx}번째 계획 엣지에 source 또는 target이 누락되었습니다.")
            if source not in node_ids:
                raise PlanValidationError(f"계획 엣지({source} ➡️ {target})의 출발지 '{source}' 노드가 존재하지 않습니다.")
            if target not in node_ids:
                raise PlanValidationError(f"계획 엣지({source} ➡️ {target})의 도착지 '{target}' 노드가 존재하지 않습니다.")

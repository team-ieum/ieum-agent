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

    # 실행 시 자동 부착되는 서브 에이전트(web/comm/transform/notion/google/github/mcp_agent)는
    # 노드 tools 키가 아니다. Planner가 'transform_agent', 'github_list_pull_requests' 등을
    # tools에 넣으면 _TOOL_MAP에 없어 검증에 걸리므로, 검증 단계에서 조용히 제거한다.
    # (AI 노드는 prompt + agentType:react만 있으면 실행 시 해당 서브 에이전트가 처리한다.)
    _GITHUB_TOOL_PREFIX = "github"

    @classmethod
    def _is_runtime_subagent_tool(cls, name: str) -> bool:
        """실행 시 서브 에이전트가 처리하는 도구 이름인지 판별한다(노드 tools에서 제거 대상).
        - '*_agent'(web_agent, transform_agent 등 서브 에이전트 이름)
        - 'github*'(github_agent 및 github_list_* browse 도구)
        'builtin:github_list_pull_requests'처럼 프리픽스가 붙어도 인식하도록 프리픽스를 떼고 판별한다."""
        if not isinstance(name, str):
            return False
        lname = name.strip().lower()
        if ":" in lname:
            lname = lname.split(":", 1)[1]
        return lname.endswith("_agent") or lname.startswith(cls._GITHUB_TOOL_PREFIX)

    # 서비스→도구 결정론 매핑(별칭 사전). LLM이 흔히 쓰는 변형/별칭을 정확한 _TOOL_MAP 키로 환원한다.
    # 프리픽스 누락(맨이름)은 코드에서 'builtin:{name}'으로 자동 보정하므로 여기 명시하지 않고,
    # 프리픽스 보정만으로 환원되지 않는 명백한 별칭만 등록한다. (값은 반드시 _TOOL_MAP의 실제 키여야 함)
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
    def _canonicalize_tool_name(cls, name: str, allowed: set) -> str | None:
        """도구 이름을 실행기 레지스트리의 정확한 키로 환원한다.
        환원 불가능하면 None을 반환한다. (이름 환각 차단을 위한 결정론 매핑)"""
        if name in allowed:
            return name
        # 1) 프리픽스 누락 자동 보정: 'notion_create_page' → 'builtin:notion_create_page'
        prefixed = f"builtin:{name}"
        if prefixed in allowed:
            return prefixed
        # 2) 별칭 사전 환원
        alias = cls._TOOL_ALIASES.get(name.strip().lower())
        if alias and alias in allowed:
            return alias
        return None

    @classmethod
    def _validate_plan_tool_names(cls, node, node_id: str, allowed_mcp_catalog_ids: set) -> None:
        """계획 AI 노드의 tools를 정확한 _TOOL_MAP 키로 결정론적으로 환원(in-place)하고 검증한다.
        프리픽스 누락·별칭은 코드가 정확 키로 교정하며, 환원 불가능한 이름만 차단한다.
        교정된 tools는 Builder로 그대로 전달되어 노드 config의 도구 이름 환각을 원천 차단한다.

        MCP 도구는 'mcp:<catalogId>' 형식으로 표현하며, catalogId가 allowed_mcp_catalog_ids에
        포함된 경우에만 허용한다. (생성 요청에 카탈로그가 없으면 allowed가 비어 모든 MCP가 차단됨)"""
        if not node.tools:
            return
        # tools 패키지는 google.adk를 최상위에서 import하므로 lazy import로 검증 비용/순환을 회피한다.
        from tools import _TOOL_MAP
        allowed = set(_TOOL_MAP.keys())

        canonical = []
        for name in node.tools:
            # 서브 에이전트(github/transform/web 등)는 실행 시 자동 처리되므로 tools에서 제거(환각 방지)
            if cls._is_runtime_subagent_tool(name):
                continue
            # MCP 도구: 'mcp' 또는 'mcp:<catalogId>'
            if name == "mcp" or name.startswith("mcp:"):
                catalog_id = name[len("mcp:"):] if name.startswith("mcp:") else ""
                if catalog_id and catalog_id in allowed_mcp_catalog_ids:
                    canonical.append(f"mcp:{catalog_id}")
                    continue
                raise PlanValidationError(
                    f"AI 계획 노드 '{node_id}'의 MCP 도구 '{name}'을(를) 사용할 수 없습니다. "
                    f"사용 가능한 MCP 서버(catalogId)만 'mcp:<catalogId>' 형식으로 지정하십시오."
                )
            resolved = cls._canonicalize_tool_name(name, allowed)
            if resolved is None:
                raise PlanValidationError(
                    f"AI 계획 노드 '{node_id}'의 도구 이름 '{name}'이(가) 유효하지 않습니다. "
                    f"사용 가능한 도구 이름만 지정하십시오."
                )
            canonical.append(resolved)
        node.tools = canonical

    @classmethod
    def validate(cls, plan: WorkflowPlanSchema, allowed_mcp_catalog_ids: set | None = None) -> None:
        """기획된 구조(Nodes, Edges)의 설계 규칙을 검증한다.
        allowed_mcp_catalog_ids: 생성 단계에서 허용되는 MCP 카탈로그 ID 집합(없으면 MCP 전면 차단)."""
        allowed_mcp_catalog_ids = allowed_mcp_catalog_ids or set()
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

            # AI 노드가 계획한 도구 이름을 정확 키로 환원(in-place)하고 검증
            elif ntype == "AI":
                cls._validate_plan_tool_names(node, nid, allowed_mcp_catalog_ids)

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

import re
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class WorkflowValidationError(ValueError):
    """워크플로우 검증 실패 시 발생하는 예외"""
    pass

class WorkflowValidator:
    """최종 생성/수정된 워크플로우 JSON의 의미 및 정적 규칙 검증기"""

    # AI 노드 config 값 화이트리스트 (환각 방지)
    ALLOWED_AGENT_TYPES = {"react", "simple"}
    ALLOWED_LLM_PROVIDERS = {"CLAUDE", "OPENAI", "GEMINI"}

    # AI 전용 도구가 매핑되어 제공되어야 하는 외부 연동 API 도메인 (HTTP 노드 직접 사용 금지)
    PROHIBITED_HTTP_DOMAINS = [
        r"api\.notion\.com",
        r"slack\.com/api",
        r"discord\.com/api",
        r"discordapp\.com/api",
        r"api\.github\.com",
        r"googleapis\.com",
        r"gmail\.googleapis\.com"
    ]

    @classmethod
    def validate(cls, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]],
                 allowed_mcp_catalog_ids: set | None = None) -> None:
        """워크플로우의 무결성 및 설계 규칙을 검증한다.
        allowed_mcp_catalog_ids: 생성 단계에서 허용되는 MCP 카탈로그 ID 집합(없으면 MCP 전면 차단)."""
        allowed_mcp_catalog_ids = allowed_mcp_catalog_ids or set()
        if not nodes:
            raise WorkflowValidationError("워크플로우에 노드가 존재하지 않습니다.")

        if nodes[0].get("type", "").upper() != "TRIGGER":
            raise WorkflowValidationError(
                f"워크플로우는 반드시 TRIGGER 노드로 시작해야 합니다. 첫 번째 노드('{nodes[0].get('id')}')의 타입이 TRIGGER가 아닙니다."
            )

        node_ids = set()
        trigger_count = 0
        trigger_node_id = None

        # 1. 노드 스키마 및 고유성, TRIGGER 검증
        for idx, node in enumerate(nodes):
            nid = node.get("id")
            ntype = node.get("type")
            config = node.get("config", {})

            if not nid:
                raise WorkflowValidationError(f"{idx}번째 노드에 id 필드가 누락되었습니다.")
            if nid in node_ids:
                raise WorkflowValidationError(f"중복된 노드 ID가 존재합니다: {nid}")
            node_ids.add(nid)

            if not ntype:
                raise WorkflowValidationError(f"노드 '{nid}'에 type 필드가 누락되었습니다.")

            # TRIGGER 노드 개수 검증
            if ntype.upper() == "TRIGGER":
                trigger_count += 1
                trigger_node_id = nid
                
                # SCHEDULE 트리거의 상세 cron 검증
                trigger_type = config.get("triggerType")
                if trigger_type == "SCHEDULE":
                    cron = config.get("cron")
                    if not cron:
                        raise WorkflowValidationError(f"SCHEDULE 트리거 노드 '{nid}'에 cron 표현식이 누락되었습니다.")
                    cls._validate_cron(cron, nid)

            # HTTP 노드 검증 - Notion, Google, Slack, Discord 등 AI 전용 도구를 써야 하는 서비스를 직접 HTTP로 불렀는지 체크
            elif ntype.upper() == "HTTP":
                url = config.get("url") or ""
                for domain_pattern in cls.PROHIBITED_HTTP_DOMAINS:
                    if re.search(domain_pattern, url, re.IGNORECASE):
                        raise WorkflowValidationError(
                            f"HTTP 노드 '{nid}'에서 외부 연동 도메인({url})으로 직접 요청을 전송하고 있습니다. "
                            f"Slack, Discord, Notion, GitHub, Google 등은 직접 HTTP 노드로 호출하지 말고 "
                            f"전용 도구가 바인딩된 AI 노드를 구성하여 수행하도록 설계하십시오."
                        )

            # AI 노드 검증 - 변수 참조 문법이 올바른지 프롬프트 내부 점검
            elif ntype.upper() == "AI":
                prompt = config.get("prompt") or ""
                system_msg = config.get("systemMessage") or ""
                cls._validate_variable_references(prompt, nid)
                cls._validate_variable_references(system_msg, nid)
                cls._validate_tool_names(config.get("tools"), nid, allowed_mcp_catalog_ids)
                cls._validate_ai_node_fields(config, nid)

            # 노드 타입 무관: 템플릿 기반 config 필드 화이트리스트 검증
            cls._validate_config_fields(node, nid)

        if trigger_count == 0:
            raise WorkflowValidationError("워크플로우는 반드시 1개의 TRIGGER 노드로 시작해야 합니다. TRIGGER 노드가 발견되지 않았습니다.")
        if trigger_count > 1:
            raise WorkflowValidationError(f"워크플로우에는 오직 1개의 TRIGGER 노드만 허용됩니다. 현재 {trigger_count}개가 존재합니다.")

        # 2. 엣지 연결 정합성 검증
        for idx, edge in enumerate(edges):
            source = edge.get("source")
            target = edge.get("target")

            if not source or not target:
                raise WorkflowValidationError(f"{idx}번째 엣지에 source 또는 target이 누락되었습니다.")
            if source not in node_ids:
                raise WorkflowValidationError(f"엣지({source} ➡️ {target})의 출발 노드 ID '{source}'가 존재하지 않습니다.")
            if target not in node_ids:
                raise WorkflowValidationError(f"엣지({source} ➡️ {target})의 도착 노드 ID '{target}'가 존재하지 않습니다.")

            # TRIGGER 노드로 들어가는 edge 차단 (TRIGGER는 오직 출발만 가능)
            if target == trigger_node_id:
                raise WorkflowValidationError(f"TRIGGER 노드 '{trigger_node_id}'는 다른 노드의 도착지(target)가 될 수 없습니다.")

        # 3. 고아 노드 및 그래프 연결 구조 분석
        cls._validate_graph_connectivity(nodes, edges, trigger_node_id)

        # 4. 변수 참조 대상 노드의 존재성 및 선행(upstream) 관계 검증
        cls._validate_reference_targets(nodes, edges)

    @classmethod
    def _validate_cron(cls, cron: str, node_id: str) -> None:
        """표준 5필드 크론식 구조 검사 (기본 공백 분할 5개 필드)"""
        parts = cron.strip().split()
        if len(parts) != 5:
            raise WorkflowValidationError(
                f"트리거 노드 '{node_id}'의 cron 표현식 '{cron}'은 표준 5필드(분 시 일 월 요일) 형식이어야 합니다."
            )
        
        # 간단한 형식적 정규식 체크 (숫자, *, ?, /, -, , 등)
        cron_part_pattern = r"^[0-9\*\/\,\-\?]+$"
        for i, part in enumerate(parts):
            if not re.match(cron_part_pattern, part):
                raise WorkflowValidationError(
                    f"트리거 노드 '{node_id}'의 cron 표현식 중 '{part}' 부분은 올바른 cron 구문이 아닙니다."
                )

    @classmethod
    def _allowed_tool_names(cls) -> set:
        """생성 단계에서 AI 노드에 허용되는 도구 이름 집합을 반환한다. _TOOL_MAP을 SSOT로 사용한다.

        주의: 커스텀 'mcp' 도구는 사용자별 MCP 서버 카탈로그(server_url 등)가 생성 시점에 주입되지
        않으므로, 생성 단계에서 자동 배정하면 환각(존재하지 않는 서버)을 유발한다. 따라서 생성 검증에서는
        'mcp'를 허용하지 않는다. MCP 연동은 생성 후 노드 편집(modify) 단계에서 추가한다."""
        # tools 패키지는 google.adk를 최상위에서 import하므로 lazy import로 검증 비용/순환을 회피한다.
        from tools import _TOOL_MAP
        return set(_TOOL_MAP.keys())

    @classmethod
    def _validate_tool_names(cls, tools, node_id: str, allowed_mcp_catalog_ids: set | None = None) -> None:
        """AI 노드의 tools 항목 이름이 실제 실행기 레지스트리에 존재하는지 검증한다.
        프리픽스 누락('notion_create_page' 등)이나 오타를 차단한다.
        MCP 도구는 config.catalogId가 allowed_mcp_catalog_ids에 있을 때만 허용한다."""
        if not tools:
            return
        if not isinstance(tools, list):
            raise WorkflowValidationError(
                f"AI 노드 '{node_id}'의 tools는 리스트 형식이어야 합니다."
            )

        allowed_mcp_catalog_ids = allowed_mcp_catalog_ids or set()
        allowed = cls._allowed_tool_names()
        for tool in tools:
            name = tool.get("name") if isinstance(tool, dict) else tool
            if not name:
                raise WorkflowValidationError(
                    f"AI 노드 '{node_id}'의 tools 항목에 name이 누락되었습니다."
                )
            if name == "mcp":
                cfg = tool.get("config") if isinstance(tool, dict) else None
                catalog_id = cfg.get("catalogId") if isinstance(cfg, dict) else None
                if catalog_id and catalog_id in allowed_mcp_catalog_ids:
                    continue
                raise WorkflowValidationError(
                    f"AI 노드 '{node_id}'의 MCP 도구를 사용할 수 없습니다. "
                    f"config.catalogId가 사용 가능한 MCP 서버 목록에 없습니다. "
                    f"(MCP 미보유 시 빌트인 도구만 사용)"
                )
            if name not in allowed:
                hint = ""
                if f"builtin:{name}" in allowed:
                    hint = f" '{name}'은(는) 'builtin:{name}' 형식이어야 합니다."
                raise WorkflowValidationError(
                    f"AI 노드 '{node_id}'의 도구 이름 '{name}'이(가) 유효하지 않습니다."
                    f"{hint} 사용 가능한 도구 이름만 지정하십시오."
                )

    @classmethod
    def _validate_ai_node_fields(cls, config: Dict[str, Any], node_id: str) -> None:
        """AI 노드 config의 enum/필수값을 검증한다.
        agentType(react|simple), llmProvider(CLAUDE|OPENAI|GEMINI) 값 화이트리스트와
        credentialId 빈 문자열 규칙(런타임 주입)을 강제해 환각을 차단한다."""
        if not isinstance(config, dict):
            raise WorkflowValidationError(f"AI 노드 '{node_id}'의 config는 객체(dict) 형식이어야 합니다.")
        agent_type = config.get("agentType")
        if agent_type not in cls.ALLOWED_AGENT_TYPES:
            raise WorkflowValidationError(
                f"AI 노드 '{node_id}'의 agentType '{agent_type}'이(가) 유효하지 않습니다. "
                f"{sorted(cls.ALLOWED_AGENT_TYPES)} 중 하나여야 합니다."
            )

        provider = config.get("llmProvider")
        if provider not in cls.ALLOWED_LLM_PROVIDERS:
            raise WorkflowValidationError(
                f"AI 노드 '{node_id}'의 llmProvider '{provider}'이(가) 유효하지 않습니다. "
                f"{sorted(cls.ALLOWED_LLM_PROVIDERS)} 중 하나여야 합니다."
            )

        # credentialId는 런타임에 백엔드가 주입하므로 빈 문자열이어야 한다.
        # (키 누락은 허용하되, 값이 있으면 반드시 ""여야 한다)
        cred = config.get("credentialId")
        if cred not in (None, ""):
            raise WorkflowValidationError(
                f"AI 노드 '{node_id}'의 credentialId는 빈 문자열(\"\")이어야 합니다(런타임 주입). "
                f"현재 값: '{cred}'"
            )

    @classmethod
    def _validate_config_fields(cls, node: Dict[str, Any], node_id: str) -> None:
        """노드 config의 키가 해당 노드 템플릿의 allowed_config_fields에 속하는지 검증한다.
        템플릿에 없는 필드를 날조하는 환각을 차단한다. 매칭 템플릿이 없으면(예: MCP 전용 노드 등)
        화이트리스트를 적용하지 않는다(false reject 방지)."""
        from core.template_registry import allowed_config_fields_for_node

        config = node.get("config")
        if not isinstance(config, dict):
            return  # config 형식 문제는 타입별 검증 영역
        allowed = allowed_config_fields_for_node(node)
        if allowed is None:
            return
        unknown = set(config.keys()) - allowed
        if unknown:
            raise WorkflowValidationError(
                f"노드 '{node_id}'의 config에 허용되지 않은 필드가 있습니다: {sorted(unknown)}. "
                f"허용 필드: {sorted(allowed)}"
            )

    @classmethod
    def _iter_config_strings(cls, value: Any):
        """config 내의 모든 문자열 값을 재귀적으로 순회한다(dict/list 중첩 포함)."""
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for v in value.values():
                yield from cls._iter_config_strings(v)
        elif isinstance(value, list):
            for v in value:
                yield from cls._iter_config_strings(v)

    @classmethod
    def _validate_reference_targets(cls, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> None:
        """변수 참조({{nodes.X.output.Y}})가 가리키는 노드 X가 실제 존재하고,
        참조하는 노드의 선행(upstream) 노드인지 검증한다.
        형식 검증(_validate_variable_references)과 달리, 존재하지 않는 노드ID 참조와
        하류/형제/자기 자신 참조 같은 데이터 흐름 환각을 차단한다."""
        node_ids = {n.get("id") for n in nodes}

        # 역방향 인접 리스트(선행 노드 맵) 구성
        preds: Dict[Any, list] = {n.get("id"): [] for n in nodes}
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            if target in preds and source in node_ids:
                preds[target].append(source)

        def ancestors(nid: Any) -> set:
            """nid의 모든 조상(선행) 노드 집합을 역방향 BFS로 수집한다."""
            seen: set = set()
            stack = list(preds.get(nid, []))
            while stack:
                cur = stack.pop()
                if cur in seen:
                    continue
                seen.add(cur)
                stack.extend(preds.get(cur, []))
            return seen

        node_ref_pattern = re.compile(r"^nodes\.([a-zA-Z0-9_-]+)\.output\.[a-zA-Z0-9_-]+$")
        ref_pattern = re.compile(r"\{\{(.*?)\}\}")

        for node in nodes:
            nid = node.get("id")
            config = node.get("config", {}) or {}
            anc = None  # 조상 집합은 참조가 실제 있을 때만 lazy 계산
            for text in cls._iter_config_strings(config):
                for raw_ref in ref_pattern.findall(text):
                    m = node_ref_pattern.match(raw_ref.strip())
                    if not m:
                        # 형식 불일치는 _validate_variable_references 책임 영역이므로 여기선 건너뜀
                        continue
                    target = m.group(1)
                    if target not in node_ids:
                        raise WorkflowValidationError(
                            f"노드 '{nid}'가 존재하지 않는 노드 '{target}'를 참조합니다"
                            f"(참조: '{{{{{raw_ref.strip()}}}}}')."
                        )
                    if target == nid:
                        raise WorkflowValidationError(
                            f"노드 '{nid}'가 자기 자신을 참조합니다(참조: '{{{{{raw_ref.strip()}}}}}')."
                        )
                    if anc is None:
                        anc = ancestors(nid)
                    if target not in anc:
                        raise WorkflowValidationError(
                            f"노드 '{nid}'가 선행(upstream) 노드가 아닌 '{target}'를 참조합니다. "
                            f"참조 대상은 반드시 엣지로 연결된 앞선 노드여야 합니다"
                            f"(참조: '{{{{{raw_ref.strip()}}}}}')."
                        )

    @classmethod
    def _validate_variable_references(cls, text: str, node_id: str) -> None:
        """이중 중괄호 변수 참조 구문이 정상적으로 완성되어 있는지 검사 (예: {{nodes.node-1.output.xxx}})"""
        # 중괄호가 하나만 열려있거나 닫혀있는 이상한 패턴 검출
        open_brackets = len(re.findall(r"\{\{", text))
        close_brackets = len(re.findall(r"\}\}", text))
        if open_brackets != close_brackets:
            raise WorkflowValidationError(
                f"AI 노드 '{node_id}'의 프롬프트 내에 이중 중괄호 괄호 짝이 맞지 않습니다. (열림: {open_brackets}, 닫힘: {close_brackets})"
            )

        # 괄호 구문 안의 변수명 포맷 검사
        references = re.findall(r"\{\{(.*?)\}\}", text)
        for ref in references:
            ref_clean = ref.strip()
            # nodes.node-X.output.field 형태여야 함
            if not re.match(r"^nodes\.[a-zA-Z0-9_-]+\.output\.[a-zA-Z0-9_-]+$", ref_clean):
                # 간혹 output 누락이나 nodes 누락 등 오타가 나는 경우 검출
                raise WorkflowValidationError(
                    f"AI 노드 '{node_id}'의 변수 참조 구문 '{{{{{ref_clean}}}}}' 형식이 올바르지 않습니다. "
                    f"반드시 '{{{{nodes.<노드ID>.output.<필드명>}}}}' 구조여야 합니다."
                )

    @classmethod
    def _validate_graph_connectivity(cls, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]], trigger_node_id: str) -> None:
        """트리거 노드로부터 모든 노드로의 도달 가능성 검사 및 고아 노드 방지"""
        adj_list = {n["id"]: [] for n in nodes}
        for edge in edges:
            adj_list[edge["source"]].append(edge["target"])

        # DFS/BFS로 도달 가능한 노드 탐색
        visited = set()
        queue = [trigger_node_id]
        
        while queue:
            curr = queue.pop(0)
            if curr not in visited:
                visited.add(curr)
                queue.extend(adj_list[curr])

        # 도달 불가능한 고아 노드 추출
        all_ids = {n["id"] for n in nodes}
        unreachable = all_ids - visited
        if unreachable:
            raise WorkflowValidationError(
                f"트리거 노드 '{trigger_node_id}'로부터 도달할 수 없는 고아 노드가 발견되었습니다: {list(unreachable)}. "
                f"모든 노드는 트리거 노드로부터 시작하여 엣지로 연결되어 있어야 합니다."
            )

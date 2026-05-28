import re
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class WorkflowValidationError(ValueError):
    """워크플로우 검증 실패 시 발생하는 예외"""
    pass

class WorkflowValidator:
    """최종 생성/수정된 워크플로우 JSON의 의미 및 정적 규칙 검증기"""

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
    def validate(cls, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> None:
        """워크플로우의 무결성 및 설계 규칙을 검증한다."""
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

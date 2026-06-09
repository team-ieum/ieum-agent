import os
import json
import logging

logger = logging.getLogger(__name__)

SKILL_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "ieum-workflow-design")
)

# 항상 주입하는 구조 레퍼런스 파일(작음). 도구 카탈로그(tool-selection.md)와
# 통짜 골든 예시(good-examples.md)는 노드 템플릿 레지스트리(검색층)로 대체되었다.
_ALWAYS_REFERENCE_FILES = [
    "node-types.md",          # 노드 타입/스키마 (공통 기본 지식)
    "workflow-patterns.md",   # 대표 워크플로우 패턴
    "bad-examples.md",        # 안티패턴 (실수 방지)
    "validation-checklist.md",  # 출력 전 자가 점검 항목
]

# 항상 골든 스니펫을 주입하는 구조 노드 타입(작고 거의 모든 워크플로우에 필요).
_ALWAYS_SNIPPET_NODE_TYPES = {"TRIGGER", "CONDITION", "TRANSFORM", "HTTP"}


def _render_template_snippet(tpl: dict) -> str:
    """템플릿 1개를 instruction용 골든 스니펫 텍스트로 렌더한다."""
    snippet = tpl.get("golden_snippet")
    head = f"### {tpl['id']} — {tpl['menu']}"
    if not snippet:
        return head
    body = json.dumps(snippet, ensure_ascii=False, indent=2)
    return f"{head}\n```json\n{body}\n```"


def _select_templates(prompt: str, current_nodes: list | None) -> list:
    """검색층에 주입할 AI 도구 템플릿을 선택한다.
    - prompt 태그 매칭 + (수정 요청 시) 현재 노드가 사용하는 템플릿
    - 매칭 0건이면 AI 템플릿 전체를 폴백 주입한다(recall 우선)."""
    from core.template_registry import select_by_tags, resolve_template_for_node, all_templates

    selected: dict = {}
    for tpl in select_by_tags(prompt):
        if tpl["node_type"] == "AI":
            selected[tpl["id"]] = tpl

    for node in (current_nodes or []):
        tpl = resolve_template_for_node(node)
        if tpl and tpl["node_type"] == "AI":
            selected[tpl["id"]] = tpl

    if not selected:
        # 무매칭 → AI 템플릿 전체 폴백(상위집합, recall 우선)
        return [t for t in all_templates() if t["node_type"] == "AI"]
    return list(selected.values())


def load_design_rules(prompt: str = "", current_nodes: list | None = None) -> str:
    """생성 에이전트 instruction에 주입할 설계 지식을 2층 구조로 빌드한다.

    - 항상층: 구조 레퍼런스(작음) + 도구 메뉴 인덱스 + 구조 노드 골든 스니펫
    - 검색층: 요청(prompt)·현재 노드에 관련된 AI 도구 템플릿 골든 스니펫만 주입

    prompt 태그로 관련 템플릿만 선택해 토큰을 절감하되, 메뉴 인덱스를 항상 주입해
    노드 누락(검색 miss) 환각을 방지한다."""
    from core.template_registry import menu_index, all_templates

    parts = []

    # 1. 항상층: 구조 레퍼런스 파일
    for filename in _ALWAYS_REFERENCE_FILES:
        path = os.path.join(SKILL_DIR, "references", filename)
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                parts.append(f.read())
        except Exception as e:
            logger.warning("Failed to load %s: %s", filename, e)

    # 2. 항상층: 도구 메뉴 인덱스(노드 존재 인지 → 검색 miss 환각 방지)
    try:
        parts.append("## 사용 가능한 노드/도구 메뉴\n" + menu_index())
    except Exception as e:
        logger.warning("Failed to build menu index: %s", e)
        return "\n\n========================================\n\n".join(parts)

    # 3. 항상층: 구조 노드 골든 스니펫(작고 거의 모든 워크플로우에 필요)
    structural = [
        _render_template_snippet(t) for t in all_templates()
        if t["node_type"] in _ALWAYS_SNIPPET_NODE_TYPES
    ]
    if structural:
        parts.append("## 구조 노드 예시\n" + "\n\n".join(structural))

    # 4. 검색층: 요청에 관련된 AI 도구 템플릿 골든 스니펫
    selected = _select_templates(prompt, current_nodes)
    if selected:
        rendered = [_render_template_snippet(t) for t in selected]
        parts.append("## 관련 도구 노드 예시 (요청 기반 선택)\n" + "\n\n".join(rendered))

    return "\n\n========================================\n\n".join(parts)


def format_mcp_catalog(available_mcp_servers: list | None) -> str:
    """사용자 보유 MCP 서버 목록을 생성 에이전트 instruction에 주입할 텍스트로 포맷한다.

    available_mcp_servers의 각 항목은 dict 또는 catalogId/name/description 속성을 가진 객체.
    비어 있으면 빈 문자열을 반환한다(=MCP 배정 안내 없음).
    """
    if not available_mcp_servers:
        return ""

    def _get(item, key):
        return item.get(key) if isinstance(item, dict) else getattr(item, key, None)

    lines = []
    for m in available_mcp_servers:
        catalog_id = _get(m, "catalogId")
        name = _get(m, "name") or "(이름 없음)"
        desc = _get(m, "description") or ""
        if not catalog_id:
            continue
        lines.append(f"- catalogId={catalog_id} · 이름: {name}" + (f" · 설명: {desc}" if desc else ""))

    if not lines:
        return ""

    catalog = "\n".join(lines)
    return (
        "## 사용 가능한 MCP 서버\n"
        "사용자가 등록한 외부 MCP 서버 목록이다. 사용자의 요청이 아래 MCP 서버 중 하나로 처리하기에 "
        "적합할 때만 해당 AI 노드의 `tools`에 `mcp:<catalogId>` 형식의 문자열을 추가한다.\n"
        "- 반드시 아래 목록에 있는 정확한 catalogId만 사용한다(임의의 server_url이나 이름 사용 금지).\n"
        "- 요청과 무관하면 MCP를 배정하지 않는다.\n"
        f"{catalog}\n"
    )


def format_webhook_catalog(available_webhooks: list | None) -> str:
    """사용자 보유 Slack/Discord 웹훅 자격증명 목록을 생성 에이전트 instruction 텍스트로 포맷한다.

    각 항목은 dict 또는 webhookCredentialId/provider/displayName 속성을 가진 객체.
    비어 있으면 빈 문자열을 반환한다(=webhook 배정 안내 없음)."""
    if not available_webhooks:
        return ""

    def _get(item, key):
        return item.get(key) if isinstance(item, dict) else getattr(item, key, None)

    lines = []
    for w in available_webhooks:
        cred_id = _get(w, "webhookCredentialId")
        provider = _get(w, "provider") or "(provider 미상)"
        name = _get(w, "displayName") or "(이름 없음)"
        if not cred_id:
            continue
        lines.append(f"- webhookCredentialId={cred_id} · {provider} · 이름: {name}")

    if not lines:
        return ""

    catalog = "\n".join(lines)
    return (
        "## 사용 가능한 Slack/Discord 웹훅\n"
        "사용자가 등록한 Slack/Discord 송신 웹훅 자격증명 목록이다. Slack/Discord 발송 노드를 만들 때 "
        "아래에서 요청에 가장 잘 맞는 항목을 골라 해당 AI 노드의 `tools` 항목을 "
        '`{"name":"slack","config":{"webhookCredentialId":"<id>"}}` (Discord면 `\"discord\"`) 형식으로 작성한다.\n'
        "- 반드시 아래 목록에 있는 정확한 webhookCredentialId만 사용한다(임의의 URL이나 id 날조 금지).\n"
        "- 실제 webhook URL은 실행 시 backend가 주입하므로 prompt나 config에 URL을 직접 넣지 않는다.\n"
        "- 해당 provider의 자격증명이 여러 개면 어느 것을 쓸지 CLARIFICATION_NEEDED로 되묻는다.\n"
        f"{catalog}\n"
    )

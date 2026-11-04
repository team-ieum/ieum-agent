"""도구 레지스트리 — 런타임 주입 인자·ACTION 도구 해석·brand 도출.

칸 명세는 도구 함수 시그니처에서 자동 파생한다(core.node_fields.signature_fields).
현재 소비처: 노드 카탈로그 칸 파생(core/template_registry — RUNTIME_INJECTED_PARAMS로
실행 시 주입 인자를 뺀다, GET /v1/nodes/catalog), 생성 노드 brand 주입(apply_service_brand —
core/builder 대화형 생성), 대화형 생성의 금지 칸(core/builder/fields — RUNTIME_INJECTED_PARAMS),
ACTION 도구 함수 해석(resolve_action_fn).

도구를 추가/변경하면 _TOOL_MAP에 함수만 등록하면 되고, 별도의 스펙/문서를
손으로 관리할 필요가 없다(drift 0).
"""

from tools import _TOOL_MAP
from tools.github import (
    github_create_issue,
    github_list_orgs,
    github_list_repos,
    github_list_issues,
    github_list_pull_requests,
)
from tools.google_calendar import google_calendar_delete

# 실행 시 주입되는 파라미터 — 백엔드가 넘기는 자격증명/연결 값과 agent가 내부에서 바인딩하는 값.
# 워크플로우 생성 단계에서는 빈 값이어도 정상이므로 "사용자 필수" 검증과 설정 폼에서 제외한다.
RUNTIME_INJECTED_PARAMS: set[str] = {
    "token",            # Notion / GitHub Integration Token, Slack·Discord 봇 토큰
    "allowed_guild_ids",  # Discord 봇: 이 사용자가 연결한 서버 ID 목록(권한 목록 — 사용자 config로 받지 않는다)
    "access_token",     # Google OAuth Access Token
    "webhook_url",      # Slack / Discord Webhook URL
    "sender_email",     # Gmail SMTP
    "sender_password",  # Gmail SMTP
    "smtp_host",        # Gmail SMTP
    "smtp_port",        # Gmail SMTP
    "workflow_context_data",  # agent가 바인딩(agents/base.py _bind_workflow_context)
}

# 동적 서브에이전트로 마운트되어 노드 tools:[]를 쓰는 서비스의 액션 함수.
# _TOOL_MAP에 없다(AI 노드 tools 키가 아님 — tools_must_be_empty 계약). 쓰는 곳은 ACTION 실행이다
# (resolve_action_fn이 `builtin:github_<x>`를 여기서 찾는다).
_DYNAMIC_SERVICE_ACTIONS: dict[str, dict] = {
    "github": {
        "github_list_orgs": github_list_orgs,
        "github_list_repos": github_list_repos,
        "github_list_issues": github_list_issues,
        "github_list_pull_requests": github_list_pull_requests,
        # ACTION 노드(POST /v1/actions/execute)만 쓰는 쓰기 도구. AI 서브에이전트의 도구 목록은 따로
        # 하드코딩돼 있어 여기 등록해도 AI 쪽엔 노출되지 않는다.
        "github_create_issue": github_create_issue,
    },
}

# ACTION 노드만 실행하는 도구. 되돌릴 수 없는 일(삭제)이라 AI 노드에 싣지 않는다(spec §4.3).
# _TOOL_MAP 밖이라 get_tools_for_request·ai.agent tools 선택지(template_registry)에
# 잡히지 않고, resolve_action_fn만 푼다(카탈로그 로더 tool_key 검사·POST /v1/actions/execute).
# _DYNAMIC_SERVICE_ACTIONS에 넣지 않는 이유: 그 dict는 "동적 서브에이전트 서비스" 뜻이다.
_ACTION_ONLY_TOOLS: dict = {
    "builtin:google_calendar_delete": google_calendar_delete,
}


def resolve_action_fn(tool_key):
    """ACTION 실행·카탈로그가 쓰는 tool_key → 도구 함수. 모르는 키면 None.

    _TOOL_MAP 키 그대로, ACTION 전용(_ACTION_ONLY_TOOLS), 또는 `builtin:github_<x>` →
    _DYNAMIC_SERVICE_ACTIONS["github"]["github_<x>"]. 뒤의 둘은 _TOOL_MAP에 넣지 않는다(AI 노드 비노출)."""
    if not isinstance(tool_key, str):
        return None
    if tool_key in _TOOL_MAP:
        return _TOOL_MAP[tool_key]
    if tool_key in _ACTION_ONLY_TOOLS:
        return _ACTION_ONLY_TOOLS[tool_key]
    if tool_key.startswith("builtin:github_"):
        return _DYNAMIC_SERVICE_ACTIONS["github"].get(tool_key[len("builtin:"):])
    return None


def _service_of(name: str) -> str:
    """도구 키에서 서비스명을 추론한다. builtin: 프리픽스와 세부 동작 접미사를 제거한다."""
    base = name.split(":", 1)[-1]  # builtin:notion_create_page -> notion_create_page
    for service in ("notion", "github", "google_sheets", "google_calendar", "google_drive",
                    "slack", "discord", "gmail", "web_search", "http_fetch",
                    "workflow_context", "json_parse", "text_extract", "date_format"):
        if base == service or base.startswith(service):
            return service
    return base


# 서비스 → 프론트 UI brand 키 매핑. _service_of() 반환값을 프론트가 렌더하는 brand로 변환한다.
# 이 맵이 brand의 단일 출처(SSOT)다. 도구를 추가하면 _service_of가 새 서비스를 추출하고,
# 브랜드 표시가 필요하면 여기 한 줄만 추가한다(매핑에 없으면 _DEFAULT_BRAND로 폴백).
SERVICE_BRAND: dict[str, str] = {
    "notion": "notion",
    "slack": "slack",
    "discord": "discord",
    "gmail": "gmail",
    "github": "github",
    "google_sheets": "sheets",
    "google_calendar": "google",
    "google_drive": "google",
}

# tool_key가 없는 노드(트리거/구조 노드, 도구 없는 AI 노드)의 node_type별 기본 brand.
_NODE_TYPE_BRAND: dict[str, str] = {
    "TRIGGER": "webhook",
    "CONDITION": "filter",
    "TRANSFORM": "filter",
    "HTTP": "webhook",
    "AI": "openai",
}

_DEFAULT_BRAND = "webhook"


def brand_for_node(node: dict) -> str:
    """노드의 UI brand 키를 도출한다.

    AI가 아닌 노드는 event 앱의 brand(apps.json, spec §4). agent가 모르는 event면 node_type 기본값 —
    옛 필드(트리거 종류·ACTION 도구 목록)로 추정하지 않는다.
    AI 노드의 event는 늘 ai.agent·ai.reasoning이라 앱을 말해 주지 않으므로 도구 tool_key의 서비스
    (SERVICE_BRAND) → 도구 없는 노드는 intent(라벨+프롬프트) 태그로 동적 서브에이전트 서비스 → AI 기본값.
    재진입 주의: 로더(_normalize)는 앱 없는 항목(모두 AI 프리셋)에만 brand_for_entry→brand_for_node를 부른다.
    AI 분기는 event_entry(→load_templates)를 부르지 않으므로 기동 중 재귀가 없다."""
    if not isinstance(node, dict):
        return _DEFAULT_BRAND
    ntype = str(node.get("type") or "").upper()
    if ntype != "AI":
        from core.template_registry import event_entry
        entry = event_entry(node)
        return entry["brand"] if entry is not None else _NODE_TYPE_BRAND.get(ntype, _DEFAULT_BRAND)
    config = node.get("config")
    tools = config.get("tools") if isinstance(config, dict) else None
    if isinstance(tools, list):
        for t in tools:
            name = t.get("name") if isinstance(t, dict) else t
            if not name or name == "mcp":
                continue
            brand = SERVICE_BRAND.get(_service_of(name))
            if brand:
                return brand
    # tool_key 없는 AI 노드: intent 기반으로 동적 서브에이전트 서비스(github 등) 식별.
    # template_registry는 tools 패키지를 lazy import하므로 함수 내부에서 import한다.
    from core.template_registry import subagent_service_for_node
    service = subagent_service_for_node(node)
    if service and SERVICE_BRAND.get(service):
        return SERVICE_BRAND[service]
    return _NODE_TYPE_BRAND["AI"]


def brand_for_entry(entry: dict) -> str:
    """카탈로그 항목(로더 메모리 항목)의 brand. 로더 기동 중(_normalize)에 부르므로 intent 경로를 타지 않는다.

    brand_for_node의 AI 분기가 쓰는 subagent_service_for_node는 select_by_tags → load_templates를 다시 불러
    기동 중엔 무한 재귀가 난다(fixed엔 label·prompt도 없어 매칭도 못 한다). 그래서 tool_key 없는 AI 항목은
    템플릿의 service 필드(ai.github_query → github)로 SERVICE_BRAND를 읽고, 없으면 AI 기본값을 쓴다.
    tool_key가 있거나 AI가 아닌 항목(앱 트리거 포함)은 fixed로 brand_for_node가 답한다."""
    if entry["tool_key"] or entry["node_type"] != "AI":
        return brand_for_node(entry["fixed"])
    return SERVICE_BRAND.get(entry.get("service"), _NODE_TYPE_BRAND["AI"])


def apply_service_brand(nodes: list) -> None:
    """각 노드 config["brand"]를 도출 값으로 주입한다(in-place).

    프론트가 노드 헤더에 서비스 라벨/아이콘을 렌더하는 데 쓴다."""
    if not isinstance(nodes, list):
        return
    for node in nodes:
        if not isinstance(node, dict):
            continue
        config = node.get("config")
        if not isinstance(config, dict):
            config = {}
            node["config"] = config
        config["brand"] = brand_for_node(node)



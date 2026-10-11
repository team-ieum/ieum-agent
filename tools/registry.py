"""도구 파라미터 스펙 SSOT 레지스트리.

도구 함수의 시그니처(`inspect.signature`)에서 파라미터 명세를 자동 파생한다.
현재 소비처: 노드 카탈로그·생성 슬롯 파생(core/template_registry — RUNTIME_INJECTED_PARAMS로
실행 시 주입 인자를 뺀다, GET /v1/nodes/catalog), 생성 노드 brand 주입(apply_service_brand —
workflow_generator·workflow_chat). 파라미터 스펙·블루프린트(tool_param_spec·service_blueprint)는
(추후) RAG 색인용으로 지금은 테스트만 부른다. 검증기(workflow_validator)는 이 모듈이 아니라
_TOOL_MAP과 core/template_registry를 쓴다.

도구를 추가/변경하면 _TOOL_MAP에 함수만 등록하면 되고, 별도의 스펙/문서를
손으로 관리할 필요가 없다(drift 0).
"""

import inspect

from tools import _TOOL_MAP
from tools.github import (
    github_create_issue,
    github_list_orgs,
    github_list_repos,
    github_list_issues,
    github_list_pull_requests,
)

# 실행 시 주입되는 파라미터 — 백엔드가 넘기는 자격증명/연결 값과 agent가 내부에서 바인딩하는 값.
# 워크플로우 생성 단계에서는 빈 값이어도 정상이므로 "사용자 필수" 검증과 설정 폼에서 제외한다.
RUNTIME_INJECTED_PARAMS: set[str] = {
    "token",            # Notion / GitHub Integration Token
    "access_token",     # Google OAuth Access Token
    "webhook_url",      # Slack / Discord Webhook URL
    "sender_email",     # Gmail SMTP
    "sender_password",  # Gmail SMTP
    "smtp_host",        # Gmail SMTP
    "smtp_port",        # Gmail SMTP
    "workflow_context_data",  # agent가 바인딩(agents/base.py _bind_workflow_context)
}

# 자동 파생이 불가능한 서비스별 수동 정책. 양이 적으므로 코드에 두고 git으로 관리한다.
# (DB가 아닌 코드를 마스터로 둔다 — 정책은 코드 리뷰/배포/롤백 대상)
SERVICE_POLICY: dict[str, dict] = {
    "notion": {
        "prompt_hint": "parent_page_id를 모르면 builtin:notion_search로 먼저 타겟 페이지를 검색한다.",
    },
    "github": {
        # GitHub는 실행 시 서브에이전트(MCP)로 동적 마운트된다. 노드 tools는 반드시 비운다([]).
        "node_contract": "tools_must_be_empty",
        "mount": "dynamic_subagent",
        "prompt_hint": "GitHub 작업은 tools를 비우고(`tools: []`) prompt에 자연어로 지시한다. "
                       "github_list_pull_requests 같은 도구명을 tools에 넣지 않는다.",
    },
}

# 동적 서브에이전트로 마운트되어 노드 tools:[]를 쓰는 서비스의 액션 함수.
# _TOOL_MAP에 없다(AI 노드 tools 키가 아님 — tools_must_be_empty 계약). 쓰는 곳은 두 갈래다:
# 프롬프트/문서용 파라미터 스펙 파생, 그리고 ACTION 실행(resolve_action_fn이 `builtin:github_<x>`를 여기서 찾는다).
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


def resolve_action_fn(tool_key):
    """ACTION 실행·카탈로그가 쓰는 tool_key → 도구 함수. 모르는 키면 None.

    _TOOL_MAP 키 그대로, 또는 `builtin:github_<x>` → _DYNAMIC_SERVICE_ACTIONS["github"]["github_<x>"].
    github 액션은 _TOOL_MAP에 넣지 않는다(AI 노드 tools_must_be_empty 계약)."""
    if not isinstance(tool_key, str):
        return None
    if tool_key in _TOOL_MAP:
        return _TOOL_MAP[tool_key]
    if tool_key.startswith("builtin:github_"):
        return _DYNAMIC_SERVICE_ACTIONS["github"].get(tool_key[len("builtin:"):])
    return None


def _classify(param: inspect.Parameter) -> str:
    """파라미터를 3분류한다: runtime_injected | required | optional."""
    if param.name in RUNTIME_INJECTED_PARAMS:
        return "runtime_injected"
    if param.default is inspect.Parameter.empty:
        return "required"
    return "optional"


def _type_name(annotation) -> str:
    if annotation is inspect.Parameter.empty:
        return "str"
    return getattr(annotation, "__name__", str(annotation))


def _param_spec_from_fn(fn) -> dict:
    """함수 시그니처에서 파라미터 명세를 파생한다(공통 로직)."""
    sig = inspect.signature(fn)
    spec: dict = {}
    for p in sig.parameters.values():
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        spec[p.name] = {
            "class": _classify(p),
            "type": _type_name(p.annotation),
            # JSON 직렬화 불가 기본값(커스텀 객체/함수/datetime 등)은 문자열로 변환해
            # 프론트 메타 엔드포인트 등에서 직렬화 에러가 나지 않도록 한다.
            "default": None if p.default is inspect.Parameter.empty else (
                p.default if isinstance(p.default, (str, int, float, bool, list, dict, type(None)))
                else str(p.default)
            ),
        }
    return spec


def tool_param_spec(name: str) -> dict:
    """노드 tools 키(_TOOL_MAP) 또는 동적 서비스 액션의 함수 시그니처에서 파라미터 명세를 파생한다.

    반환 형식:
        {
          "<param>": {"class": "required|optional|runtime_injected",
                      "type": "str", "default": <default or None>},
          ...
        }
    """
    if name in _TOOL_MAP:
        return _param_spec_from_fn(_TOOL_MAP[name])
    for actions in _DYNAMIC_SERVICE_ACTIONS.values():
        if name in actions:
            return _param_spec_from_fn(actions[name])
    raise KeyError(f"Tool '{name}' not found in registry.")


def required_user_params(name: str) -> list[str]:
    """사용자(또는 이전 노드)가 반드시 제공해야 하는 파라미터 이름 목록.

    런타임 주입 파라미터와 기본값이 있는 선택 파라미터는 제외한다.
    검증기가 이 목록을 기준으로 누락을 검사한다.
    """
    return [n for n, s in tool_param_spec(name).items() if s["class"] == "required"]


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

    우선순위: 노드 tools의 tool_key에서 추출한 서비스(SERVICE_BRAND) → TRIGGER는 triggerType 서비스(앱 트리거) → node_type 기본값.
    ACTION 노드는 github도 tool_key(`builtin:github_<x>`)가 있어 첫 갈래로 답한다.
    AI 노드의 동적 서브에이전트 서비스(github 등)는 tools가 비어([]) tool_key가 없으므로,
    intent(라벨+프롬프트) 태그 매칭으로 서비스를 식별한다(생성 시 분류와 동일 신호)."""
    config = node.get("config") if isinstance(node, dict) else None
    tools = config.get("tools") if isinstance(config, dict) else None
    if isinstance(tools, list):
        for t in tools:
            name = t.get("name") if isinstance(t, dict) else t
            if not name or name == "mcp":
                continue
            brand = SERVICE_BRAND.get(_service_of(name))
            if brand:
                return brand
    ntype = (node.get("type") or "").upper() if isinstance(node, dict) else ""
    # tool_key 없는 AI 노드: intent 기반으로 동적 서브에이전트 서비스(github 등) 식별.
    # template_registry는 tools 패키지를 lazy import하므로 함수 내부에서 import한다.
    if ntype == "AI":
        from core.template_registry import subagent_service_for_node
        service = subagent_service_for_node(node)
        if service and SERVICE_BRAND.get(service):
            return SERVICE_BRAND[service]
    # 앱 트리거는 triggerType 접두사(GMAIL_NEW_EMAIL → gmail)로 앱 brand — BE 연동 목록이 config.brand로
    # 워크플로우를 고르므로 webhook이면 빠진다. SCHEDULE/MANUAL/WEBHOOK은 매핑에 없어 webhook.
    if ntype == "TRIGGER" and isinstance(config, dict) and isinstance(config.get("triggerType"), str):
        brand = SERVICE_BRAND.get(_service_of(config["triggerType"].lower()))
        if brand:
            return brand
    return _NODE_TYPE_BRAND.get(ntype, _DEFAULT_BRAND)


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

    프론트가 노드 헤더에 서비스 라벨/아이콘을 렌더하는 데 쓴다. config["brand"]는
    UNIVERSAL_CONFIG_FIELDS에 포함되어 검증 화이트리스트를 통과한다."""
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


def service_blueprint(service: str) -> dict:
    """서비스별 블루프린트를 합성한다: 시그니처 자동 파생 + 수동 정책.

    두 종류를 지원한다.
    - tool-based: 노드 tools 키(_TOOL_MAP)에서 파라미터 스펙을 파생 (notion/slack/google 등)
    - policy-only: 동적 서브에이전트로 마운트되는 서비스(github 등). 노드 tools는 []이며,
      액션 스펙은 _DYNAMIC_SERVICE_ACTIONS에서 파생한다.

    이 레지스트리(코드)가 마스터다. DB/캐시/RAG 색인은 이 결과를 빌드해 사용하는
    파생물이어야 하며, 손으로 편집하지 않는다.
    """
    tools = {
        name: tool_param_spec(name)
        for name in _TOOL_MAP
        if _service_of(name) == service
    }
    blueprint: dict = {"service": service, "tools": tools}

    # 동적 마운트 서비스의 액션 스펙(노드 tools에는 들어가지 않음)
    actions = _DYNAMIC_SERVICE_ACTIONS.get(service)
    if actions:
        blueprint["actions"] = {
            action: _param_spec_from_fn(fn) for action, fn in actions.items()
        }

    blueprint.update(SERVICE_POLICY.get(service, {}))
    return blueprint


def all_blueprints() -> dict[str, dict]:
    """등록된 모든 서비스의 블루프린트를 반환한다.

    tool-based 서비스(_TOOL_MAP)와 policy-only 서비스(SERVICE_POLICY/동적 마운트)를 모두 포함한다.
    (추후) RAG 색인 빌드의 입력으로 쓴다.
    """
    services = {_service_of(name) for name in _TOOL_MAP}
    services |= set(SERVICE_POLICY.keys())
    services |= set(_DYNAMIC_SERVICE_ACTIONS.keys())
    return {s: service_blueprint(s) for s in sorted(services)}

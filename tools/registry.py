"""도구 파라미터 스펙 SSOT 레지스트리.

도구 함수의 시그니처(`inspect.signature`)에서 파라미터 명세를 자동 파생한다.
검증기(workflow_validator), 프론트엔드 설정 폼 엔드포인트, 시스템 프롬프트 도구 명세,
(추후) RAG 색인이 모두 이 레지스트리를 단일 출처(SSOT)로 삼는다.

도구를 추가/변경하면 _TOOL_MAP에 함수만 등록하면 되고, 별도의 스펙/문서를
손으로 관리할 필요가 없다(drift 0).
"""

import inspect

from tools import _TOOL_MAP
from tools.github import (
    github_list_orgs,
    github_list_repos,
    github_list_issues,
    github_list_pull_requests,
)

# Spring Boot 백엔드가 실행 시 주입하는 자격증명/연결 파라미터.
# 워크플로우 생성 단계에서는 빈 값이어도 정상이므로 "사용자 필수" 검증에서 제외한다.
RUNTIME_INJECTED_PARAMS: set[str] = {
    "token",            # Notion / GitHub Integration Token
    "access_token",     # Google OAuth Access Token
    "webhook_url",      # Slack / Discord Webhook URL
    "sender_email",     # Gmail SMTP
    "sender_password",  # Gmail SMTP
    "smtp_host",        # Gmail SMTP
    "smtp_port",        # Gmail SMTP
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
# _TOOL_MAP에 없으므로(노드 tools 키가 아님) 블루프린트 표현을 위해 별도로 등록한다.
# 노드 tools에는 들어가지 않으며, 프론트 폼/프롬프트/문서용 파라미터 스펙 파생에만 쓴다.
_DYNAMIC_SERVICE_ACTIONS: dict[str, dict] = {
    "github": {
        "github_list_orgs": github_list_orgs,
        "github_list_repos": github_list_repos,
        "github_list_issues": github_list_issues,
        "github_list_pull_requests": github_list_pull_requests,
    },
}


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
    for service in ("notion", "google_sheets", "google_calendar", "google_drive",
                    "slack", "discord", "gmail", "web_search", "http_fetch",
                    "workflow_context", "json_parse", "text_extract", "date_format"):
        if base == service or base.startswith(service):
            return service
    return base


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
    프론트엔드 메타데이터 엔드포인트와 (추후) RAG 색인 빌드의 입력으로 쓴다.
    """
    services = {_service_of(name) for name in _TOOL_MAP}
    services |= set(SERVICE_POLICY.keys())
    services |= set(_DYNAMIC_SERVICE_ACTIONS.keys())
    return {s: service_blueprint(s) for s in sorted(services)}

"""대화형 생성의 칸 쓰기 규칙(spec 2026-10-28 §3.3) — LLM이 쓰는 값은 전부 여기를 지난다."""
import re
from urllib.parse import urlsplit

from core.builder.state import said_literally
from core.node_fields import names_path
from core.template_registry import _get_by_path, _set_by_path, match_entry
from core.validators.static_validator import validate_workflow
from tools.registry import RUNTIME_INJECTED_PARAMS

# 자유 입력 목적지 칸 — 사용자가 대화에 직접 적은 호스트·주소로만 정한다. 받는 사람 칸이 새로 열리면 여기에 더한다.
DESTINATION_URL_FIELDS = {("http.request", "config.url"), ("ai.agent", "config.tools.0.config.url")}
DESTINATION_EMAIL_FIELDS = {("google_calendar.update", "config.attendees")}  # 쉼표 구분 이메일 — 초대 메일이 나간다
# 실행 때 주입되는 값·저장 때 BE가 채우는 값은 LLM이 쓰지 않는다(llm.inject 칸 llmProvider·model은 writable_fields가 뺀다)
_FORBIDDEN_KEYS = {"credentialId"} | RUNTIME_INJECTED_PARAMS
# authority에 이것이 있으면 파싱 호스트와 실행 호스트가 갈릴 수 있다(https://{{x}}@user-said.com/)
_BAD_AUTHORITY = re.compile(r"\{\{|[@\\\s\x00-\x1f\x7f\[\]]")
# 노드 하나의 검증에서 뺄 코드 — 그래프 전체 몫(spec §5.4)
_GRAPH_CODES = ("TRIGGER_COUNT", "ORPHAN_NODE", "EDGE_INVALID")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def clean(value, limit: int = 60) -> str:
    """도구 결과·표시 이름에 싣는 문자열 — 개행·제어문자를 공백으로, limit자에서 자른다."""
    return _CONTROL.sub(" ", str(value)).strip()[:limit]


def writable_fields(node: dict) -> dict[str, dict]:
    """쓸 수 있는 칸 {path: Field} — match_entry의 inputFields에서 금지 칸을 뺀 것. 모르는 event면 빈 dict."""
    entry = match_entry(node)
    if entry is None:
        return {}
    # config.tools(도구 목록 통째)는 쓰지 않는다 — 덮어쓰면 목적지·드롭다운 검사를 새 도구로 우회한다
    return {f["path"]: f for f in entry["inputFields"]
            if f["key"] not in _FORBIDDEN_KEYS and "inject" not in (f.get("llm") or {})
            and f["path"] != "config.tools"}


def check_url(url, user_hosts) -> str | None:
    """목적지 url 검사. 통과면 None, 아니면 LLM에 돌려줄 이유."""
    if not isinstance(url, str) or "://" not in url:
        return "http:// 또는 https://로 시작하는 주소만 넣을 수 있어요."
    scheme, rest = url.split("://", 1)
    if scheme.lower() not in ("http", "https"):
        return "http:// 또는 https://로 시작하는 주소만 넣을 수 있어요."
    authority = re.split(r"[/?#]", rest, maxsplit=1)[0]
    if not authority or _BAD_AUTHORITY.search(authority):
        return "주소의 호스트 부분에는 참조식·@·\\·공백·대괄호를 쓸 수 없어요."
    try:
        host = (urlsplit(url).hostname or "").rstrip(".")
    except ValueError:
        return "주소의 호스트 부분을 읽을 수 없어요."
    if not host or host not in set(user_hosts or []):
        return f"'{clean(host)}'는 사용자가 대화에서 직접 말한 주소가 아니에요. 보낼 주소를 사용자에게 물어보세요."
    return None


def check_emails(value, user_text: str) -> str | None:
    """받는 사람 칸 검사 — 쉼표로 나눈 주소가 전부 사용자 입력에 문자 그대로 있어야 한다(참조식 거절). 통과면 None."""
    if not isinstance(value, str) or "{{" in value:
        return "받는 사람 칸에는 사용자가 대화에 적은 이메일만 쉼표로 넣어요(참조식 불가)."
    for address in (a.strip() for a in value.split(",")):
        if address and not said_literally(address, user_text):
            return f"'{clean(address)}'는 사용자가 대화에서 직접 말한 주소가 아니에요. 받는 사람을 사용자에게 물어보세요."
    return None


def apply_fields(node: dict, values: dict, state) -> tuple[list[str], dict[str, str]]:
    """values {path: 값}을 노드에 쓴다(in-place). (쓴 경로, {거절한 경로: 이유})."""
    fields = writable_fields(node)
    names = {names_path(p): p for p in fields}
    applied, rejected = [], {}
    for path, value in values.items():
        reason = _write(node, str(path), value, fields, names, state)
        if reason:
            rejected[str(path)] = reason
        else:
            applied.append(str(path))
    return applied, rejected


def _write(node: dict, path: str, value, fields: dict, names: dict, state) -> str | None:
    if path in names:
        if not isinstance(value, str):
            return "표시 이름은 문자열이에요."
        _set_by_path(node, path, clean(value))
        return None
    f = fields.get(path)
    if f is None:
        return "쓸 수 없는 칸이에요. get_fields가 준 path만 쓸 수 있어요."
    if f.get("fieldsSource"):
        if not isinstance(value, dict):
            return "동적 칸은 {키: 값} 객체로 넣어요."
        # 키를 경로로 쪼개지 않는다 — 숫자 키가 리스트 인덱스가 되는 _set_by_path 문제. 키의 '.'는 %2E(BE와 같은 규칙).
        current = _get_by_path(node, path)
        merged = dict(current) if isinstance(current, dict) else {}
        for k, v in value.items():
            key = str(k).replace(".", "%2E")
            if v is None:
                merged.pop(key, None)
            else:
                merged[key] = v
        _set_by_path(node, path, merged)
        return None
    if value not in (None, "") and f.get("optionsSource"):
        if not isinstance(value, str) or "{{" in value:
            return "드롭다운 칸에는 참조식이나 문자열이 아닌 값을 넣을 수 없어요."
        seen = (state.get("options_seen") or {}).get(f"{node['id']}|{path}") or {}
        if value not in seen and not said_literally(value, state.get("user_text") or ""):
            return "드롭다운 칸은 list_options 후보의 id나 사용자가 직접 말한 값만 넣을 수 있어요."
    if value not in (None, "") and (node.get("event"), path) in DESTINATION_URL_FIELDS:
        reason = check_url(value, state.get("user_hosts"))
        if reason:
            return reason
    if value not in (None, "") and (node.get("event"), path) in DESTINATION_EMAIL_FIELDS:
        reason = check_emails(value, state.get("user_text") or "")
        if reason:
            return reason
    _set_by_path(node, path, value)
    return None


def node_errors(nodes: list, edges: list, node_id: str) -> list[dict]:
    """노드 하나의 검증 에러 — 전체 validate_workflow에서 그 노드 것만, 그래프 코드는 뺀다(참조식 REF_*까지 본다)."""
    return [e for e in validate_workflow(nodes, edges)
            if e["nodeId"] == node_id and e["code"] not in _GRAPH_CODES]

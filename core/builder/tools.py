"""대화형 생성 도구(spec 2026-10-28 §3.2). 도구 하나 = 모듈 함수 하나(turn, state, 인자) — 테스트는 이 함수를 바로 부르고,
LLM에는 make_tools(turn)이 turn을 잡은 얇은 래퍼를 준다. 위임 토큰은 turn.backend 안에만 있다(인자·state에 없음).
state는 ADK 세션 state(도구 안에서는 tool_context.state) — 최상위 키 값을 통째로 다시 넣는다."""
import copy
import functools
import os
import re
import time
import uuid
from dataclasses import dataclass, field

from google.adk.tools.tool_context import ToolContext

from core.builder.backend import Backend
from core.builder.fields import apply_fields, clean, node_errors, writable_fields
from core.builder.hitl import (ASK_FINISH, ASK_SKIP_ONLY, ASK_TEST, FINISH, finish_summary, how_to_test,
                               sample_summary, target_summary)
from core.builder.state import DONE_STATUSES, node_hash, node_status, workflow_hash
from core.provider_config import resolve_model
from core.template_registry import CATALOG_DIR, _get_by_path, _read_json, builder_entries, event_entry
from core.validators.static_validator import _declared_outputs, validate_workflow
from tools.registry import apply_service_brand

# OAuth 연결 판정: apps.json auth → BE /oauth/connections의 provider (spec §3.4)
_AUTH_PROVIDER = {"google_oauth": "GOOGLE", "github_token": "GITHUB", "notion_token": "NOTION",
                  "slack_oauth": "SLACK", "discord_oauth": "DISCORD"}
MAX_CANDIDATES = 20
MAX_LOOKUPS_PER_TURN = 5
MAX_TESTS_PER_TURN = 3
TURN_SECONDS = 540       # 턴 본체 시간 상한(spec §3.5) — 엔진 TURN_TIMEOUT_SECONDS가 이 값이다(한 상수)
MIN_TEST_SECONDS = 200   # BE 테스트 요청 timeout 190초 + 여유 — 이보다 적게 남으면 테스트를 시작하지 않는다


@dataclass
class Turn:
    """한 턴 동안의 작업 사본. nodes·edges는 요청 currentNodes·currentEdges의 deepcopy이고 도구가 직접 고친다."""
    workflow_id: str
    provider: str
    backend: Backend
    nodes: list
    edges: list
    confirmed: dict | None = None      # 턴 시작 판정이 넘긴 {node_id, hash} — 쓰기 노드 테스트 1회분
    confirm_used: bool = False         # 같은 턴 안 두 번째 호출(병렬 호출 포함)을 막는다
    lookups: int = 0
    test_runs: int = 0
    connections: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)   # 응답 끝에 코드가 붙일 문장(샘플 요약 등)
    ask: str | None = None                      # 응답 맨 끝 확인 질문(마지막 것 하나)
    pending: dict | None = None                 # ask와 짝인 확인 대기 — 응답을 조립한 뒤에만 state에 저장한다(엔진)
    created: list = field(default_factory=list)  # 이번 턴에 만든 노드 id(아직 BE 저장 전)
    deadline: float = field(default_factory=lambda: time.monotonic() + TURN_SECONDS)  # time.monotonic() 기준

    def node(self, node_id) -> dict | None:
        return next((n for n in self.nodes if n.get("id") == node_id), None)


@functools.lru_cache(maxsize=1)
def _apps() -> dict:
    return _read_json(os.path.join(CATALOG_DIR, "apps.json"))


def menu() -> list[dict]:
    """생성 메뉴 = 빌더 메뉴에서 ai.agent를 뺀 것(spec §3.1). 앱 ai.* 프리셋은 builder가 아니라 이미 없다."""
    return [t for t in builder_entries() if t["id"] != "ai.agent"]


def _err(reason: str, **extra) -> dict:
    return {"error": reason, **extra}


# ── 메뉴·개요·연결 ─────────────────────────────────────────────────────────

def find_events(query: str = "") -> dict:
    words = [w for w in (query or "").lower().split() if w]

    def hay(t):
        return " ".join([t["id"], t["title"], t.get("app_title") or "", t.get("kind") or "",
                         t.get("description") or ""]).lower()

    entries = menu()
    hits = [t for t in entries if any(w in hay(t) for w in words)] or entries
    apps = _apps()
    return {"events": [{"event": t["id"], "title": clean(t["title"]), "kind": t.get("kind"),
                        "node_type": t["node_type"], "app": t["app"], "app_title": clean(t.get("app_title") or ""),
                        "needs_connection": bool((apps.get(t["app"]) or {}).get("auth"))}
                       for t in hits]}


def set_outline(state, steps: list, workflowName: str) -> dict:
    if not isinstance(steps, list):
        return _err("steps는 단계 이름 목록이에요.")
    state["outline"] = {"steps": [clean(s, 80) for s in (steps or [])][:12], "workflowName": clean(workflowName)}
    return {"ok": True}


async def check_connection(turn: Turn, app: str) -> dict:
    if app in turn.connections:
        return turn.connections[app]
    meta = _apps().get(app)
    if meta is None:
        return {"connected": False, "message": f"모르는 앱이에요: {clean(app)}"}
    auth = meta.get("auth")
    if not auth:
        result = {"connected": True}
    elif auth == "webhook_credential":
        r = await turn.backend.options(f"{meta['provider']}.webhooks", {})
        ok = "data" in r and bool((r["data"] or {}).get("items"))
        result = {"connected": ok} if ok else {
            "connected": False,
            "message": r["error"]["message"] if "error" in r
            else f"설정에서 {meta['title']} 웹훅을 등록한 뒤 '등록했어'라고 말해 주세요."}
    else:
        r = await turn.backend.connections()
        ok = "data" in r and any(isinstance(c, dict) and c.get("provider") == _AUTH_PROVIDER.get(auth)
                                 for c in (r["data"] or []))
        result = {"connected": ok} if ok else {
            "connected": False,
            "message": r["error"]["message"] if "error" in r
            else f"설정 > 연동에서 {meta['title']}을(를) 연결한 뒤 '연결했어'라고 말해 주세요."}
    turn.connections[app] = result
    return result


# ── 노드·엣지 ──────────────────────────────────────────────────────────────

def _branch_error(source: dict, branch: str) -> str | None:
    """branch.if에서 나가는 엣지는 true|false 필수, 그 밖에서는 금지. BE는 conditionType이 결과와 같은 엣지만 따라간다."""
    if source.get("event") == "branch.if":
        return None if branch in ("true", "false") else "분기 노드에서 나가는 연결은 branch를 'true' 또는 'false'로 정해요."
    return "branch는 분기 노드에서 나가는 연결에만 써요." if branch else None


def _is_trigger(node: dict) -> bool:
    entry = event_entry(node)
    return (entry["node_type"] if entry else str(node.get("type") or "").upper()) == "TRIGGER"


def _edge(source: str, target: str, branch: str) -> dict:
    return {"source": source, "target": target, **({"conditionType": branch} if branch else {})}


async def add_node(turn: Turn, event: str, label: str, after: str = "", branch: str = "") -> dict:
    entry = next((t for t in menu() if t["id"] == event), None)
    if entry is None:
        return _err("find_events가 준 event만 쓸 수 있어요.")
    if after:
        source = turn.node(after)
        if source is None:
            return _err(f"앞 노드 '{clean(after)}'가 없어요.")
        if entry["node_type"] == "TRIGGER":
            return _err("트리거는 다른 노드 뒤에 둘 수 없어요.")
        reason = _branch_error(source, branch)
        if reason:
            return _err(reason)
    elif branch:
        return _err("branch는 after와 함께 써요.")
    connection = await check_connection(turn, entry["app"])
    if not connection["connected"]:
        return _err("연결이 필요해서 노드를 만들지 않았어요.", message=connection.get("message"))
    node = {"id": str(uuid.uuid4()), "event": event, "type": entry["node_type"],
            "label": clean(label) or entry["title"], "config": copy.deepcopy(entry["fixed"]["config"])}
    if entry["node_type"] == "AI":
        node["config"]["llmProvider"] = turn.provider.upper()
        node["config"]["model"] = resolve_model(turn.provider)
        apply_service_brand([node])
    turn.nodes.append(node)
    turn.created.append(node["id"])
    if after:
        turn.edges.append(_edge(after, node["id"], branch))
    return {"node_id": node["id"]}


def connect(turn: Turn, source: str, target: str, branch: str = "") -> dict:
    src, dst = turn.node(source), turn.node(target)
    if src is None or dst is None:
        return _err("없는 노드예요.")
    if source == target:
        return _err("자기 자신에게 연결할 수 없어요.")
    if _is_trigger(dst):
        return _err("트리거로 들어오는 연결은 둘 수 없어요.")
    reason = _branch_error(src, branch)
    if reason:
        return _err(reason)
    edge = _edge(source, target, branch)
    if not any(e.get("source") == source and e.get("target") == target
               and (e.get("conditionType") or "") == branch for e in turn.edges):
        turn.edges.append(edge)
    return {"ok": True}


def remove_node(turn: Turn, node_id: str) -> dict:
    if turn.node(node_id) is None:
        return _err("없는 노드예요.")
    if len(turn.nodes) == 1:  # FE는 빈 nodes로 캔버스를 갱신하지 않아 노드가 되살아난다(spec §2.1)
        return _err("마지막 남은 노드는 지울 수 없어요. 다른 노드를 먼저 만든 뒤 지워요.")
    turn.nodes[:] = [n for n in turn.nodes if n.get("id") != node_id]
    turn.edges[:] = [e for e in turn.edges if node_id not in (e.get("source"), e.get("target"))]
    return {"ok": True}


# ── 칸 ────────────────────────────────────────────────────────────────────

# GOOGLE_SCOPE_REQUIRED(403)의 공급원 → 추가할 권한(BE IntegrationOptionControllerDocs의 scope_groups 표)
_GOOGLE_SCOPE = {"spreadsheets": "Sheets", "worksheets": "Sheets", "files": "Drive", "folders": "Drive",
                 "calendars": "Calendar", "writable_calendars": "Calendar", "events": "Calendar"}
# test_node는 노드 event(google_calendar.update)를 넘긴다 — 앱으로 정한다
_GOOGLE_APP = {"gmail": "Gmail", "google_sheets": "Sheets", "google_drive": "Drive", "google_calendar": "Calendar"}


def _explain(error: dict, source: str | None) -> dict:
    code = error.get("code")
    if code == "GOOGLE_SCOPE_REQUIRED":
        app, _, resource = (source or "").partition(".")
        group = _GOOGLE_SCOPE.get(resource) or _GOOGLE_APP.get(app, "해당 서비스")
        return {"code": code, "message": f"설정 > 연동의 Google 연결에서 {group} 권한을 추가해 주세요."}
    return {"code": code, "message": clean(error.get("message") or "", 120)}


def _brief(errors: list) -> list:
    return [{"field": e["field"], "code": e["code"], "message": e["message"]} for e in errors]


def _ancestors(turn: Turn, node_id: str) -> list[str]:
    seen, stack = [], [e.get("source") for e in turn.edges if e.get("target") == node_id]
    while stack:
        cur = stack.pop()
        if cur not in seen and turn.node(cur) is not None:
            seen.append(cur)
            stack.extend(e.get("source") for e in turn.edges if e.get("target") == cur)
    return seen


def _outputs(node: dict, state) -> list[str] | None:
    """참조식에 쓸 출력 이름 — 테스트 샘플의 키, 없으면 카탈로그(outputFields·outputsFrom). 알 수 없으면 None."""
    test = (state.get("tests") or {}).get(node["id"])
    if test and test.get("fields"):
        return list(test["fields"])
    declared = _declared_outputs(node)
    return sorted(declared) if declared is not None else None


def _shown(value):
    return value[:500] if isinstance(value, str) else value


def get_fields(turn: Turn, state, node_id: str) -> dict:
    node = turn.node(node_id)
    if node is None:
        return _err("없는 노드예요.")
    out = []
    for path, f in writable_fields(node).items():
        item = {"path": path, "title": clean(f.get("title") or f["key"]), "type": f["type"],
                "required": bool(f.get("required"))}
        if f.get("description"):
            item["description"] = clean(f["description"], 120)
        if f.get("choices"):
            item["choices"] = [{"id": c["id"], "name": clean(c.get("name") or c["id"])}
                               for c in f["choices"][:MAX_CANDIDATES]]
        if f.get("optionsSource"):
            item["dropdown"] = True
        if f.get("fieldsSource"):
            item["dynamic"] = True
        value = _get_by_path(node, path)
        if value not in (None, "", [], {}):
            item["value"] = _shown(value)
        out.append(item)
    upstream = [{"node_id": a, "label": clean(turn.node(a).get("label") or ""),
                 "outputs": _outputs(turn.node(a), state)} for a in _ancestors(turn, node_id)]
    return {"node_id": node_id, "event": node.get("event"), "fields": out, "upstream": upstream,
            "errors": _brief(node_errors(turn.nodes, turn.edges, node_id))}


def set_fields(turn: Turn, state, node_id: str, values: dict) -> dict:
    node = turn.node(node_id)
    if node is None:
        return _err("없는 노드예요.")
    if not isinstance(values, dict):
        return _err("values는 {path: 값} 객체예요.")
    applied, rejected = apply_fields(node, values, state)
    return {"applied": applied, "rejected": rejected, "errors": _brief(node_errors(turn.nodes, turn.edges, node_id))}


def _inputs(node: dict, field_path: str, keys) -> tuple[dict, str | None]:
    """optionsInputs·fieldsInputs 칸 값(같은 부모 경로의 형제 칸) → 쿼리. 비었거나 참조식이면 이유."""
    parent = field_path.rpartition(".")[0]
    params = {}
    for key in keys or []:
        value = _get_by_path(node, f"{parent}.{key}")
        if value in (None, ""):
            return {}, f"먼저 '{key}' 칸을 채워야 후보를 볼 수 있어요."
        if not isinstance(value, str) or "{{" in value:
            return {}, f"'{key}' 칸이 앞 단계 값이면 후보를 볼 수 없어요. 사용자에게 직접 물어보세요."
        params[key] = value
    return params, None


def _spend_lookup(turn: Turn) -> str | None:
    if turn.lookups >= MAX_LOOKUPS_PER_TURN:
        return f"이번 턴 조회 한도({MAX_LOOKUPS_PER_TURN}회)를 다 썼어요. 사용자에게 먼저 물어보세요."
    turn.lookups += 1
    return None


async def list_options(turn: Turn, state, node_id: str, field_path: str, q: str = "") -> dict:
    node = turn.node(node_id)
    f = writable_fields(node).get(field_path) if node else None
    if f is None or not f.get("optionsSource"):
        return _err("드롭다운 칸이 아니에요(get_fields의 dropdown 칸만).")
    params, reason = _inputs(node, field_path, f.get("optionsInputs"))
    if reason:
        return _err(reason)
    reason = _spend_lookup(turn)
    if reason:
        return _err(reason)
    if q:
        params["q"] = q[:100]
    r = await turn.backend.options(f["optionsSource"], params)
    if "error" in r:
        return {"error": _explain(r["error"], f["optionsSource"])}
    data = r["data"] or {}
    items = [i for i in (data.get("items") or []) if isinstance(i, dict) and i.get("id") not in (None, "")]
    picked = [{"id": str(i["id"]), "name": clean(i.get("name") or i["id"])} for i in items[:MAX_CANDIDATES]]
    seen = dict(state.get("options_seen") or {})
    key = f"{node_id}|{field_path}"
    seen[key] = {**(seen.get(key) or {}), **{p["id"]: p["name"] for p in picked}}
    state["options_seen"] = seen
    return {"items": picked, "more": bool(data.get("nextCursor")) or len(items) > MAX_CANDIDATES}


async def load_dynamic_fields(turn: Turn, state, node_id: str, field_path: str) -> dict:
    node = turn.node(node_id)
    f = writable_fields(node).get(field_path) if node else None
    if f is None or not f.get("fieldsSource"):
        return _err("동적 칸이 아니에요(get_fields의 dynamic 칸만).")
    params, reason = _inputs(node, field_path, f.get("fieldsInputs"))
    if reason:
        return _err(reason)
    reason = _spend_lookup(turn)
    if reason:
        return _err(reason)
    r = await turn.backend.fields(f["fieldsSource"], params)
    if "error" in r:
        return {"error": _explain(r["error"], f["fieldsSource"])}
    out = []
    # ponytail: 안쪽 칸 50개까지 — 속성이 아주 많은 DB는 뒤쪽 칸을 채팅으로 못 고른다
    for inner in ((r["data"] or {}).get("fields") or [])[:50]:
        if not isinstance(inner, dict) or not isinstance(inner.get("key"), str):
            continue
        item = {"key": inner["key"], "title": clean(inner.get("title") or inner["key"]),
                "type": inner.get("type"), "required": bool(inner.get("required"))}
        if inner.get("choices"):
            item["choices"] = [clean(c.get("name") or c.get("id")) for c in inner["choices"][:MAX_CANDIDATES]
                               if isinstance(c, dict)]
        out.append(item)
    return {"fields": out, "how": f"set_fields({{'{field_path}': {{키: 값}}}})로 넣어요."}


# ── 테스트·완료 ────────────────────────────────────────────────────────────

_PY_TYPE = {str: "string", bool: "boolean", int: "integer", float: "number", dict: "object", list: "list",
            type(None): "null"}
# 참조식에 쓸 수 있는 이름만 LLM에 준다(REF_BODY_RE와 같은 글자) — 외부 데이터가 키 이름으로 들어오는 것도 막는다
_OUTPUT_KEY = re.compile(r"[A-Za-z0-9_]{1,64}")


def _record(state, node_id: str, status: str, h: str, fields: list | None = None) -> None:
    tests = dict(state.get("tests") or {})
    tests[node_id] = {"status": status, "hash": h, **({"fields": fields} if fields is not None else {})}
    state["tests"] = tests


async def test_node(turn: Turn, state, node_id: str) -> dict:
    """노드 테스트(spec §5). LLM이 받는 것은 status와 출력 필드 이름·타입뿐 — 샘플 값·실패 문구는 코드가 응답 끝에 붙인다.
    확인 대기는 turn.pending에만 두고 엔진이 응답을 조립한 뒤 저장한다 — 턴이 오류로 끝나 질문이 안 나가면 대기도 없다."""
    node = turn.node(node_id)
    if node is None:
        return _err("없는 노드예요.")
    kind = how_to_test(node)
    h = node_hash(node)
    label = clean(node.get("label") or node.get("event"))
    if kind == "listen":
        _record(state, node_id, "skipped", h)
        turn.notes.append(f"{label}: 웹훅 트리거는 완료 후 수신 주소로 요청을 보내 테스트할 수 있어요.")
        return {"status": "skipped"}
    errors = node_errors(turn.nodes, turn.edges, node_id)
    if errors:
        return {"status": "blocked", "errors": _brief(errors)}
    if kind == "write":
        c = turn.confirmed
        confirmed = bool(c and c.get("node_id") == node_id and c.get("hash") == h)
        if confirmed and turn.confirm_used:  # 같은 확인으로 또(병렬 호출 포함) — 실행도, 새 확인도 없다
            return {"status": "already_tested", "message": "이번 턴에 이미 테스트했어요. 결과는 시스템이 사용자에게 보여 줘요."}
        if not confirmed:
            # 상류가 skipped면 선택지는 건너뛰기뿐 — 이 대기의 "네"도 건너뛰기로 판정한다(engine.judge_turn_start)
            skip_only = any(node_status(turn.node(a), state) == "skipped" for a in _ancestors(turn, node_id))
            summary = target_summary(node, state.get("options_seen"))
            turn.pending = {"node_id": node_id, "hash": h, "summary": summary, "skip_only": skip_only}
            turn.ask = f"테스트 전 확인 — {summary}. {ASK_SKIP_ONLY if skip_only else ASK_TEST}"
            return {"status": "awaiting_confirmation",
                    "message": "사용자 확인을 기다려요. 확인 질문은 시스템이 붙이니 이번 턴은 짧게 마치세요."}
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    # 이번 턴에 만든 AI 노드는 저장 전 — BE가 저장 때 요청 credentialId를 넣는다(ChatService.java:582-585). 저장된 노드가
    # 비어 있으면 BE가 self-hosted·베타 키로 돌리므로(AgentNodeExecutor.java:126-137) 그대로 BE에 맡긴다
    if (node.get("event") == "ai.reasoning" and node_id in turn.created
            and not str(config.get("credentialId") or "").strip()):
        return {"status": "later", "message": "AI 노드는 저장된 뒤 다음 턴에 테스트할 수 있어요. 사용자에게 다음 메시지를 부탁하세요."}
    if turn.test_runs >= MAX_TESTS_PER_TURN:
        return {"status": "limit", "message": f"이번 턴 테스트 한도({MAX_TESTS_PER_TURN}회)를 다 썼어요. 다음 턴에 이어서 해요."}
    if turn.deadline - time.monotonic() < MIN_TEST_SECONDS:
        return {"status": "later", "message": "이번 턴은 남은 시간이 부족해요. 다음 턴에 테스트하자고 사용자에게 다음 메시지를 부탁하세요."}
    # later·limit·시간 판정을 모두 통과한 뒤에만 확인을 쓴다 — 실행하지 못한 확인은 남겨 둔다
    if kind == "write":
        turn.confirm_used = True  # 확인 한 번 = 실행 한 번. await 전에 막아 병렬 호출도 한 번만 돈다
        state["confirmed"] = None
    turn.test_runs += 1
    r = await turn.backend.test_node(turn.workflow_id, node)
    if "error" in r:
        e = r["error"]
        if e.get("code") == "TEST_SAMPLE_MISSING":
            missing = [m for m in ((e.get("data") or {}).get("missingNodeIds") or []) if isinstance(m, str)]
            if kind != "write" and missing and all(
                    turn.node(m) is not None and node_status(turn.node(m), state) == "skipped" for m in missing):
                _record(state, node_id, "skipped", h)  # 참조 칸은 위 node_errors로 정적 확인을 마쳤다
                return {"status": "skipped", "message": "앞 단계를 건너뛰어 이 노드도 건너뛰었어요."}
            return {"status": "missing_upstream", "missingNodeIds": missing}
        return {"status": "error", "error": _explain(e, node.get("event"))}
    data = r["data"] or {}
    if data.get("status") == "SUCCESS":
        output = data.get("output") if isinstance(data.get("output"), dict) else {}
        fields = [{"key": k, "type": _PY_TYPE.get(type(v), "object")}
                  for k, v in output.items() if isinstance(k, str) and _OUTPUT_KEY.fullmatch(k)][:MAX_CANDIDATES]
        _record(state, node_id, "success", h, [f["key"] for f in fields])
        turn.notes.append(f"{label}: {sample_summary(output)}")
        return {"status": "success", "fields": fields}
    _record(state, node_id, "failed", h)
    turn.notes.append(f"{label}: 테스트 실패 — {clean(data.get('error') or '원인 미상', 120)}")
    if kind == "app_trigger":
        turn.pending = {"node_id": node_id, "hash": h, "summary": label}
        turn.ask = f"{label}의 최근 항목을 가져오지 못했어요. {ASK_TEST}"
    return {"status": "failed"}


def finish(turn: Turn, state) -> dict:
    """완료 판정(spec §5.4). 통과해도 바로 DONE이 아니다 — 켜질 트리거·쓰기 대상을 보여 주고 다음 턴 확인을 받는다."""
    remaining = [{"node_id": n["id"], "label": clean(n.get("label") or ""), "status": node_status(n, state)}
                 for n in turn.nodes if node_status(n, state) not in DONE_STATUSES]
    errors = validate_workflow(turn.nodes, turn.edges)
    if remaining or errors:
        return {"ok": False, "remaining": remaining, "errors": _brief(errors)[:10]}
    summary = finish_summary(turn.nodes, state.get("options_seen"))
    turn.pending = {"node_id": FINISH, "hash": workflow_hash(turn.nodes, turn.edges), "summary": summary}
    turn.ask = f"완료 전 확인 — {summary}. {ASK_FINISH}"
    return {"status": "awaiting_confirmation", "message": "완료 확인은 시스템이 붙이니 이번 턴은 짧게 마치세요."}



# ── LLM에 주는 래퍼 ────────────────────────────────────────────────────────

def make_tools(turn: Turn) -> list:
    """turn을 잡은 도구 12개. 독스트링이 LLM이 보는 설명이다. tool_context는 ADK가 넣는다(LLM 인자 아님)."""

    def find_events_(query: str = "") -> dict:
        """만들 수 있는 노드(이벤트) 메뉴를 찾는다. query는 앱·동작 단어(예: 'slack 보내기', 'calendar').
        맞는 것이 없으면 전체 메뉴를 준다. 여기서 받은 event만 add_node에 쓸 수 있다."""
        return find_events(query)

    def set_outline_(tool_context: ToolContext, steps: list[str], workflowName: str) -> dict:
        """사용자에게 보여 줄 개요(단계 이름 목록)와 워크플로우 이름(한국어, 짧게)을 저장한다. 첫 턴에 부른다."""
        return set_outline(tool_context.state, steps, workflowName)

    async def check_connection_(app: str) -> dict:
        """앱(find_events의 app 값) 연결 여부. 연결이 없으면 message를 사용자에게 그대로 안내하고 노드를 만들지 않는다."""
        return await check_connection(turn, app)

    async def add_node_(event: str, label: str, after: str = "", branch: str = "") -> dict:
        """노드를 하나 만든다. after = 앞 노드 id(그 뒤에 연결), 앞 노드가 분기(branch.if)면 branch='true'|'false' 필수.
        연결이 필요한 앱이 미연결이면 거절한다. 돌려준 node_id를 이후 도구에 쓴다."""
        return await add_node(turn, event, label, after, branch)

    def get_fields_(tool_context: ToolContext, node_id: str) -> dict:
        """노드의 칸 목록(path·제목·타입·필수·선택지·드롭다운·동적 칸·현재 값), 앞 노드들의 출력 이름, 이 노드의 검증 에러."""
        return get_fields(turn, tool_context.state, node_id)

    async def list_options_(tool_context: ToolContext, node_id: str, field_path: str, q: str = "") -> dict:
        """드롭다운 칸(dropdown)의 실제 후보 {id, name}을 최대 20개 조회한다. q로 이름을 좁힌다. 턴당 조회 5회까지."""
        return await list_options(turn, tool_context.state, node_id, field_path, q)

    async def load_dynamic_fields_(tool_context: ToolContext, node_id: str, field_path: str) -> dict:
        """동적 칸(dynamic, 예: Notion DB 속성)의 안쪽 칸 목록을 조회한다. 값은 set_fields로 {키: 값} 객체를 넣는다."""
        return await load_dynamic_fields(turn, tool_context.state, node_id, field_path)

    def set_fields_(tool_context: ToolContext, node_id: str, values: dict) -> dict:
        """칸 값을 쓴다. values = {get_fields의 path: 값}. 표시 이름은 'config._names.<칸>'. 앞 노드 값은
        '{{nodes.<id>.output.<이름>}}'. 거절된 칸(rejected)과 노드 검증 에러(errors)를 돌려준다."""
        return set_fields(turn, tool_context.state, node_id, values)

    def connect_(source: str, target: str, branch: str = "") -> dict:
        """두 노드를 잇는다. source가 분기(branch.if)면 branch='true'|'false' 필수."""
        return connect(turn, source, target, branch)

    def remove_node_(node_id: str) -> dict:
        """노드와 그 연결을 지운다. 마지막 남은 노드는 지울 수 없다."""
        return remove_node(turn, node_id)

    async def test_node_(tool_context: ToolContext, node_id: str) -> dict:
        """노드를 테스트한다. 쓰기 노드는 사용자 확인을 먼저 받는다(awaiting_confirmation이면 그 턴은 짧게 마친다).
        결과는 status와 출력 이름·타입뿐이다 — 샘플 내용은 시스템이 사용자에게 직접 보여 준다."""
        return await test_node(turn, tool_context.state, node_id)

    def finish_(tool_context: ToolContext) -> dict:
        """모든 노드 테스트(또는 건너뛰기)·검증이 끝났는지 보고, 끝났으면 사용자에게 완료 확인을 받는다."""
        return finish(turn, tool_context.state)

    fns = [find_events_, set_outline_, check_connection_, add_node_, get_fields_, list_options_,
           load_dynamic_fields_, set_fields_, connect_, remove_node_, test_node_, finish_]
    for fn in fns:
        fn.__name__ = fn.__name__.rstrip("_")
    return fns

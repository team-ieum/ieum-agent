"""워크플로우 정적 검증(로드맵 ④) — 실행 없이 노드별 에러를 **모아서** 돌려준다.

spec: ieum-backend docs/superpowers/specs/2026-10-12-node-static-validation-design.md §3·§4.
POST /v1/workflows/validate가 쓴다. 채팅·generate 경로(WorkflowValidator.validate)는 이 모듈을 쓰지
않는다 — 슬롯으로 고칠 수 없는 칸 때문에 채팅 수정이 영구히 막히는 것을 피한다(spec §3.8, ⑤에서 붙인다).
에러 하나 = {nodeId, field, code, message}. code 고정 목록은 spec §4.2."""
import json
import re

from core.template_registry import _get_by_path, _match_view, match_entry
from core.validators.workflow_validator import REF_BODY_RE

# 중괄호 없는 본문만 — `.*?`는 닫히지 않은 `{{` 반복에서 제곱 시간이 걸린다(모든 실행 경로가 부른다)
_REF_RE = re.compile(r"\{\{([^{}]*)\}\}")

_EMPTY = (None, "", [])
# BE ConditionNodeExecutor: isEmpty·isNotEmpty는 right를 보지 않는다. 튜플 — 저장된 operator가 list·dict여도
# `in`이 해시하지 않아 예외가 나지 않는다.
_RIGHT_UNUSED_OPERATORS = ("isEmpty", "isNotEmpty")
# BE ConditionNodeExecutor는 left/right가 없으면 옛 표기 leftValue/rightValue를 읽는다
_CONDITION_ALIASES = {"config.left": "config.leftValue", "config.right": "config.rightValue"}
_TOOL_FIELD_PREFIX = "config.tools.0.config."
# spec §1: AI 출력 칸 규칙은 실행 시점(INVALID_OUTPUT_FIELDS, core/output_fields.py) 몫 — 칸 검사에서 뺀다
_SKIPPED_AI_FIELDS = ("config.outputFields",)


def _err(code: str, message: str, node_id=None, field: str | None = None) -> dict:
    return {"nodeId": None if node_id is None else str(node_id), "field": field, "code": code, "message": message}


def _sid(value) -> str | None:
    """노드 id·엣지 끝점으로 쓸 수 있는 값(비어 있지 않은 문자열)만 돌려준다. 깨진 입력(숫자·목록)은 None."""
    return value if isinstance(value, str) and value else None


def validate_workflow(nodes: list, edges: list) -> list[dict]:
    """워크플로우 전체 검증. 에러가 없으면 빈 목록. 어떤 입력에도 예외를 내지 않는다."""
    nodes = [n for n in (nodes or []) if isinstance(n, dict)]
    edges = [e for e in (edges or []) if isinstance(e, dict)]
    errors = _graph_errors(nodes, edges)
    by_id = {_sid(n.get("id")): n for n in nodes if _sid(n.get("id"))}
    preds = _predecessors(edges, set(by_id))
    for node in nodes:
        errors += field_errors(node)
        errors += _ref_errors(node, by_id, preds)
    return errors


# ── 그래프 (spec §3.9) ─────────────────────────────────────────────

def _graph_errors(nodes: list, edges: list) -> list[dict]:
    if not nodes:
        return [_err("TRIGGER_COUNT", "워크플로우에 노드가 없습니다.")]
    errors = []
    seen, triggers = set(), []
    for idx, node in enumerate(nodes):
        nid, ntype = _sid(node.get("id")), node.get("type")
        if nid is None:
            errors.append(_err("EDGE_INVALID", f"{idx + 1}번째 노드에 id가 없습니다."))
            continue
        if nid in seen:
            errors.append(_err("EDGE_INVALID", f"노드 id '{nid}'가 중복됩니다.", nid))
        seen.add(nid)
        if not isinstance(ntype, str) or not ntype:
            errors.append(_err("EDGE_INVALID", f"노드 '{nid}'에 type이 없습니다.", nid))
            continue
        if ntype.upper() == "TRIGGER":
            triggers.append(node)
            errors += _cron_errors(node)
    if len(triggers) != 1:
        errors.append(_err("TRIGGER_COUNT", f"트리거는 1개여야 합니다(현재 {len(triggers)}개)."))
    trigger_id = triggers[0].get("id") if len(triggers) == 1 else None
    for idx, edge in enumerate(edges):
        source, target = _sid(edge.get("source")), _sid(edge.get("target"))
        if source is None or target is None:
            errors.append(_err("EDGE_INVALID", f"{idx + 1}번째 연결에 출발 또는 도착 노드가 없습니다."))
        elif source not in seen or target not in seen:
            missing = source if source not in seen else target
            errors.append(_err("EDGE_INVALID", f"연결({source} → {target})이 없는 노드 '{missing}'를 가리킵니다."))
        elif target == trigger_id:
            errors.append(_err("EDGE_INVALID", "트리거로 들어오는 연결은 둘 수 없습니다.", trigger_id))
    if trigger_id is not None:
        adj: dict = {}
        for edge in edges:
            source, target = _sid(edge.get("source")), _sid(edge.get("target"))
            if source in seen and target in seen:
                adj.setdefault(source, []).append(target)
        reached, stack = set(), [trigger_id]
        while stack:
            cur = stack.pop()
            if cur not in reached:
                reached.add(cur)
                stack.extend(adj.get(cur, []))
        for node in nodes:
            nid = _sid(node.get("id"))
            if nid is not None and nid not in reached:
                errors.append(_err("ORPHAN_NODE", "트리거에서 이어지지 않은 노드입니다.", nid))
    return errors


_CRON_PART_RE = re.compile(r"^[0-9A-Za-z*/,\-?LW#]+$")


def _cron_errors(node: dict) -> list[dict]:
    """SCHEDULE 트리거의 cron 형식. 비어 있으면 REQUIRED 몫(trigger.schedule 항목의 cron 칸)."""
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    cron = config.get("cron")
    if config.get("triggerType") != "SCHEDULE" or cron in _EMPTY:
        return []
    if not isinstance(cron, str):
        return [_err("CRON", "cron 표현식은 문자열이어야 합니다.", node.get("id"), "config.cron")]
    # BE CronConverter.toQuartz(:25-30)와 같은 필드 수 규칙: 5(Unix) 또는 6/7(Quartz). REST 저장은
    # Quartz 6/7필드를 받으므로 5필드 전용 WorkflowValidator._validate_cron을 쓰면 오탐한다.
    # ponytail: 필드 수·문자 집합만 본다. 값 범위(분 0-59 등)는 저장 시 BE CronExpression이 검증.
    parts = cron.split()
    if len(parts) not in (5, 6, 7) or not all(_CRON_PART_RE.match(p) for p in parts):
        return [_err("CRON", f"cron 표현식 '{cron}'이 올바르지 않습니다(5필드 또는 Quartz 6·7필드).",
                     node.get("id"), "config.cron")]
    return []


# ── 칸 검사 (spec §3.2-3.6) ────────────────────────────────────────

def field_errors(node: dict) -> list[dict]:
    """노드 하나의 칸 검사. 카탈로그 항목이 매칭되지 않는 노드는 건너뛴다(spec §3.2)."""
    entry = match_entry(node)
    if entry is None:
        return []
    view = _match_view(node)
    nid = _sid(node.get("id"))
    errors = []
    for f in entry["inputFields"]:
        path = f["path"]
        if entry["node_type"] == "AI" and path in _SKIPPED_AI_FIELDS:
            continue
        value = _get_by_path(view, path)
        # AI 노드의 도구 칸은 비우면 "AI가 결정" — 생성 슬롯도 항상 선택이다(node_fields.derive_slots).
        # 채운 값은 형식 검사를 받는다.
        if (value in _EMPTY and entry["node_type"] == "AI" and entry["tool_key"]
                and path.startswith(_TOOL_FIELD_PREFIX)):
            continue
        if value in _EMPTY and entry["node_type"] == "CONDITION" and path in _CONDITION_ALIASES:
            value = _get_by_path(view, _CONDITION_ALIASES[path])
        if (path == "config.right" and entry["node_type"] == "CONDITION"
                and _get_by_path(view, "config.operator") in _RIGHT_UNUSED_OPERATORS):
            continue
        # BE HttpNodeExecutor는 webhookCredentialId가 채워져 있으면 url을 읽지 않는다(BE-62: url에 웹훅 주소 금지)
        if (path == "config.url" and entry["node_type"] == "HTTP"
                and str(_get_by_path(view, "config.webhookCredentialId") or "").strip()):
            continue
        errors += _check(f, value, path, nid)
    return errors


def _check(f: dict, value, path: str, nid) -> list[dict]:
    title = f.get("title") or f["key"]
    if value in _EMPTY:
        if f.get("required") and "default" not in f:
            verb = "선택" if f.get("choices") or f.get("optionsSource") else "입력"
            return [_err("REQUIRED", f"'{title}' 칸을 {verb}하세요.", nid, path)]
        return []
    if f.get("list"):
        if not isinstance(value, list):
            return [_err("TYPE", f"'{title}' 칸은 목록이어야 합니다.", nid, path)]
        errors = []
        for i, item in enumerate(value):
            if f.get("children") and isinstance(item, dict):
                for child in f["children"]:
                    errors += _check(child, item.get(child["key"]), f"{path}.{i}.{child['key']}", nid)
        return errors
    if isinstance(value, str) and "{{" in value:
        if f.get("ref") is False:
            return [_err("REF_NOT_ALLOWED", f"'{title}' 칸에는 앞 단계 값을 넣을 수 없어요.", nid, path)]
        return []  # 실행해야 값이 정해진다
    if not _type_ok(f["type"], value):
        return [_err("TYPE", f"'{title}' 칸의 값 형식이 맞지 않아요({f['type']}).", nid, path)]
    choices = f.get("choices")
    # 목록 칸은 위에서 끝났다 — 여기 dict·list가 오면 그 자체로 틀린 값이고, 집합 비교 전에 걸러 해시 예외를 피한다
    if choices and (isinstance(value, (dict, list)) or value not in {c["id"] for c in choices}):
        return [_err("CHOICE", f"'{title}' 칸의 값 '{value}'은(는) 고를 수 있는 값이 아니에요.", nid, path)]
    return []


def _type_ok(ftype: str, v) -> bool:
    if ftype == "integer":
        return (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, str) and _parses(int, v))
    if ftype == "number":
        return (isinstance(v, (int, float)) and not isinstance(v, bool)) or (isinstance(v, str) and _parses(float, v))
    if ftype == "boolean":
        return isinstance(v, bool)
    if ftype in ("dict", "object"):
        return isinstance(v, dict)
    if ftype == "json":  # 도구 인자(values·filter_json·headers_json)는 JSON 문자열로 저장된다
        return isinstance(v, (dict, list)) or (isinstance(v, str) and _parses(json.loads, v))
    return True  # string·text·datetime, cron(_cron_errors 몫)


def _parses(fn, s: str) -> bool:
    try:
        fn(s)
        return True
    except (ValueError, RecursionError):  # json.JSONDecodeError도 ValueError, 깊은 중첩 JSON은 RecursionError
        return False


# ── 참조식 (spec §3.7) ─────────────────────────────────────────────

def _predecessors(edges: list, ids: set) -> dict:
    preds: dict = {nid: [] for nid in ids}
    for edge in edges:
        source, target = _sid(edge.get("source")), _sid(edge.get("target"))
        if target in preds and source in ids:
            preds[target].append(source)
    return preds


def _ancestors(nid, preds: dict) -> set:
    seen, stack = set(), list(preds.get(nid, []))
    while stack:
        cur = stack.pop()
        if cur not in seen:
            seen.add(cur)
            stack.extend(preds.get(cur, []))
    return seen


def _iter_strings(value, path: str):
    """(위치 경로, 문자열)을 재귀로 낸다."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _iter_strings(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _iter_strings(v, f"{path}.{i}")


def _declared_outputs(node: dict) -> set | None:
    """대상 노드가 내는 최상위 출력 키. 알 수 없으면(미매칭·outputDynamic) None = 검사 안 함."""
    entry = match_entry(node)
    if entry is None or entry.get("outputDynamic"):
        return None
    source = entry.get("outputsFrom")
    if source:
        value = _get_by_path(node, source)
        if isinstance(value, dict) and value:
            return set(value)
        if isinstance(value, list) and value:  # 출력 칸 목록이 정적 outputFields 전체를 대체(③ spec)
            # 칸 형식은 검사하지 않는다(spec §1) — 깨진 key(list 등)는 해시 예외 대신 버린다
            return {f["key"] for f in value if isinstance(f, dict) and isinstance(f.get("key"), str)}
    return {f["key"] for f in entry["outputFields"]}


def _ref_errors(node: dict, by_id: dict, preds: dict) -> list[dict]:
    nid = _sid(node.get("id"))
    config = node.get("config")
    if nid is None or not isinstance(config, dict):
        return []
    entry = match_entry(node)
    field_paths = [f["path"] for f in entry["inputFields"]] if entry else []
    errors, anc = [], None
    for loc, text in _iter_strings(config, "config"):
        if "{{" not in text:
            continue
        field = next((p for p in field_paths if loc == p or loc.startswith(p + ".")), None)
        if text.rfind("{{") > text.rfind("}}"):
            errors.append(_err("REF_FORMAT", "참조식의 '{{'가 닫히지 않았어요.", nid, field))
        for raw in _REF_RE.findall(text):
            m = REF_BODY_RE.fullmatch(raw)
            if not m:
                errors.append(_err("REF_FORMAT", f"참조식 '{{{{{raw}}}}}'의 형식이 맞지 않아요. "
                                                 "'{{nodes.<노드ID>.output.<칸>}}' 모양이어야 해요.", nid, field))
                continue
            target, out_path = m.group(1), m.group(2)
            if target not in by_id:
                errors.append(_err("REF_UNKNOWN_NODE", f"없는 노드 '{target}'를 참조해요.", nid, field))
                continue
            if target == nid:
                errors.append(_err("REF_SELF", "자기 자신의 결과는 참조할 수 없어요.", nid, field))
                continue
            if anc is None:
                anc = _ancestors(nid, preds)
            if target not in anc:
                errors.append(_err("REF_NOT_UPSTREAM", f"앞 단계가 아닌 노드 '{target}'를 참조해요.", nid, field))
                continue
            declared = _declared_outputs(by_id[target])
            head = out_path.split(".", 1)[0]
            if declared is not None and head not in declared:
                errors.append(_err("REF_UNKNOWN_FIELD", f"노드 '{target}'에는 '{head}' 결과가 없어요.", nid, field))
    return errors

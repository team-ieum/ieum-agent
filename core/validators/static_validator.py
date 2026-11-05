"""워크플로우 정적 검증(로드맵 ④) — 실행 없이 노드별 에러를 **모아서** 돌려준다.

spec: ieum-backend docs/superpowers/specs/2026-10-12-node-static-validation-design.md §3·§4.
POST /v1/workflows/validate와 대화형 생성(core/builder — 노드 검증·finish)이 쓴다.
에러 하나 = {nodeId, field, code, message}. code 고정 목록은 spec §4.2."""
import json
import re

from core.template_registry import _get_by_path, match_entry, trigger_type_of

# 참조식 본문 {{ 여기 }}. BE 실행기(ExecutionCursor.VARIABLE_PATTERN)와 같은 규칙 — 실제로 치환하는 쪽이 진실이다.
# id [a-zA-Z0-9-]+, 경로 [a-zA-Z0-9_.]+(중첩 issues.0.title 허용). 옛 workflow_validator.py에서 옮겼다.
REF_BODY_RE = re.compile(r"nodes\.([a-zA-Z0-9-]+)\.output\.([a-zA-Z0-9_.]+)")

# 중괄호 없는 본문만 — `.*?`는 닫히지 않은 `{{` 반복에서 제곱 시간이 걸린다(모든 실행 경로가 부른다)
_REF_RE = re.compile(r"\{\{([^{}]*)\}\}")

_EMPTY = (None, "", [])
# BE ConditionNodeExecutor: isEmpty·isNotEmpty·isTrue·isFalse는 right를 보지 않는다. 튜플 — 저장된 operator가 list·dict여도
# `in`이 해시하지 않아 예외가 나지 않는다.
_RIGHT_UNUSED_OPERATORS = ("isEmpty", "isNotEmpty", "isTrue", "isFalse")
# 레거시 분기 3칸 — 카탈로그는 선택(갈래만 쓴 노드)이고, 갈래가 없을 때만 검증이 필수로 건다
_LEGACY_CONDITION_PATHS = ("config.operator", "config.left", "config.right")
# 분기 엣지 conditionType 예약어 — 갈래 key가 될 수 없다
_RESERVED_BRANCH_KEYS = ("else", "true", "false")
MAX_BRANCHES = MAX_RULES = 10
# BE ConditionNodeExecutor는 left/right가 없으면 옛 표기 leftValue/rightValue를 읽는다
_CONDITION_ALIASES = {"config.left": "config.leftValue", "config.right": "config.rightValue"}
# AI 프리셋 도구 칸 경로. ACTION 입력 칸은 config.<칸>이라 이 접두사 밖이다(spec §4).
_TOOL_FIELD_PREFIX = "config.tools.0.config."
# spec §1: AI 출력 칸 규칙은 실행 시점(INVALID_OUTPUT_FIELDS, core/output_fields.py) 몫 — 칸 검사에서 뺀다
_SKIPPED_AI_FIELDS = ("config.outputFields",)
# spec §7-15: 검증 비용(조상 탐색 O(N²))·응답 크기 상한. BE는 3초 뒤 fail-open해도 agent CPU는 계속 돌고,
# 응답이 BE WebClient 기본 버퍼 256KB를 넘으면 파싱 실패 = fail-open으로 차단이 거꾸로 풀린다.
MAX_NODES, MAX_EDGES, MAX_ERRORS, MAX_VALUE_LEN = 500, 2000, 200, 80
# 200건이어도 제어문자 값(JSON에서 문자당 6바이트)이면 256KB를 넘는다 — 바이트로도 자른다. json.dumps 기본
# ensure_ascii=True는 한글도 6바이트로 세서 실제 응답(UTF-8 3바이트)보다 크게 잡는다.
MAX_ERROR_BYTES = 200_000
# 위치 경로 상한 — 카탈로그 path(최대 41자)와 비교하고 동적 칸 오류의 field 문자열도 된다. 긴 키 아래 큰 목록에서
# 원소마다 긴 경로를 복사하지 않게, 응답 field가 길어지지 않게 자른다(긴 키는 잘린다)
_MAX_LOC = 256


def _err(code: str, message: str, node_id=None, field: str | None = None) -> dict:
    # nodeId도 사용자 값 — 긴 id 하나로 응답이 버퍼를 넘지 않게 자른다(정상 id는 UUID 36자)
    return {"nodeId": None if node_id is None else _clip(node_id), "field": field, "code": code, "message": message}


def _clip(value) -> str:
    """메시지에 싣는 사용자 값 — MAX_VALUE_LEN자를 넘으면 자르고 '…'."""
    s = str(value)
    return s if len(s) <= MAX_VALUE_LEN else s[:MAX_VALUE_LEN] + "…"


def _sid(value) -> str | None:
    """노드 id·엣지 끝점으로 쓸 수 있는 값(비어 있지 않은 문자열)만 돌려준다. 깨진 입력(숫자·목록)은 None."""
    return value if isinstance(value, str) and value else None


def validate_workflow(nodes: list, edges: list) -> list[dict]:
    """워크플로우 전체 검증. 에러가 없으면 빈 목록. 어떤 입력에도 예외를 내지 않는다.
    같은 에러는 처음 하나만 남기고 MAX_ERRORS건·MAX_ERROR_BYTES에서 자른다(spec §7-15). 최소 1건은 남긴다."""
    nodes, edges = nodes or [], edges or []
    if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES:  # 원본 길이 — dict 아닌 원소를 거르기 전
        return [_err("TOO_LARGE", f"노드는 {MAX_NODES}개, 연결은 {MAX_EDGES}개까지 검증할 수 있어요"
                                  f"(현재 노드 {len(nodes)}개, 연결 {len(edges)}개).")]
    nodes = [n for n in nodes if isinstance(n, dict)]
    edges = [e for e in edges if isinstance(e, dict)]
    errors = _graph_errors(nodes, edges)
    by_id = {_sid(n.get("id")): n for n in nodes if _sid(n.get("id"))}
    preds = _predecessors(edges, set(by_id))
    outputs: dict = {}  # 대상 id → _declared_outputs, 대상당 한 번만 계산

    def found():
        yield from errors
        for node in nodes:
            yield from field_errors(node)  # 노드당 칸 수만큼 — 작다
            yield from _ref_errors(node, by_id, preds, outputs)

    # 수집하면서 거른다 — 다 만든 뒤 자르면 같은 참조 수백만 개가 에러 dict 수백만 개로 메모리를 먹는다.
    # dict는 키가 처음 들어온 순서를 지킨다.
    unique, size = {}, 0
    for e in found():
        key = (e["nodeId"], e["field"], e["code"], e["message"])
        if key in unique:
            continue
        size += len(json.dumps(e))
        if unique and size > MAX_ERROR_BYTES:
            break
        unique[key] = e
        if len(unique) == MAX_ERRORS:
            break
    return list(unique.values())


# ── 그래프 (spec §3.9) ─────────────────────────────────────────────

def _graph_errors(nodes: list, edges: list) -> list[dict]:
    if not nodes:
        return [_err("TRIGGER_COUNT", "워크플로우에 노드가 없습니다.")]
    errors = []
    seen, triggers, first = set(), [], {}
    for idx, node in enumerate(nodes):
        nid, ntype = _sid(node.get("id")), node.get("type")
        if nid is None:
            errors.append(_err("EDGE_INVALID", f"{idx + 1}번째 노드에 id가 없습니다."))
            continue
        if nid in seen:
            errors.append(_err("EDGE_INVALID", f"노드 id '{_clip(nid)}'가 중복됩니다.", nid))
        seen.add(nid)
        first.setdefault(nid, node)
        if not isinstance(ntype, str) or not ntype:
            errors.append(_err("EDGE_INVALID", f"노드 '{_clip(nid)}'에 type이 없습니다.", nid))
            continue
        if ntype.upper() == "TRIGGER":
            triggers.append(node)
            errors += _cron_errors(node)
    if len(triggers) != 1:
        errors.append(_err("TRIGGER_COUNT", f"트리거는 1개여야 합니다(현재 {len(triggers)}개)."))
    trigger_id = triggers[0].get("id") if len(triggers) == 1 else None
    allowed: dict = {}  # 출발 노드 id → _branch_allowed, id당 한 번만 계산
    for idx, edge in enumerate(edges):
        source, target = _sid(edge.get("source")), _sid(edge.get("target"))
        if source is None or target is None:
            errors.append(_err("EDGE_INVALID", f"{idx + 1}번째 연결에 출발 또는 도착 노드가 없습니다."))
        elif source not in seen or target not in seen:
            missing = source if source not in seen else target
            errors.append(_err("EDGE_INVALID", f"연결({_clip(source)} → {_clip(target)})이 없는 노드 '{_clip(missing)}'를 가리킵니다."))
        elif target == trigger_id:
            errors.append(_err("EDGE_INVALID", "트리거로 들어오는 연결은 둘 수 없습니다.", trigger_id))
        if source in seen and target in seen:
            if source not in allowed:
                allowed[source] = _branch_allowed(first[source])
            if allowed[source] is not None:
                errors += _branch_edge_errors(source, target, edge.get("conditionType"), *allowed[source])
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


def condition_branches(node: dict) -> list[tuple[str, str]]:
    """분기 노드에서 나가는 엣지가 쓸 수 있는 (conditionType 값, 이름). 새 모양 = 갈래 key(비어 있지 않은 문자열)들
    + otherwise가 stop이 아니면 else. 레거시(branches 빈 값)는 otherwise를 무시하고 true·false."""
    branches = _get_by_path(node, "config.branches")
    if branches in _EMPTY:
        return [("true", "참"), ("false", "그 외")]
    out = [(br["key"], str(br.get("name") or "")) for br in (branches if isinstance(branches, list) else [])
           if isinstance(br, dict) and isinstance(br.get("key"), str) and br["key"]]
    if _get_by_path(node, "config.otherwise") != "stop":
        out.append(("else", "그 외"))
    return out


_MAX_LISTED_BRANCHES = 10  # 메시지에 싣는 허용값 개수 — 갈래 상한을 넘긴 노드도 메시지가 커지지 않게


def _branch_allowed(node: dict):
    """CONDITION 노드면 (허용값 set, 메시지용 허용값 표기, otherwise == stop), 아니면 None."""
    entry = match_entry(node)
    if entry is None or entry["node_type"] != "CONDITION":
        return None
    branches = condition_branches(node)
    listed = ", ".join(f"'{_clip(v)}'({_clip(n)})" for v, n in branches[:_MAX_LISTED_BRANCHES])
    if len(branches) > _MAX_LISTED_BRANCHES:
        listed += f" 외 {len(branches) - _MAX_LISTED_BRANCHES}개"
    stop = _is_branched(node) and _get_by_path(node, "config.otherwise") == "stop"
    return {v for v, _ in branches}, listed or "없음", stop


def _branch_edge_errors(source: str, target: str, ct, values: set, listed: str, stop: bool) -> list[dict]:
    """분기 엣지의 conditionType — 허용값 밖이면 실행기가 절대 따라가지 않는 연결이다.
    출발·도착·받은 값을 메시지에 넣는다: 같은 메시지는 하나로 합쳐지고, finish는 nodeId를 버린다."""
    if isinstance(ct, str) and ct in values:  # 문자열만 set에 넣어 본다 — 목록·dict는 해시 예외
        return []
    where = f"분기 '{_clip(source)}' → '{_clip(target)}' 연결"
    if stop and ct == "else":
        msg = f"{where}: 그 외는 '여기서 멈추기'라 'else' 연결은 실행되지 않아요. 쓸 수 있는 값: {listed}."
    else:
        got = "없음" if ct is None else f"'{_clip(ct)}'"
        msg = f"{where}의 갈래 값({got})이 맞지 않아요. 쓸 수 있는 값: {listed}."
    return [_err("EDGE_INVALID", msg, source)]


_CRON_PART_RE = re.compile(r"^[0-9A-Za-z*/,\-?LW#]+$")


def _cron_errors(node: dict) -> list[dict]:
    """SCHEDULE 트리거의 cron 형식. 비어 있으면 REQUIRED 몫(schedule.every 항목의 cron 칸)."""
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    cron = config.get("cron")
    if trigger_type_of(node) != "SCHEDULE" or cron in _EMPTY:
        return []
    if not isinstance(cron, str):
        return [_err("CRON", "cron 표현식은 문자열이어야 합니다.", node.get("id"), "config.cron")]
    # BE CronConverter.toQuartz(:25-30)와 같은 필드 수 규칙: 5(Unix) 또는 6/7(Quartz). REST 저장은
    # Quartz 6/7필드를 받으므로 5필드만 받으면 오탐한다.
    # ponytail: 필드 수·문자 집합만 본다. 값 범위(분 0-59 등)는 저장 시 BE CronExpression이 검증.
    parts = cron.split()
    if len(parts) not in (5, 6, 7) or not all(_CRON_PART_RE.match(p) for p in parts):
        return [_err("CRON", f"cron 표현식 '{_clip(cron)}'이 올바르지 않습니다(5필드 또는 Quartz 6·7필드).",
                     node.get("id"), "config.cron")]
    return []


# ── 칸 검사 (spec §3.2-3.6) ────────────────────────────────────────

def field_errors(node: dict) -> list[dict]:
    """노드 하나의 칸 검사. event가 없거나 agent가 모르는 event(BE 카탈로그 ⊇ agent 카탈로그)인 노드는
    건너뛴다(spec §3.2, 노드 event spec §4). 값은 노드 원본에서 읽는다 — 옛 저장 모양 정규화는 없다."""
    entry = match_entry(node)
    if entry is None:
        return []
    nid = _sid(node.get("id"))
    errors = []
    for f in entry["inputFields"]:
        path = f["path"]
        if entry["node_type"] == "AI" and path in _SKIPPED_AI_FIELDS:
            continue
        value = _get_by_path(node, path)
        # AI 노드의 도구 칸은 비우면 "AI가 결정"이라 선택이다.
        # 채운 값은 형식 검사를 받는다.
        if (value in _EMPTY and entry["node_type"] == "AI" and entry["tool_key"]
                and path.startswith(_TOOL_FIELD_PREFIX)):
            continue
        # 모델은 비우면 agent가 provider 기본 모델로 실행한다(BE AgentNodeExecutor는 null을 그대로 넘김).
        # IEUM-AI-55 이전 저장분은 model 키가 없다 — dev 사전 점검에서 13건 모두 이 오탐이었다(spec §7-17).
        if value in _EMPTY and entry["node_type"] == "AI" and path == "config.model":
            continue
        if value in _EMPTY and entry["node_type"] == "CONDITION" and path in _CONDITION_ALIASES:
            value = _get_by_path(node, _CONDITION_ALIASES[path])
        if _unused_field(entry, node, path):
            continue
        if entry["node_type"] == "CONDITION" and path in _LEGACY_CONDITION_PATHS:
            f = {**f, "required": True}
        if entry["node_type"] == "CONDITION" and path == "config.branches" and isinstance(value, list):
            # 상한 밖 원소는 TOO_LARGE(_condition_errors)라 내용을 보지 않는다 — 원소마다 에러를 만들지 않게.
            value = [{**b, "rules": b["rules"][:MAX_RULES]} if isinstance(b, dict) and isinstance(b.get("rules"), list)
                     else b for b in value[:MAX_BRANCHES]]
        errors += _check(f, value, path, nid)
    if entry["node_type"] == "CONDITION":
        errors += _condition_errors(node, nid)
    return errors


def _is_branched(node: dict) -> bool:
    """새 모양 분기 — config.branches가 비어 있지 않다(목록이 아니어도 새 모양, TYPE은 _check 몫)."""
    return _get_by_path(node, "config.branches") not in _EMPTY


def _condition_errors(node: dict, nid) -> list[dict]:
    """갈래 규칙 중 칸 정의(_check)로 못 거는 것: 개수 상한·원소 형식·key 중복·예약어·이름 중복·조건 기준값.
    key·name은 None·""만 빈 값(REQUIRED는 _check 몫) — 그 밖의 문자열 아닌 값은 TYPE."""
    branches = _get_by_path(node, "config.branches")
    if not isinstance(branches, list):
        return []
    errors = []
    if len(branches) > MAX_BRANCHES:
        errors.append(_err("TOO_LARGE", f"갈래는 {MAX_BRANCHES}개까지 만들 수 있어요.", nid, "config.branches"))
    keys, names = set(), set()
    for i, br in enumerate(branches[:MAX_BRANCHES]):
        loc = f"config.branches.{i}"
        if not isinstance(br, dict):
            errors.append(_err("TYPE", "갈래는 객체여야 합니다.", nid, loc))
            continue
        key, name, rules = br.get("key"), br.get("name"), br.get("rules")
        for k, v in (("key", key), ("name", name)):
            if v not in (None, "") and not isinstance(v, str):
                errors.append(_err("TYPE", f"갈래 {k}는 문자열이어야 합니다.", nid, f"{loc}.{k}"))
        if isinstance(rules, list):
            if len(rules) > MAX_RULES:
                errors.append(_err("TOO_LARGE", f"갈래 하나에 조건은 {MAX_RULES}개까지 넣을 수 있어요.",
                                   nid, f"{loc}.rules"))
            for j, rule in enumerate(rules[:MAX_RULES]):
                if not isinstance(rule, dict):
                    errors.append(_err("TYPE", "조건은 객체여야 합니다.", nid, f"{loc}.rules.{j}"))
                elif rule.get("operator") not in _RIGHT_UNUSED_OPERATORS and rule.get("right") in _EMPTY:
                    errors.append(_err("REQUIRED", "조건의 '기준값' 칸을 입력하세요.", nid, f"{loc}.rules.{j}.right"))
        if isinstance(key, str) and key:
            if key in _RESERVED_BRANCH_KEYS or key in keys:
                errors.append(_err("CHOICE", f"갈래 key '{_clip(key)}'은(는) 쓸 수 없어요(예약어 또는 중복).",
                                   nid, f"{loc}.key"))
            keys.add(key)
        if isinstance(name, str) and name:
            if name in names:
                errors.append(_err("CHOICE", f"갈래 이름 '{_clip(name)}'이(가) 겹쳐요.", nid, f"{loc}.name"))
            names.add(name)
    return errors


def _unused_field(entry: dict, node: dict, path: str) -> bool:
    """실행기가 읽지 않는 칸 — 칸 검사와 참조 검사가 함께 건너뛴다(spec §7-16)."""
    # 새 모양 분기는 레거시 3칸을 읽지 않는다
    if entry["node_type"] == "CONDITION" and path in _LEGACY_CONDITION_PATHS and _is_branched(node):
        return True
    if path == "config.right" and entry["node_type"] == "CONDITION":
        return _get_by_path(node, "config.operator") in _RIGHT_UNUSED_OPERATORS
    # BE HttpNodeExecutor는 webhookCredentialId가 채워져 있으면 url을 읽지 않는다(BE-62: url에 웹훅 주소 금지)
    if path == "config.url" and entry["node_type"] == "HTTP":
        return bool(str(_get_by_path(node, "config.webhookCredentialId") or "").strip())
    return False


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
        return [_err("CHOICE", f"'{title}' 칸의 값 '{_clip(value)}'은(는) 고를 수 있는 값이 아니에요.", nid, path)]
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
    """(위치 경로, 문자열)을 전위 순서로 낸다. 명시 스택 — 재귀 yield from은 문자열 하나를 깊이만큼 올려 보내
    깊은 중첩 × 많은 문자열에서 BE 3초 타임아웃을 넘겼다."""
    stack = [(path, value)]
    while stack:
        loc, cur = stack.pop()
        if isinstance(cur, str):
            yield loc, cur
        elif isinstance(cur, dict):
            stack.extend((f"{loc}.{k}"[:_MAX_LOC], v) for k, v in reversed(list(cur.items())))
        elif isinstance(cur, list):
            stack.extend((f"{loc}.{i}"[:_MAX_LOC], cur[i]) for i in range(len(cur) - 1, -1, -1))


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


def _ref_errors(node: dict, by_id: dict, preds: dict, outputs: dict):
    """참조식 에러를 하나씩 낸다(참조 수에 비례해 많을 수 있다). outputs: 대상 id → _declared_outputs 캐시."""
    nid = _sid(node.get("id"))
    config = node.get("config")
    if nid is None or not isinstance(config, dict):
        return
    entry = match_entry(node)
    field_paths = [f["path"] for f in entry["inputFields"]] if entry else []
    # ACTION 동적 칸(fieldsSource) 안은 <칸 경로>.<키>까지 짚는다(spec 2026-10-26 §8). AI 노드 tools[].config는 그대로
    dynamic = {f["path"] for f in entry["inputFields"] if f.get("fieldsSource")} \
        if entry and entry["node_type"] == "ACTION" else set()
    unused = [p for p in field_paths if _unused_field(entry, node, p)]
    unused += [_CONDITION_ALIASES[p] for p in unused if p in _CONDITION_ALIASES]  # BE는 right가 없으면 rightValue를 읽는다
    anc = None
    for loc, text in _iter_strings(config, "config"):
        if "{{" not in text or any(loc == p or loc.startswith(p + ".") for p in unused):
            continue
        field = next((p for p in field_paths if loc == p or loc.startswith(p + ".")), None)
        if field in dynamic and loc != field:  # 키에는 `.`이 없다 — 저장 키는 `.`을 %2E로 바꾼다(D5)
            field = f"{field}.{loc[len(field) + 1:].split('.', 1)[0]}"
        if text.rfind("{{") > text.rfind("}}"):
            yield _err("REF_FORMAT", "참조식의 '{{'가 닫히지 않았어요.", nid, field)
        for ref in _REF_RE.finditer(text):  # findall은 참조 수백만 개를 목록으로 먼저 만든다
            raw = ref.group(1)
            m = REF_BODY_RE.fullmatch(raw)
            if not m:
                yield _err("REF_FORMAT", f"참조식 '{{{{{_clip(raw)}}}}}'의 형식이 맞지 않아요. "
                                         "'{{nodes.<노드ID>.output.<칸>}}' 모양이어야 해요.", nid, field)
                continue
            target, out_path = m.group(1), m.group(2)
            if target not in by_id:
                yield _err("REF_UNKNOWN_NODE", f"없는 노드 '{_clip(target)}'를 참조해요.", nid, field)
                continue
            if target == nid:
                yield _err("REF_SELF", "자기 자신의 결과는 참조할 수 없어요.", nid, field)
                continue
            if anc is None:
                anc = _ancestors(nid, preds)
            if target not in anc:
                yield _err("REF_NOT_UPSTREAM", f"앞 단계가 아닌 노드 '{_clip(target)}'를 참조해요.", nid, field)
                continue
            if target not in outputs:
                outputs[target] = _declared_outputs(by_id[target])
            declared = outputs[target]
            head = out_path.split(".", 1)[0]
            if declared is not None and head not in declared:
                yield _err("REF_UNKNOWN_FIELD", f"노드 '{_clip(target)}'에는 '{_clip(head)}' 결과가 없어요.", nid, field)

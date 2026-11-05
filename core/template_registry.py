"""노드 템플릿 레지스트리 (SSOT 로더)

카탈로그 서브모듈 `catalog/`(ieum-node-catalog)의 `events/<앱>/<이벤트>.json`·`presets.json`·`apps.json`을
단일 진실 원천으로 로드·검증한다. 스키마는 catalog/SCHEMA.md.
원본 JSON은 inputFields·fields로 필드를 정의하고, 로더가 공통 필드를 붙여 정규화한다(core.node_fields).

소비처: 노드 카탈로그 API·정적 검증(match_entry)·ACTION 실행·대화형 생성(core/builder — builder_entries·match_entry).
"""
import os
import re
import copy
import json
import glob

from api.schemas.generate_workflow import TRIGGER_TYPES

CATALOG_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "catalog"))
# 프리셋으로 만든 노드의 event. 프리셋은 이벤트가 아니다(spec §3) — 출처 프리셋은 저장하지 않는다.
PRESET_EVENT = "ai.agent"
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_KINDS = {"trigger", "action", "search"}
_SUBMODULE_HINT = "`git submodule update --init`으로 catalog/를 받아라"

_VALID_NODE_TYPES = {"TRIGGER", "AI", "HTTP", "CONDITION", "TRANSFORM", "APPROVAL", "ACTION"}

_REQUIRED_TOP_KEYS = {"id", "node_type", "tool_key", "fixed"}
# 옛 생성 경로 칸 — 읽는 곳이 없다. 파일에 쓰면 거부
_LEGACY_TOP_KEYS = {"slots", "allowed_config_fields", "tags", "menu", "golden_snippet", "generation"}

# 모듈 캐시 (파일은 기동 중 불변)
_cache: dict | None = None


class TemplateSchemaError(ValueError):
    """템플릿 스키마/드리프트 검증 실패"""
    pass


def _tool_map_keys() -> set:
    """_TOOL_MAP 키 집합. tools 패키지는 google.adk를 import하므로 lazy import한다."""
    from tools import _TOOL_MAP
    return set(_TOOL_MAP.keys())


def _validate_template(tpl: dict, where: str, tool_keys: set, apps: dict) -> None:
    """원본 템플릿 1개의 형식·드리프트를 검증한다(정규화 전). 형식은 SCHEMA.md."""
    from core.node_fields import AI_COMMON_KEYS, AI_COMMON_OUTPUTS, FieldError, validate_field
    from tools.registry import resolve_action_fn

    if not isinstance(tpl, dict):
        raise TemplateSchemaError(f"{where}: 템플릿 루트는 JSON 객체(dict)여야 합니다.")
    missing = _REQUIRED_TOP_KEYS - tpl.keys()
    if missing:
        raise TemplateSchemaError(f"{where}: 필수 필드 누락 {sorted(missing)}")
    tid = tpl["id"]
    legacy = _LEGACY_TOP_KEYS & tpl.keys()
    if legacy:
        raise TemplateSchemaError(
            f"{tid}: {sorted(legacy)}는 옛 생성 경로 칸이다 — 쓰지 않는다(SCHEMA.md).")
    if "builder" in tpl and not isinstance(tpl["builder"], bool):
        raise TemplateSchemaError(f"{tid}: builder는 true/false여야 합니다.")

    if tpl["node_type"] not in _VALID_NODE_TYPES:
        raise TemplateSchemaError(f"{tid}: node_type '{tpl['node_type']}' 유효하지 않음 {sorted(_VALID_NODE_TYPES)}")

    tool_key = tpl["tool_key"]
    # _TOOL_MAP 키 외에 builtin:github_*(ACTION 전용 동적 서비스 액션)도 실제 등록된 함수면 허용한다.
    if tool_key is not None and tool_key not in tool_keys and resolve_action_fn(tool_key) is None:
        raise TemplateSchemaError(f"{tid}: tool_key '{tool_key}'가 _TOOL_MAP에 없음(드리프트)")

    fixed = tpl["fixed"]
    if not isinstance(fixed, dict) or "type" not in fixed:
        raise TemplateSchemaError(f"{tid}: fixed에 type이 필요함")
    if fixed["type"] != tpl["node_type"]:
        raise TemplateSchemaError(f"{tid}: fixed.type({fixed['type']}) != node_type({tpl['node_type']})")
    fixed_config = fixed.get("config") or {}
    for tool in fixed_config.get("tools", []) or []:
        name = tool.get("name") if isinstance(tool, dict) else tool
        if name and name != "mcp" and name not in tool_keys and resolve_action_fn(name) is None:
            raise TemplateSchemaError(f"{tid}: fixed tools '{name}'가 _TOOL_MAP에 없음(드리프트)")
    if "serviceType" in fixed_config:
        raise TemplateSchemaError(f"{tid}: serviceType은 폐지됐다 — 쓰지 않는다(spec §4).")
    app = tpl.get("app")
    if app is not None and app not in apps:
        raise TemplateSchemaError(f"{tid}: 앱 '{app}'가 apps.json에 없다.")
    if not tpl.get("preset"):
        if tpl.get("kind") not in _KINDS:
            raise TemplateSchemaError(f"{tid}: kind '{tpl.get('kind')}'가 {sorted(_KINDS)}에 없다.")
        if (tpl["node_type"] == "TRIGGER") != (tpl["kind"] == "trigger"):
            raise TemplateSchemaError(f"{tid}: TRIGGER 노드와 kind 'trigger'는 함께 다닌다.")
        if tpl["node_type"] == "TRIGGER" and tpl.get("trigger_type") not in TRIGGER_TYPES:
            raise TemplateSchemaError(
                f"{tid}: trigger_type '{tpl.get('trigger_type')}'가 BE TriggerType에 없음 {list(TRIGGER_TYPES)}")

    inputs = tpl.get("inputFields", [])
    if not isinstance(inputs, list):
        raise TemplateSchemaError(f"{tid}: inputFields는 리스트여야 합니다.")
    seen = set()
    for field in inputs:
        partial = tpl["node_type"] == "AI" and isinstance(field, dict) and field.get("key") in AI_COMMON_KEYS
        try:
            validate_field(field, partial=partial)
        except FieldError as e:
            raise TemplateSchemaError(f"{tid}: inputFields {e}")
        if field["key"] in seen:  # 병합(merge_common)에서 뒤 것이 조용히 이기지 않게
            raise TemplateSchemaError(f"{tid}: inputFields key '{field['key']}' 중복")
        seen.add(field["key"])

    overrides = tpl.get("fields")
    if overrides is not None:
        if tool_key is None:
            raise TemplateSchemaError(f"{tid}: fields(도구 필드 덮어쓰기)는 tool_key가 있는 항목만 쓴다.")
        if not isinstance(overrides, dict):
            raise TemplateSchemaError(f"{tid}: fields는 {{파라미터: 덮어쓰기}} 객체여야 합니다.")
        for key, ov in overrides.items():
            if not isinstance(ov, dict) or {"key", "path"} & ov.keys():
                raise TemplateSchemaError(f"{tid}: fields.{key}는 key·path 없는 객체여야 합니다.")
            try:
                validate_field({"key": key, **ov}, partial=True)
            except FieldError as e:
                raise TemplateSchemaError(f"{tid}: fields.{key} {e}")

    outputs = tpl.get("outputFields", [])
    if not isinstance(outputs, list):
        raise TemplateSchemaError(f"{tid}: outputFields는 리스트여야 합니다.")
    # AI 항목은 로더가 공통 출력을 앞에 붙이므로 그 키도 이미 쓰인 것으로 본다
    seen = {f["key"] for f in AI_COMMON_OUTPUTS} if tpl["node_type"] == "AI" else set()
    for field in outputs:
        try:
            validate_field(field)
        except FieldError as e:
            raise TemplateSchemaError(f"{tid}: outputFields {e}")
        if field["key"] in seen:
            raise TemplateSchemaError(f"{tid}: outputFields key '{field['key']}' 중복")
        seen.add(field["key"])
    if "outputDynamic" in tpl and not isinstance(tpl["outputDynamic"], bool):
        raise TemplateSchemaError(f"{tid}: outputDynamic은 true/false여야 합니다.")
    if "outputsFrom" in tpl and not (isinstance(tpl["outputsFrom"], str) and tpl["outputsFrom"].startswith("config.")
                                     and len(tpl["outputsFrom"]) > len("config.")):
        raise TemplateSchemaError(f"{tid}: outputsFrom은 'config.<경로>' 형식이어야 합니다.")
    if tpl.get("builder", True):
        for key in ("title", "description"):
            if not (isinstance(tpl.get(key), str) and tpl[key].strip()):
                raise TemplateSchemaError(f"{tid}: builder 항목은 {key}(사용자 표시 문구)가 필요합니다.")
    if "match" in tpl and not (isinstance(tpl["match"], dict) and tpl["match"]
                               and all(isinstance(k, str) for k in tpl["match"])):
        raise TemplateSchemaError(f"{tid}: match는 {{경로: 값}} 객체여야 합니다.")


def _tool_fields(tool_key: str, overrides: dict) -> list:
    """도구 시그니처 → Field(주입 인자 제외) + 소유 항목의 덮어쓰기."""
    from tools.registry import RUNTIME_INJECTED_PARAMS, resolve_action_fn
    from core.node_fields import apply_overrides, signature_fields
    return apply_overrides(signature_fields(resolve_action_fn(tool_key), RUNTIME_INJECTED_PARAMS), overrides,
                           where=tool_key)


def _normalize(tpl: dict, tool_fields: dict, apps: dict) -> dict:
    """원본 항목 → 메모리상 항목. 공통 필드를 붙이고 칸 경로·출력·brand를 채운다."""
    from core.node_fields import AI_COMMON_FIELDS, AI_COMMON_OUTPUTS, merge_common
    from tools.registry import brand_for_entry

    t = copy.deepcopy(tpl)
    t.setdefault("builder", True)
    t["event"] = PRESET_EVENT if t.get("preset") else t["id"]
    t["fixed"]["event"] = t["event"]
    t["fixed"].setdefault("config", {})
    app = apps.get(t["app"]) if t.get("app") else None
    t["provider"] = app["provider"] if app else None
    t["app_title"] = app["title"] if app else None
    common = AI_COMMON_FIELDS if t["node_type"] == "AI" else []
    direct = [{**f, "path": f.get("path") or f"config.{f['key']}"}
              for f in merge_common(common, t.get("inputFields") or [])]
    tool = copy.deepcopy(tool_fields.get(t["tool_key"], [])) if t["tool_key"] else []
    if t["node_type"] == "ACTION":
        # ACTION 칸은 노드 config 바로 아래다(spec §4) — 직속 칸과 같은 취급.
        direct += [{**f, "path": f"config.{f['key']}"} for f in tool]
        tool = []
    else:
        tool = [{**f, "path": f"config.tools.0.config.{f['key']}"} for f in tool]
    t["inputFields"] = direct + tool
    t["outputFields"] = (copy.deepcopy(AI_COMMON_OUTPUTS) if t["node_type"] == "AI" else []) \
        + copy.deepcopy(t.get("outputFields") or [])
    # 카탈로그 응답이 fixed.config.brand로 내보낸다. fixed 자체엔 넣지 않는다 — fixed는 다른 소비처
    # (빌더 add_node)와 공유한다.
    t["brand"] = app["brand"] if app else brand_for_entry(t)
    return t


def _read_json(path: str):
    if not os.path.exists(path):
        raise TemplateSchemaError(f"{os.path.relpath(path, CATALOG_DIR)}가 없다 — {_SUBMODULE_HINT}.")
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            raise TemplateSchemaError(f"{os.path.relpath(path, CATALOG_DIR)}: JSON 파싱 실패 - {e}")


def _read_catalog() -> tuple[dict, list]:
    """카탈로그 → (apps, 원본 항목 목록). 이벤트 이름은 경로에서 계산해 id·app으로 넣고, 프리셋은
    node_type AI·빌더 비노출로 표시한다. 카탈로그가 비면 기동 실패다(spec §5.3) — 서브모듈 미초기화를
    빈 카탈로그로 조용히 넘기면 생성·검증이 엉뚱하게 돈다."""
    events_dir = os.path.join(CATALOG_DIR, "events")
    paths = sorted(glob.glob(os.path.join(events_dir, "*", "*.json")))
    if not paths:
        raise TemplateSchemaError(f"카탈로그 이벤트가 없다({events_dir}) — {_SUBMODULE_HINT}.")
    apps = _read_json(os.path.join(CATALOG_DIR, "apps.json"))
    if not isinstance(apps, dict):
        raise TemplateSchemaError("apps.json은 객체여야 한다.")
    entries = []
    for path in paths:
        app, event = os.path.basename(os.path.dirname(path)), os.path.basename(path)[:-len(".json")]
        name = f"{app}.{event}"
        if not (_NAME_RE.match(app) and _NAME_RE.match(event)):
            raise TemplateSchemaError(f"{name}: 경로는 events/<앱>/<이벤트>.json, 각 부분은 소문자·숫자·밑줄이다.")
        tpl = _read_json(path)
        if not isinstance(tpl, dict):
            raise TemplateSchemaError(f"{name}: 템플릿 루트는 JSON 객체(dict)여야 합니다.")
        if {"id", "app"} & tpl.keys():
            raise TemplateSchemaError(f"{name}: id·app은 경로에서 계산한다 — 파일에 쓰지 않는다.")
        entries.append({"id": name, "app": app, **tpl})
    presets = _read_json(os.path.join(CATALOG_DIR, "presets.json"))
    if not isinstance(presets, list):
        raise TemplateSchemaError("presets.json은 배열이어야 한다.")
    for p in presets:
        if not isinstance(p, dict) or {"node_type", "builder", "event", "kind"} & p.keys():
            raise TemplateSchemaError(f"프리셋 {p.get('id') if isinstance(p, dict) else p!r}: "
                                      "node_type·builder·event·kind는 로더가 정한다.")
        entries.append({**p, "node_type": "AI", "builder": False, "preset": True})
    # id 정렬 — builder_entries(카탈로그 API·빌더 목록)가 이 순서를 따른다.
    entries.sort(key=lambda e: e.get("id", ""))
    return apps, entries


def load_templates(force: bool = False, tool_keys: set | None = None) -> dict:
    """모든 항목을 로드·검증·정규화해 {id: 항목} dict로 반환한다(캐시).
    도구 필드 덮어쓰기(`fields`)는 도구당 한 항목만 소유한다 — 같은 tool_key의 다른 항목(생성 프리셋)은
    그 정의를 그대로 쓴다."""
    from core.node_fields import FieldError

    global _cache
    if _cache is not None and not force:
        return _cache
    if tool_keys is None:
        tool_keys = _tool_map_keys()

    apps, entries = _read_catalog()
    raw: dict = {}
    for tpl in entries:
        _validate_template(tpl, tpl.get("id", "?"), tool_keys, apps)
        if tpl["id"] in raw:
            raise TemplateSchemaError(f"중복된 템플릿 id: {tpl['id']}")
        raw[tpl["id"]] = tpl

    owners: dict = {}
    for tpl in raw.values():
        if "fields" in tpl:
            tk = tpl["tool_key"]
            if tk in owners:
                raise TemplateSchemaError(
                    f"도구 '{tk}'의 fields 덮어쓰기가 '{owners[tk]}'·'{tpl['id']}' 두 곳에 있다 — 한 항목에만 쓴다.")
            owners[tk] = tpl["id"]

    tool_fields: dict = {}
    for tk in {t["tool_key"] for t in raw.values() if t["tool_key"]}:
        overrides = raw[owners[tk]]["fields"] if tk in owners else {}
        try:
            tool_fields[tk] = _tool_fields(tk, overrides)
        except FieldError as e:
            raise TemplateSchemaError(f"{owners.get(tk, tk)}: {e}")

    templates = {tid: _normalize(tpl, tool_fields, apps) for tid, tpl in raw.items()}
    for t in templates.values():
        keys = [f["key"] for f in t["inputFields"]]
        dup = sorted({k for k in keys if keys.count(k) > 1})
        if dup:  # 직속 필드와 도구 필드가 같은 key — FE 폼은 key로 칸을 찾는다
            raise TemplateSchemaError(f"{t['id']}: 최종 inputFields(직속 + 도구 필드) key {dup} 중복")
        keys = set(keys)
        for f in t["inputFields"]:
            for attr in ("optionsInputs", "fieldsInputs"):
                bad = set(f.get(attr) or []) - keys
                if bad:
                    raise TemplateSchemaError(
                        f"{t['id']}: '{f['key']}'의 {attr} {sorted(bad)}가 같은 항목의 필드가 아니다.")
    seen: dict = {}
    for t in templates.values():
        if not t.get("preset") or "match" not in t:
            continue
        key = json.dumps(t["match"], sort_keys=True)
        if key in seen:
            raise TemplateSchemaError(f"프리셋 '{seen[key]}'·'{t['id']}'의 match가 같다.")
        seen[key] = t["id"]
    agent = templates.get(_AGENT_ENTRY_ID)
    if agent is not None:
        tools_field = next((f for f in agent["inputFields"] if f["key"] == "tools"), None)
        if tools_field is None:
            raise TemplateSchemaError(f"{_AGENT_ENTRY_ID}: tools 필드가 필요하다(도구 선택지를 싣는 칸).")
        # github 액션은 _TOOL_MAP 밖(동적 서브에이전트 서비스)이라 뺀다 — AI 노드 GitHub 계약은 tools: [].
        tools_field["choices"] = [{"id": t["tool_key"], "name": t["title"]}
                                  for t in sorted(templates.values(), key=lambda t: t["id"])
                                  if t["node_type"] == "ACTION" and t["builder"] and t["tool_key"] in tool_keys]

    _cache = templates
    return templates


def validate_registry(tool_keys: set | None = None) -> None:
    """레지스트리 전체를 강제 재검증한다(기동·CI 게이트용)."""
    load_templates(force=True, tool_keys=tool_keys)


def all_entries() -> list:
    """카탈로그 항목 전체(builder 전용 포함). 카탈로그 API는 builder_entries()."""
    return list(load_templates().values())


# FE 빌더의 "AI 에이전트" 항목. tools 필드의 선택지는 액션 항목 목록에서 로더가 채운다.
_AGENT_ENTRY_ID = "ai.agent"


def _matches(match: dict, node: dict) -> bool:
    """match의 모든 (경로, 값)이 맞으면 True. 값 None은 '경로가 없거나 null·빈 문자열·빈 목록'."""
    for path, expected in match.items():
        actual = _get_by_path(node, path)
        if expected is None:
            if actual not in (None, "", []):
                return False
        elif actual != expected:
            return False
    return True


def _by_specificity(entries) -> tuple:
    return tuple(sorted(entries, key=lambda t: (-len(t["match"]), t["id"])))


def builder_entries() -> list:
    """빌더 목록(카탈로그 API·액션 허용 목록·도구 선택지). 프리셋은 없다(설계 결정 4). id 순."""
    return [t for t in load_templates().values() if t["builder"]]


# (정렬 기준 dict, 결과). dict가 바뀌면 다시 정렬한다 — lru_cache는 인자가 없어 재로드 후에도 낡은 결과를 준다.
_preset_memo: tuple = (None, ())


def _preset_matchers() -> list:
    """AI 노드의 프리셋 칸 규칙 후보 = match를 쓴 프리셋. 조건 수 내림차순 → id(구체적인 것 먼저, spec §4 AI 노드)."""
    global _preset_memo
    templates = load_templates()
    if _preset_memo[0] is not templates:
        _preset_memo = (templates, _by_specificity(t for t in templates.values() if t.get("preset") and t.get("match")))
    return list(_preset_memo[1])


def event_entry(node: dict) -> dict | None:
    """노드 종류의 유일한 판정(spec §4) — event가 가리키는 카탈로그 이벤트 항목. event가 없거나, 문자열이 아니거나,
    모르는 이름이거나(BE 카탈로그 ⊇ agent 카탈로그), 프리셋 id면 None. 옛 필드는 보지 않는다."""
    event = node.get("event") if isinstance(node, dict) else None
    if not isinstance(event, str):
        return None
    entry = load_templates().get(event)
    return entry if entry is not None and not entry.get("preset") else None


def trigger_type_of(node: dict) -> str | None:
    """트리거 종류 = event 항목의 trigger_type. 모르는 event·트리거가 아닌 event면 None."""
    entry = event_entry(node)
    return entry.get("trigger_type") if entry is not None else None


def match_entry(node: dict) -> dict | None:
    """칸·출력 규칙을 고를 카탈로그 항목. event 항목이고, AI 항목이면 match가 성립하는 첫 프리셋이 대신한다
    (ai.mcp catalogId 필수·ai.github_query 도구 없음 허용 등 — spec §4 AI 노드). 모르는 event면 None.
    프리셋 match의 type 조건은 노드 표기가 아니라 event가 정한 AI로 본다 — 정적 검증 경로는 type을
    정규화하지 않은 저장 노드를 받는다(소문자·누락 type이 ai.agent로 떨어져 tools REQUIRED가 나거나 catalogId 검사가 빠지면 안 된다)."""
    entry = event_entry(node)
    if entry is None or entry["node_type"] != "AI":
        return entry
    view = {**node, "type": "AI"}
    return next((p for p in _preset_matchers() if _matches(p["match"], view)), entry)


def get_template(template_id: str) -> dict | None:
    return load_templates().get(template_id)


def _set_by_path(obj: dict, path: str, value) -> None:
    """dotted path 위치에 value를 설정한다(in-place). 숫자 세그먼트는 list 인덱스로 처리한다.
    예: "label" → obj["label"], "config.prompt" → obj["config"]["prompt"],
        "config.tools.0.config.catalogId" → obj["config"]["tools"][0]["config"]["catalogId"].
    중간 컨테이너가 없으면 다음 세그먼트 종류(숫자=list, 그 외=dict)에 맞춰 생성한다."""
    parts = path.split(".")
    cur = obj
    for i, raw in enumerate(parts[:-1]):
        nxt_is_idx = parts[i + 1].isdigit()
        if raw.isdigit():
            idx = int(raw)
            while len(cur) <= idx:
                cur.append({})
            if not isinstance(cur[idx], (dict, list)):
                cur[idx] = [] if nxt_is_idx else {}
            cur = cur[idx]
        else:
            if raw not in cur or not isinstance(cur[raw], (dict, list)):
                cur[raw] = [] if nxt_is_idx else {}
            cur = cur[raw]
    last = parts[-1]
    if last.isdigit():
        idx = int(last)
        while len(cur) <= idx:
            cur.append(None)
        cur[idx] = value
    else:
        cur[last] = value


def _get_by_path(obj, path: str):
    """dotted path(숫자=list 인덱스) 위치의 값을 읽는다. 없으면 None."""
    cur = obj
    for raw in path.split("."):
        if raw.isdigit():
            idx = int(raw)
            if not isinstance(cur, list) or idx >= len(cur):
                return None
            cur = cur[idx]
        else:
            if not isinstance(cur, dict) or raw not in cur:
                return None
            cur = cur[raw]
    return cur



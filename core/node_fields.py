"""노드 카탈로그 필드(Field) — 형식 검증과 파생 규칙.

템플릿 JSON(ieum-workflow-design/templates)과 도구 시그니처에서 입력 필드를 만들고, 그 필드에서
생성 슬롯·허용 config 키를 파생한다. 형식은 templates/SCHEMA.md와 spec(ieum-backend
docs/superpowers/specs/2026-10-05-node-catalog-design.md §2·§5). 템플릿·도구 모듈을 import하지
않는 순수 함수만 둔다 — 로더(core.template_registry)가 이 함수들을 조립한다."""
import copy
import inspect
import types
import typing


class FieldError(ValueError):
    """Field 형식 위반."""


VALID_FIELD_TYPES = {"string", "text", "integer", "number", "boolean", "datetime",
                     "cron", "json", "dict", "object"}
VALID_SLOT_KINDS = {"string", "enum", "provider", "model", "cron", "expr", "mapping", "http_method"}
_FIELD_ATTRS = {"key", "title", "type", "description", "required", "default", "choices",
                "optionsSource", "optionsInputs", "list", "children", "dynamic", "ref", "llm", "path"}
_LLM_ATTRS = {"hint", "slot", "name", "kind", "inject"}
_INJECT = {"provider", "model"}
_PY_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", dict: "object"}

# 모든 노드 공통(노드 최상위 label·description). 필드 목록이 아니라 슬롯으로만 존재한다.
UNIVERSAL_SLOTS = [
    {"name": "label", "path": "label", "required": True, "kind": "string", "description": "노드 라벨"},
    {"name": "description", "path": "description", "required": True, "kind": "string",
     "description": "이 노드가 무슨 일을 하는지 일반 사용자에게 알려주는 자연어 1문장. 도구 키·필드명·"
                    "변수 참조식 같은 기술 용어 없이, 사용자가 얻는 결과 중심으로 쓴다."},
]

# node_type AI 항목의 inputFields 앞에 로더가 붙인다. 순서 = 기존 슬롯 순서(llmProvider·model·prompt).
AI_COMMON_FIELDS = [
    {"key": "llmProvider", "title": "AI 제공자", "type": "string", "required": True, "ref": False,
     "choices": [{"id": "CLAUDE", "name": "Claude"}, {"id": "OPENAI", "name": "OpenAI"},
                 {"id": "GEMINI", "name": "Gemini"}],
     "llm": {"inject": "provider", "hint": "요청자 provider 계승"}},
    {"key": "model", "title": "모델", "type": "string", "required": True, "ref": False,
     "optionsSource": "ai.models", "optionsInputs": ["llmProvider"],
     "llm": {"inject": "model", "hint": "요청 provider의 기본 모델. 시스템이 자동 주입한다(작성 금지)."}},
    {"key": "prompt", "title": "지시문", "type": "text", "required": True},
    # 비우면 서버가 정한다(BE AgentNodeExecutor: 역할·베타 자격에 따라). 생성 경로는 fixed ""로 고정.
    {"key": "credentialId", "title": "API 키", "type": "string", "ref": False,
     "optionsSource": "ieum.credentials", "optionsInputs": ["llmProvider"], "llm": {"slot": False}},
    {"key": "systemMessage", "title": "시스템 메시지", "type": "text", "llm": {"slot": False}},
]
AI_COMMON_KEYS = {f["key"] for f in AI_COMMON_FIELDS}

# node_type AI 항목의 outputFields 앞에 로더가 붙인다(BE AgentNodeExecutor 출력 그대로).
AI_COMMON_OUTPUTS = [
    {"key": "output", "title": "결과", "type": "text"},
    {"key": "metadata", "title": "메타데이터", "type": "object", "dynamic": True},
]


def validate_field(field: dict, *, partial: bool = False) -> None:
    """Field 하나를 검증한다. partial이면 key 말고는 선택(공통 필드 병합·도구 덮어쓰기용)."""
    if not isinstance(field, dict):
        raise FieldError("필드는 객체여야 합니다.")
    key = field.get("key")
    if not isinstance(key, str) or not key:
        raise FieldError(f"필드에 key가 없습니다: {field}")
    unknown = set(field) - _FIELD_ATTRS
    if unknown:
        raise FieldError(f"'{key}': 알 수 없는 속성 {sorted(unknown)}")
    if not partial:
        for attr in ("title", "type"):
            if not field.get(attr):
                raise FieldError(f"'{key}': {attr}가 필요합니다.")
    for attr in ("required", "ref", "list", "dynamic"):
        if attr in field and not isinstance(field[attr], bool):
            raise FieldError(f"'{key}': {attr}는 true/false여야 합니다.")
    if "type" in field and field["type"] not in VALID_FIELD_TYPES:
        raise FieldError(f"'{key}': type '{field['type']}'는 {sorted(VALID_FIELD_TYPES)} 중 하나여야 합니다.")
    choices = field.get("choices")
    if choices is not None:
        if not isinstance(choices, list) or not all(
                isinstance(c, dict) and {"id", "name"} <= c.keys() for c in choices):
            raise FieldError(f"'{key}': choices는 {{id, name}} 목록이어야 합니다.")
        ids = [c["id"] for c in choices]
        if len(ids) != len(set(ids)):
            raise FieldError(f"'{key}': choices id 중복")
    if "llm" in field:  # null도 거부 — merge_common이 dict로 펼친다
        llm = field["llm"]
        if not isinstance(llm, dict) or set(llm) - _LLM_ATTRS:
            raise FieldError(f"'{key}': llm은 {sorted(_LLM_ATTRS)} 키만 갖는 객체여야 합니다.")
        if "slot" in llm and not isinstance(llm["slot"], bool):
            raise FieldError(f"'{key}': llm.slot은 true/false여야 합니다.")
        if "kind" in llm and llm["kind"] not in VALID_SLOT_KINDS:
            raise FieldError(f"'{key}': llm.kind '{llm['kind']}'가 유효하지 않습니다.")
        if "inject" in llm and llm["inject"] not in _INJECT:
            raise FieldError(f"'{key}': llm.inject는 provider|model이어야 합니다.")
    for child in field.get("children") or []:
        validate_field(child)


def _field_type(annotation) -> tuple[str, bool]:
    """파이썬 타입 표기 → (Field type, list 여부). Optional·Union은 None을 뺀 첫 타입."""
    if annotation is inspect.Parameter.empty:
        return "string", False
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        annotation = next(a for a in typing.get_args(annotation) if a is not type(None))
    origin = typing.get_origin(annotation) or annotation
    if origin is list:
        args = typing.get_args(annotation)
        return (_PY_TYPES.get(args[0], "string") if args else "string"), True
    return _PY_TYPES.get(origin, "string"), False


def signature_fields(fn, exclude: set[str]) -> list[dict]:
    """도구 함수 시그니처 → Field 목록(시그니처 순서). exclude(실행 시 주입 인자)는 뺀다.
    기본값이 None이면 default를 두지 않는다(키가 없으면 도구가 알아서 처리)."""
    fields = []
    for p in inspect.signature(fn).parameters.values():
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD) or p.name in exclude:
            continue
        ftype, is_list = _field_type(p.annotation)
        field = {"key": p.name, "title": p.name, "type": ftype}
        if is_list:
            field["list"] = True
        if p.default is inspect.Parameter.empty:
            field["required"] = True
        elif isinstance(p.default, (str, int, float, bool)):
            field["default"] = p.default
        fields.append(field)
    return fields


def apply_overrides(fields: list[dict], overrides: dict, where: str) -> list[dict]:
    """시그니처 필드에 항목 `fields` 덮어쓰기를 적용한다(얕은 병합, llm은 통째로 교체).
    시그니처 밖 키는 title·type을 포함한 전체 정의여야 하고 뒤에 붙는다 — 파라미터 이름이 바뀌어
    덮어쓰기가 떠돌면 여기서 기동이 실패한다."""
    sig_keys = {f["key"] for f in fields}
    out = [{**copy.deepcopy(f), **copy.deepcopy(overrides.get(f["key"], {}))} for f in fields]
    for key, ov in overrides.items():
        if key in sig_keys:
            continue
        extra = {"key": key, **copy.deepcopy(ov)}
        try:
            validate_field(extra)
        except FieldError as e:
            raise FieldError(f"{where}: 시그니처에 없는 '{key}'는 title·type을 포함한 전체 정의가 필요합니다 ({e})")
        out.append(extra)
    for f in out:
        validate_field(f)
    return out


def merge_common(common: list[dict], own: list[dict]) -> list[dict]:
    """공통 필드에 항목 inputFields를 병합한다. 같은 key는 공통 위치에서 덮어쓰고 새 key는 뒤에 붙인다.
    llm은 한 단계 더 병합한다 — hint만 줘도 공통 llm(inject 등)이 남는다."""
    own_by_key = {f["key"]: f for f in own}
    common_keys = {c["key"] for c in common}
    merged = []
    for c in common:
        o = own_by_key.get(c["key"], {})
        m = {**c, **o}
        if "llm" in o:
            m["llm"] = {**c.get("llm", {}), **o["llm"]}
        merged.append(m)
    merged += [f for f in own if f["key"] not in common_keys]
    return copy.deepcopy(merged)


def names_path(path: str) -> str:
    """표시 이름 캐시 경로: `<부모>._names.<key>`."""
    head, _, key = path.rpartition(".")
    return f"{head}._names.{key}"


def _slot_kind(field: dict) -> str:
    llm = field.get("llm") or {}
    if "kind" in llm:
        return llm["kind"]
    if "inject" in llm:
        return llm["inject"]
    if field.get("choices"):
        return "enum"
    return {"cron": "cron", "dict": "mapping"}.get(field["type"], "string")


def derive_slots(direct: list[dict], tool: list[dict]) -> list[dict]:
    """생성 슬롯 = 공통(label·description) + 직속 필드 + optionsSource가 있는 도구 필드.
    도구 필드 슬롯은 항상 선택(AI 노드에서는 비워도 AI가 결정). llm.slot=false는 제외,
    llm.name이 있으면 바로 뒤에 `<key>_name` 표시 이름 슬롯."""
    slots = copy.deepcopy(UNIVERSAL_SLOTS)
    candidates = [(f, False) for f in direct] + [(f, True) for f in tool if f.get("optionsSource")]
    for field, is_tool in candidates:
        llm = field.get("llm") or {}
        if llm.get("slot") is False:
            continue
        slot = {
            "name": field["key"],
            "path": field["path"],
            "required": False if is_tool else bool(field.get("required")),
            "kind": _slot_kind(field),
            "description": llm.get("hint") or field.get("description") or field["title"],
        }
        if field.get("choices"):
            slot["enum"] = [c["id"] for c in field["choices"]]
        slots.append(slot)
        if llm.get("name"):
            slots.append({"name": f"{field['key']}_name", "path": names_path(field["path"]),
                          "required": False, "kind": "string", "description": llm["name"]})
    return slots


def derive_allowed_config(fixed_config: dict, direct: list[dict]) -> list[str]:
    """허용 config 키 = fixed.config 키 ∪ 직속 필드 경로의 최상위 키 ∪ (직속 optionsSource 필드가 있으면 _names)."""
    allowed = set(fixed_config)
    for field in direct:
        parts = field["path"].split(".")
        if parts[0] != "config" or len(parts) < 2:
            continue
        allowed.add(parts[1])
        if len(parts) == 2 and field.get("optionsSource"):
            allowed.add("_names")
    return sorted(allowed)

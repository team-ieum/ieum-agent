"""AI 노드 구조화 출력 — 출력 칸(config.outputFields) 규칙·JSON Schema·응답 검증.

spec: ieum-backend docs/superpowers/specs/2026-10-11-ai-structured-output-design.md §2.2·§3.1·§3.2.
규칙 검사는 이 모듈 한 곳이다(BE는 config 값을 검사 없이 그대로 싣는다). 위반·불일치는 예외로
알리고, run_agent가 ErrorCode(INVALID_OUTPUT_FIELDS·AI_OUTPUT_MISMATCH) 결과로 바꾼다."""
import json
import math
import re

from core.output_validator import OutputValidator

MAX_FIELDS = 20
MAX_DESCRIPTION = 500
# BE 참조식 정규식(ExecutionCursor)이 읽을 수 있는 칸 이름. 점은 경로 구분자라 금지.
_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_ATTRS = {"key", "type", "description"}
# 출력 칸 type → JSON Schema type
_SCHEMA_TYPES = {"text": "string", "number": "number", "boolean": "boolean"}
_RESERVED_KEYS = {"success", "error"}
# BE SensitiveDataMasker가 이 이름이 든 키의 값을 ***로 저장한다 → 재처리가 ***를 하류로 흘린다.
_SENSITIVE_PARTS = ("apikey", "api_key", "token", "secret", "password", "authorization")
_PREVIEW_CHARS = 50
# Mongo(bson)가 담을 수 있는 정수 범위
_INT64_MIN, _INT64_MAX = -2**63, 2**63 - 1


class InvalidOutputFieldsError(ValueError):
    """outputFields가 규칙(§2.2)을 어겼다. 재시도해도 같으므로 BE는 재시도하지 않는다."""


class OutputMismatchError(ValueError):
    """모델 응답이 선언한 칸 모양과 다르다. BE는 SERVER_ERROR로 보고 재시도한다."""


def validate_output_fields(value, tools, agent_type: str | None = None) -> list[dict] | None:
    """정규화된 칸 목록을 돌려준다. 기본 칸(None·[])이면 None — 스키마 없이 지금처럼 텍스트를 받는다.
    agent_type이 None이면 simple로 본다(AgentNodeRequest·BE 기본값과 같다)."""
    if value is None or (isinstance(value, list) and not value):
        return None
    if not isinstance(value, list):
        raise InvalidOutputFieldsError("출력 칸(outputFields)은 목록이어야 합니다.")
    if len(value) > MAX_FIELDS:
        raise InvalidOutputFieldsError(f"출력 칸은 최대 {MAX_FIELDS}개입니다(받은 개수: {len(value)}).")
    if tools:
        raise InvalidOutputFieldsError("출력 칸은 도구 없는 AI 노드에만 쓸 수 있습니다.")
    if (agent_type or "simple") == "react":
        # 도구 없는 react 노드도 react 경로에서 MCP·조회 헬퍼를 붙인다 — simple로 돌리면 조회 없이
        # 스키마 모양의 꾸며낸 값을 성공으로 돌려준다(spec §2.2, 2026-10-12 결정).
        raise InvalidOutputFieldsError("출력 칸은 react(에이전트) 노드에는 쓸 수 없습니다. agentType을 simple로 두세요.")
    fields, seen = [], set()
    for i, f in enumerate(value, 1):
        if not isinstance(f, dict):
            raise InvalidOutputFieldsError(f"{i}번째 출력 칸은 객체여야 합니다.")
        unknown = set(f) - _ATTRS
        if unknown:
            raise InvalidOutputFieldsError(f"{i}번째 출력 칸에 알 수 없는 속성이 있습니다: {sorted(unknown)}")
        key = f.get("key")
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
            raise InvalidOutputFieldsError(
                f"출력 칸 이름 {key!r}는 영문·숫자·밑줄만 쓰고 영문이나 밑줄로 시작해야 합니다(64자 이하).")
        if key in seen:
            raise InvalidOutputFieldsError(f"출력 칸 이름 '{key}'가 중복됐습니다.")
        if key in _RESERVED_KEYS:
            raise InvalidOutputFieldsError(f"출력 칸 이름 '{key}'는 예약어라 쓸 수 없습니다.")
        if any(part in key.lower() for part in _SENSITIVE_PARTS):
            raise InvalidOutputFieldsError(f"출력 칸 이름 '{key}'는 민감정보 이름이라 저장 시 가려집니다. 다른 이름을 쓰세요.")
        ftype = f.get("type")
        if not isinstance(ftype, str) or ftype not in _SCHEMA_TYPES:
            raise InvalidOutputFieldsError(f"출력 칸 '{key}'의 타입 {ftype!r}는 text·number·boolean 중 하나여야 합니다.")
        desc = f.get("description")
        if desc is not None and (not isinstance(desc, str) or len(desc) > MAX_DESCRIPTION):
            raise InvalidOutputFieldsError(f"출력 칸 '{key}'의 설명은 {MAX_DESCRIPTION}자 이하 문자열이어야 합니다.")
        seen.add(key)
        fields.append({"key": key, "type": ftype, **({"description": desc} if desc else {})})
    return fields


def build_output_schema(fields: list[dict]) -> dict:
    """LlmAgent(output_schema=...)에 넘길 JSON Schema. additionalProperties는 넣지 않는다 —
    Gemini response_schema가 거부하고, LiteLlm은 strict 변환에서 스스로 붙인다."""
    return {
        "type": "object",
        "properties": {
            f["key"]: {"type": _SCHEMA_TYPES[f["type"]],
                       **({"description": f["description"]} if f.get("description") else {})}
            for f in fields
        },
        "required": [f["key"] for f in fields],
    }


def _preview(value) -> str:
    return OutputValidator.mask_response_content(json.dumps(value, ensure_ascii=False))[:_PREVIEW_CHARS]


def _type_ok(ftype: str, value) -> bool:
    if ftype == "text":
        return isinstance(value, str)
    if ftype == "boolean":
        return isinstance(value, bool)
    # number: bool은 int의 하위형이라 따로 뺀다. NaN·Infinity는 응답 JSON 직렬화가 거부한다
    # (int에 isfinite를 걸면 아주 큰 정수에서 OverflowError가 나므로 float에만 건다).
    # int는 int64 범위만 — 밖이면 멱등 complete()의 bson 인코딩이 OverflowError(PyMongoError 아님)로
    # 500을 내고, BE 재시도가 IN_FLIGHT 레코드에 막혀 DUPLICATE_REQUEST가 된다.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, int):
        return _INT64_MIN <= value <= _INT64_MAX
    return math.isfinite(value)


def parse_structured_output(text: str, fields: list[dict]) -> dict:
    """모델 원문 → 선언한 칸만 담은 객체. 코드펜스 제거 같은 관용 처리는 하지 않는다(강제 출력은 순수 JSON)."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        raise OutputMismatchError("AI 응답이 JSON이 아닙니다.")
    if not isinstance(data, dict):
        raise OutputMismatchError("AI 응답이 JSON 객체가 아닙니다.")
    out = {}
    for f in fields:
        key, ftype = f["key"], f["type"]
        value = data.get(key)
        if value is None:
            raise OutputMismatchError(f"출력 필드 '{key}' 누락")
        if not _type_ok(ftype, value):
            raise OutputMismatchError(f"출력 필드 '{key}'는 {ftype}여야 합니다(받은 값: {_preview(value)})")
        if (ftype == "number" and isinstance(value, float) and value.is_integer()
                and _INT64_MIN <= value <= _INT64_MAX):
            value = int(value)  # BE 치환이 "4.0"이 되면 CONDITION equals "4"가 깨진다. 1e20 같은 값은 float로 둔다(int64 밖)
        out[key] = value
    return out

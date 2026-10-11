"""AI 노드 구조화 출력 규칙·스키마·응답 검증(spec §2.2·§3.1·§3.2)."""
import json

import pytest

from core.output_fields import (
    InvalidOutputFieldsError, OutputMismatchError,
    build_output_schema, parse_structured_output, validate_output_fields,
)

FIELDS = [
    {"key": "start", "type": "text", "description": "회의 시작 시각"},
    {"key": "available_count", "type": "number"},
    {"key": "all_available", "type": "boolean"},
]


# --- validate_output_fields ---------------------------------------------------

@pytest.mark.parametrize("value", [None, []])
def test_미선언은_기본_칸이라_None(value):
    assert validate_output_fields(value, tools=[]) is None


def test_도구가_있어도_미선언이면_통과():
    assert validate_output_fields(None, tools=[{"name": "slack"}]) is None


def test_정상_칸은_정규화된_목록을_돌려준다():
    got = validate_output_fields(FIELDS + [{"key": "note", "type": "text", "description": ""}], tools=None)
    assert got == [
        {"key": "start", "type": "text", "description": "회의 시작 시각"},
        {"key": "available_count", "type": "number"},
        {"key": "all_available", "type": "boolean"},
        {"key": "note", "type": "text"},  # 빈 설명은 스키마에 싣지 않는다
    ]


def test_description_null은_없는_것과_같다():
    assert validate_output_fields([{"key": "a", "type": "text", "description": None}], []) == \
        [{"key": "a", "type": "text"}]


def test_64자_키는_통과():
    assert validate_output_fields([{"key": "a" * 64, "type": "text"}], [])


@pytest.mark.parametrize("value", [{}, "", "x", 1, {"key": "a", "type": "text"}])
def test_목록이_아니면_위반(value):
    with pytest.raises(InvalidOutputFieldsError):
        validate_output_fields(value, [])


@pytest.mark.parametrize("key", [
    "my-field", "a.b", "긴급", "1st", "", " a", "a\n", "a" * 65, None, 3,
])
def test_키_정규식_위반(key):
    with pytest.raises(InvalidOutputFieldsError):
        validate_output_fields([{"key": key, "type": "text"}], [])


@pytest.mark.parametrize("fields", [
    [{"key": "a", "type": "text"}, {"key": "a", "type": "number"}],          # 중복
    [{"key": f"k{i}", "type": "text"} for i in range(21)],                   # 21개
    [{"key": "success", "type": "boolean"}],                                 # 예약어
    [{"key": "error", "type": "text"}],
    [{"key": "tokenCount", "type": "number"}],                               # 마스킹 이름
    [{"key": "my_API_KEY", "type": "text"}],
    [{"key": "Authorization", "type": "text"}],
    [{"key": "a", "type": "string"}],                                        # 허용 밖 type
    [{"key": "a", "type": "list"}],
    [{"key": "a"}],
    [{"key": "a", "type": ["text"]}],                                      # unhashable type
    [{"key": "a", "type": {}}],
    [{"key": "a", "type": "text", "required": True}],                        # 알 수 없는 속성
    [{"key": "a", "type": "text", "description": "x" * 501}],                # 설명 501자
    [{"key": "a", "type": "text", "description": 3}],
    ["a"],                                                                   # 원소가 객체 아님
])
def test_규칙_위반(fields):
    with pytest.raises(InvalidOutputFieldsError):
        validate_output_fields(fields, [])


def test_설명_500자는_통과():
    assert validate_output_fields([{"key": "a", "type": "text", "description": "x" * 500}], [])


def test_20개는_통과():
    assert len(validate_output_fields([{"key": f"k{i}", "type": "text"} for i in range(20)], [])) == 20


def test_칸이_선언됐는데_도구가_있으면_위반():
    with pytest.raises(InvalidOutputFieldsError, match="도구"):
        validate_output_fields(FIELDS, [{"name": "builtin:web_search"}])


def test_칸이_선언됐는데_react면_위반_agentType_없으면_simple():
    with pytest.raises(InvalidOutputFieldsError, match="react"):
        validate_output_fields(FIELDS, [], agent_type="react")
    assert validate_output_fields(FIELDS, [], agent_type=None)
    assert validate_output_fields(FIELDS, [], agent_type="simple")
    assert validate_output_fields(None, [], agent_type="react") is None  # 칸 없는 react는 그대로


# --- build_output_schema --------------------------------------------------------

def test_스키마_타입_매핑_required_전부_additionalProperties_없음():
    schema = build_output_schema(validate_output_fields(FIELDS, []))
    assert schema == {
        "type": "object",
        "properties": {
            "start": {"type": "string", "description": "회의 시작 시각"},
            "available_count": {"type": "number"},
            "all_available": {"type": "boolean"},
        },
        "required": ["start", "available_count", "all_available"],
    }
    assert "additionalProperties" not in json.dumps(schema)


# --- parse_structured_output ------------------------------------------------------

def test_정상_응답():
    text = '{"start": "2026-10-01T14:00:00", "available_count": 4, "all_available": true}'
    assert parse_structured_output(text, FIELDS) == {
        "start": "2026-10-01T14:00:00", "available_count": 4, "all_available": True}


def test_소수부_0인_number는_int로():
    got = parse_structured_output('{"start": "s", "available_count": 4.0, "all_available": false}', FIELDS)
    assert got["available_count"] == 4 and type(got["available_count"]) is int


def test_소수는_그대로():
    got = parse_structured_output('{"start": "s", "available_count": 4.5, "all_available": false}', FIELDS)
    assert got["available_count"] == 4.5


def test_추가_칸은_버린다():
    got = parse_structured_output('{"start": "s", "available_count": 1, "all_available": true, "extra": 1}', FIELDS)
    assert "extra" not in got


def test_빈_문자열_text는_통과():
    assert parse_structured_output('{"a": ""}', [{"key": "a", "type": "text"}]) == {"a": ""}


@pytest.mark.parametrize("text, reason", [
    ("", "JSON이 아닙니다"),
    ("```json\n{\"a\": \"x\"}\n```", "JSON이 아닙니다"),                      # 코드펜스 관용 처리 없음
    ('{"start": "s", ', "JSON이 아닙니다"),
    ('[{"start": "s"}]', "객체가 아닙니다"),
    ('"s"', "객체가 아닙니다"),
    ('{"available_count": 1, "all_available": true}', "'start' 누락"),
    ('{"start": null, "available_count": 1, "all_available": true}', "'start' 누락"),
    ('{"start": "s", "available_count": 1, "all_available": "true"}', "'all_available'는 boolean"),
    ('{"start": "s", "available_count": "4", "all_available": true}', "'available_count'는 number"),
    ('{"start": "s", "available_count": true, "all_available": true}', "'available_count'는 number"),
    ('{"start": 1, "available_count": 1, "all_available": true}', "'start'는 text"),
    ('{"start": "s", "available_count": NaN, "all_available": true}', "'available_count'는 number"),
    ('{"start": "s", "available_count": Infinity, "all_available": true}', "'available_count'는 number"),
])
def test_불일치(text, reason):
    with pytest.raises(OutputMismatchError, match=reason):
        parse_structured_output(text, FIELDS)


def test_불일치_사유의_받은_값은_50자로_자르고_토큰을_가린다():
    value = "ghp_" + "a" * 100
    with pytest.raises(OutputMismatchError) as e:
        parse_structured_output(json.dumps({"n": value}), [{"key": "n", "type": "number"}])
    msg = str(e.value)
    assert "ghp_" not in msg and "[MASKED_GITHUB_TOKEN]" in msg
    assert "a" * 50 not in msg


def test_int64_범위_밖_정수는_불일치():
    # 범위 밖 int를 통과시키면 멱등 complete()의 bson 인코딩이 OverflowError → 500 → BE 재시도 DUPLICATE_REQUEST
    n = [{"key": "n", "type": "number"}]
    for big in (2 ** 63, -(2 ** 63) - 1, 10 ** 400):
        with pytest.raises(OutputMismatchError, match="'n'는 number"):
            parse_structured_output(json.dumps({"n": big}), n)
    assert parse_structured_output(json.dumps({"n": 2 ** 63 - 1}), n) == {"n": 2 ** 63 - 1}
    got = parse_structured_output('{"n": 1e20}', n)  # 정수값 float라도 int64 밖이면 int로 바꾸지 않는다
    assert got == {"n": 1e20} and isinstance(got["n"], float)

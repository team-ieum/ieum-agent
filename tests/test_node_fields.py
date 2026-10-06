import pytest

from core.node_fields import (
    AI_COMMON_FIELDS, AI_COMMON_KEYS, FieldError, UNIVERSAL_SLOTS, apply_overrides,
    derive_allowed_config, derive_slots, merge_common, names_path, signature_fields, validate_field,
)


# --- validate_field ---------------------------------------------------------------

def test_validate_field_accepts_full_definition():
    validate_field({"key": "cron", "title": "실행 주기", "type": "cron", "required": True, "ref": False,
                    "choices": [{"id": "a", "name": "A"}], "llm": {"hint": "h", "kind": "cron"}})


@pytest.mark.parametrize("bad, msg", [
    ({"title": "t", "type": "string"}, "key"),
    ({"key": "k", "type": "string"}, "title"),
    ({"key": "k", "title": "t"}, "type"),
    ({"key": "k", "title": "t", "type": "enum"}, "type"),
    ({"key": "k", "title": "t", "type": "string", "placeholder": "x"}, "placeholder"),
    ({"key": "k", "title": "t", "type": "string", "choices": [{"id": "a", "name": "A"}, {"id": "a", "name": "B"}]}, "중복"),
    ({"key": "k", "title": "t", "type": "string", "choices": [{"id": "a"}]}, "choices"),
    ({"key": "k", "title": "t", "type": "string", "llm": {"desc": "x"}}, "llm"),
    ({"key": "k", "title": "t", "type": "string", "llm": {"kind": "bogus"}}, "kind"),
    ({"key": "k", "title": "t", "type": "string", "llm": {"inject": "token"}}, "inject"),
    ({"key": "k", "title": "t", "type": "object", "children": [{"key": "c"}]}, "title"),
])
def test_validate_field_rejects(bad, msg):
    with pytest.raises(FieldError, match=msg):
        validate_field(bad)


def test_validate_field_partial_allows_missing_title_and_type():
    validate_field({"key": "prompt", "llm": {"hint": "h"}}, partial=True)


# --- signature_fields ----------------------------------------------------------------

def _sample(token: str, a: str, b: int = 5, c: str | None = None, d: list[str] = None,
            e: str = "", f: bool = False, *args, **kwargs):
    pass


def test_signature_fields_derives_type_required_default_and_skips_injected():
    assert signature_fields(_sample, exclude={"token"}) == [
        {"key": "a", "title": "a", "type": "string", "required": True},
        {"key": "b", "title": "b", "type": "integer", "default": 5},
        {"key": "c", "title": "c", "type": "string"},
        {"key": "d", "title": "d", "type": "string", "list": True},
        {"key": "e", "title": "e", "type": "string", "default": ""},
        {"key": "f", "title": "f", "type": "boolean", "default": False},
    ]


# --- apply_overrides -------------------------------------------------------------------

def test_apply_overrides_merges_in_place_and_appends_extra():
    base = [{"key": "a", "title": "a", "type": "string", "required": True}]
    out = apply_overrides(base, {
        "a": {"title": "가", "optionsSource": "x.y"},
        "webhookCredentialId": {"title": "웹훅", "type": "string", "optionsSource": "slack.webhooks"},
    }, where="slack")
    assert out == [
        {"key": "a", "title": "가", "type": "string", "required": True, "optionsSource": "x.y"},
        {"key": "webhookCredentialId", "title": "웹훅", "type": "string", "optionsSource": "slack.webhooks"},
    ]
    assert base == [{"key": "a", "title": "a", "type": "string", "required": True}]  # 원본 불변


def test_apply_overrides_extra_key_needs_full_definition():
    """시그니처에서 이름이 바뀐 파라미터의 덮어쓰기는 '시그니처 밖 키'가 된다 — 기동 시 실패해야 한다."""
    with pytest.raises(FieldError, match="old_name"):
        apply_overrides([{"key": "new_name", "title": "n", "type": "string"}],
                        {"old_name": {"title": "옛 이름"}}, where="builtin:x")


# --- merge_common --------------------------------------------------------------------

def test_merge_common_overrides_in_place_and_appends_new():
    common = [{"key": "p", "title": "P", "type": "text"}, {"key": "q", "title": "Q", "type": "text"}]
    out = merge_common(common, [{"key": "q", "llm": {"hint": "h"}},
                                {"key": "z", "title": "Z", "type": "string"}])
    assert [f["key"] for f in out] == ["p", "q", "z"]
    assert out[1] == {"key": "q", "title": "Q", "type": "text", "llm": {"hint": "h"}}
    assert "llm" not in common[1]  # 공통 상수 불변


def test_ai_common_order_matches_legacy_slot_order():
    assert [f["key"] for f in AI_COMMON_FIELDS] == [
        "llmProvider", "model", "prompt", "credentialId", "systemMessage"]
    assert AI_COMMON_KEYS == {"llmProvider", "model", "prompt", "credentialId", "systemMessage"}
    for f in AI_COMMON_FIELDS:
        validate_field(f)


# --- names_path / derive_slots / derive_allowed_config ----------------------------------

def test_names_path():
    assert names_path("config.tools.0.config.spreadsheet_id") == "config.tools.0.config._names.spreadsheet_id"
    assert names_path("config.credentialId") == "config._names.credentialId"


def _with_path(fields, prefix):
    return [{**f, "path": f.get("path") or f"{prefix}{f['key']}"} for f in fields]


def test_derive_slots_rules():
    direct = _with_path([
        {"key": "llmProvider", "title": "p", "type": "string", "required": True, "llm": {"inject": "provider"}},
        {"key": "method", "title": "m", "type": "string", "required": True,
         "choices": [{"id": "GET", "name": "GET"}], "llm": {"kind": "http_method"}},
        {"key": "op", "title": "o", "type": "string", "choices": [{"id": "eq", "name": "같음"}]},
        {"key": "cron", "title": "c", "type": "cron", "required": True},
        {"key": "mappings", "title": "m", "type": "dict"},
        {"key": "systemMessage", "title": "s", "type": "text", "llm": {"slot": False}},
    ], "config.")
    tool = _with_path([
        {"key": "spreadsheet_id", "title": "s", "type": "string", "required": True,
         "optionsSource": "google.spreadsheets", "llm": {"hint": "ID만", "name": "표시 이름"}},
        {"key": "cell_range", "title": "r", "type": "string", "required": True},
    ], "config.tools.0.config.")
    slots = derive_slots(direct, tool)
    assert slots[:2] == UNIVERSAL_SLOTS
    rows = [(s["name"], s["path"], s["required"], s["kind"]) for s in slots[2:]]
    assert rows == [
        ("llmProvider", "config.llmProvider", True, "provider"),
        ("method", "config.method", True, "http_method"),
        ("op", "config.op", False, "enum"),
        ("cron", "config.cron", True, "cron"),
        ("mappings", "config.mappings", False, "mapping"),
        ("spreadsheet_id", "config.tools.0.config.spreadsheet_id", False, "string"),
        ("spreadsheet_id_name", "config.tools.0.config._names.spreadsheet_id", False, "string"),
    ]
    by = {s["name"]: s for s in slots}
    assert by["op"]["enum"] == ["eq"]
    assert by["spreadsheet_id"]["description"] == "ID만"
    assert by["spreadsheet_id_name"]["description"] == "표시 이름"


def test_derive_allowed_config():
    direct = _with_path([
        {"key": "prompt", "title": "p", "type": "text"},
        {"key": "model", "title": "m", "type": "string", "optionsSource": "ai.models"},
        {"key": "catalogId", "title": "c", "type": "string", "optionsSource": "ieum.mcp_servers",
         "path": "config.tools.0.config.catalogId"},
    ], "config.")
    assert derive_allowed_config({"agentType": "react", "tools": []}, direct) == [
        "_names", "agentType", "model", "prompt", "tools"]
    assert derive_allowed_config({}, _with_path([{"key": "url", "title": "u", "type": "string"}], "config.")) == ["url"]

import os
import json
import pathlib

import pytest

from core import template_registry as tr
from core.template_registry import TemplateSchemaError


def _keys(tid):
    return [f["key"] for f in tr.get_template(tid)["outputFields"]]


@pytest.mark.parametrize("tid, keys", [
    ("trigger.schedule", ["triggeredAt", "cron"]),
    ("trigger.manual", []),
    ("trigger.webhook", []),
    ("trigger.gmail_new_email", ["messageId", "threadId", "from", "to", "subject", "snippet",
                                 "bodyText", "receivedAt", "labels"]),
    ("trigger.github_new_issue", ["issueNumber", "title", "body", "url", "author", "labels",
                                  "repo", "createdAt"]),
    ("http", ["statusCode", "body"]),
    ("condition", ["result"]),
    ("transform", []),
    ("approval", ["approved", "approvedBy", "approvedAt"]),
    ("ai.reasoning", ["output", "metadata"]),
    ("ai.mcp", ["output", "metadata"]),
    ("ai.google_sheets_write", ["output", "metadata"]),
])
def test_declared_outputs(tid, keys):
    assert _keys(tid) == keys


def test_dynamic_outputs():
    assert tr.get_template("trigger.manual")["outputDynamic"] is True
    assert tr.get_template("trigger.webhook")["outputDynamic"] is True
    assert tr.get_template("transform")["outputsFrom"] == "config.mappings"
    body = next(f for f in tr.get_template("http")["outputFields"] if f["key"] == "body")
    assert body["dynamic"] is True
    meta = next(f for f in tr.get_template("ai.reasoning")["outputFields"] if f["key"] == "metadata")
    assert meta["dynamic"] is True


def test_output_field_must_be_full_definition():
    path = os.path.join(tr.TEMPLATES_DIR, "condition.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    raw["outputFields"] = [{"key": "result"}]
    with pytest.raises(TemplateSchemaError, match="outputFields"):
        tr._validate_template(raw, path, tr._tool_map_keys())


@pytest.mark.parametrize("value", ["mappings", "config."])
def test_outputs_from_must_be_config_path(value):
    path = os.path.join(tr.TEMPLATES_DIR, "transform.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    raw["outputsFrom"] = value
    with pytest.raises(TemplateSchemaError, match="outputsFrom"):
        tr._validate_template(raw, path, tr._tool_map_keys())


def test_every_action_has_output_match_assertion():
    """액션이 늘었는데 성공 테스트에 출력 일치 단언을 빼먹으면 실패한다."""
    import glob
    here = os.path.dirname(__file__)
    src = "".join(pathlib.Path(p).read_text(encoding="utf-8") for p in glob.glob(os.path.join(here, "test_*.py")))
    for t in tr.all_entries():
        if t["node_type"] == "ACTION":
            assert f'assert_matches_outputs("{t["id"]}"' in src, t["id"]


@pytest.mark.parametrize("tid, dup", [
    ("condition", {"key": "result", "title": "두 번째 결과", "type": "boolean"}),
    ("ai.reasoning", {"key": "output", "title": "결과", "type": "text"}),  # 로더가 공통 출력을 앞에 붙인다
])
def test_duplicate_output_field_key_rejected(tid, dup):
    path = os.path.join(tr.TEMPLATES_DIR, f"{tid}.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    raw.setdefault("outputFields", []).append(dup)
    with pytest.raises(TemplateSchemaError, match="중복"):
        tr._validate_template(raw, path, tr._tool_map_keys())


def test_builder_entries_have_user_copy():
    for t in tr.all_entries():
        if t["builder"]:
            assert t.get("title", "").strip() and t.get("description", "").strip(), t["id"]


def test_builder_tool_fields_have_korean_titles():
    """빌더 폼에 파라미터 이름(예: start_datetime)이 그대로 보이면 안 된다."""
    for t in tr.all_entries():
        if not t["builder"]:
            continue
        for f in t["inputFields"]:
            assert f["title"] != f["key"], (t["id"], f["key"])


def test_builder_title_required_by_schema():
    path = os.path.join(tr.TEMPLATES_DIR, "http.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    raw.pop("title")
    with pytest.raises(TemplateSchemaError, match="title"):
        tr._validate_template(raw, path, tr._tool_map_keys())

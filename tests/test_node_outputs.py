import os
import json

import pytest

from core import template_registry as tr
from core.template_registry import TemplateSchemaError


def _keys(tid):
    return [f["key"] for f in tr.get_template(tid)["outputFields"]]


@pytest.mark.parametrize("tid, keys", [
    ("trigger.schedule", ["triggeredAt", "cron"]),
    ("trigger.manual", []),
    ("trigger.webhook", []),
    ("http", ["statusCode", "body"]),
    ("condition", ["result"]),
    ("transform", []),
    ("approval", ["approvedBy", "approvedAt"]),
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


def test_outputs_from_must_be_config_path():
    path = os.path.join(tr.TEMPLATES_DIR, "transform.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    raw["outputsFrom"] = "mappings"
    with pytest.raises(TemplateSchemaError, match="outputsFrom"):
        tr._validate_template(raw, path, tr._tool_map_keys())

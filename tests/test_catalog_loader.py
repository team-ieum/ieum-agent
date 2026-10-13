import json
import os

import pytest

from core import template_registry as tr
from tests.catalog_paths import copy_catalog


def test_event_name_comes_from_path_and_presets_are_ai_agent():
    t = tr.load_templates(force=True)
    gmail = t["gmail.new_email"]
    assert gmail["event"] == "gmail.new_email" == gmail["fixed"]["event"]
    assert (gmail["app"], gmail["provider"], gmail["kind"], gmail["trigger_type"]) == \
           ("gmail", "google", "trigger", "GMAIL_NEW_EMAIL")
    preset = t["ai.notion_create_page"]
    assert preset["preset"] is True and preset["event"] == "ai.agent" == preset["fixed"]["event"]
    assert preset["builder"] is False and preset["node_type"] == "AI"


def test_legacy_derived_values_still_injected():
    t = tr.load_templates(force=True)
    assert t["schedule.every"]["fixed"]["config"]["triggerType"] == "SCHEDULE"
    assert t["gmail.new_email"]["fixed"]["config"]["serviceType"] == "GOOGLE"
    assert t["ai.notion_create_page"]["fixed"]["config"]["serviceType"] == "NOTION"
    assert "serviceType" not in t["manual.run"]["fixed"]["config"]


def test_counts_and_builder_has_no_presets():
    t = tr.load_templates(force=True)
    assert sum(1 for x in t.values() if not x.get("preset")) == 30
    assert sum(1 for x in t.values() if x.get("preset")) == 21
    assert not [x["id"] for x in tr.builder_entries() if x.get("preset")]
    assert list(t) == sorted(t)        # 생성 경로의 동점 처리(select_by_tags)가 순서에 기댄다


def test_presets_with_match_stay_matchable():
    # 결정 4는 빌더 목록만 바꾼다 — 정적 검증 칸 규칙(예: ai.mcp catalogId 필수)은 그대로다.
    mcp = {"type": "AI", "config": {"agentType": "react", "tools": [{"name": "mcp", "config": {}}]}}
    assert tr.match_entry(mcp)["id"] == "ai.mcp"
    github = {"type": "AI", "config": {"agentType": "react", "tools": []}}
    assert tr.match_entry(github)["id"] == "ai.github_query"
    matchable_presets = sorted(x["id"] for x in tr.load_templates().values() if x.get("preset") and x["matchable"])
    assert matchable_presets == ["ai.github_query", "ai.http_fetch", "ai.mcp", "ai.web_search"]


def test_brand_from_apps_json():
    t = tr.load_templates(force=True)
    assert t["google_sheets.read"]["brand"] == "sheets"
    assert t["google_calendar.list"]["brand"] == "google"
    assert t["branch.if"]["brand"] == "filter"
    assert t["ai.web_search"]["brand"] == "openai"          # 앱 없는 프리셋은 기존 brand_for_entry


def test_webhook_catch_has_include_raw_field():
    field = next(f for f in tr.load_templates(force=True)["webhook.catch"]["inputFields"] if f["key"] == "includeRaw")
    assert field["type"] == "boolean" and field.get("default") is False


def test_trigger_type_outside_be_enum_rejected(tmp_path, monkeypatch):
    root = copy_catalog(tmp_path, monkeypatch)
    p = root / "events" / "gmail" / "new_email.json"
    data = json.loads(p.read_text())
    data["trigger_type"] = "SLACK_NEW_MESSAGE"
    p.write_text(json.dumps(data))
    with pytest.raises(tr.TemplateSchemaError, match="SLACK_NEW_MESSAGE"):
        tr.load_templates(force=True)


def test_event_file_must_not_carry_id(tmp_path, monkeypatch):
    root = copy_catalog(tmp_path, monkeypatch)
    p = root / "events" / "manual" / "run.json"
    p.write_text(json.dumps({**json.loads(p.read_text()), "id": "trigger.manual"}))
    with pytest.raises(tr.TemplateSchemaError, match="경로"):
        tr.load_templates(force=True)


def test_aliases_must_be_str_map(tmp_path, monkeypatch):
    root = copy_catalog(tmp_path, monkeypatch)
    (root / "aliases.json").write_text("[]")
    with pytest.raises(tr.TemplateSchemaError, match="aliases.json"):
        tr.load_templates(force=True)


def test_missing_apps_json_fails_with_submodule_hint(tmp_path, monkeypatch):
    root = copy_catalog(tmp_path, monkeypatch)
    (root / "apps.json").unlink()
    with pytest.raises(tr.TemplateSchemaError) as e:
        tr.load_templates(force=True)
    assert "apps.json" in str(e.value) and "submodule update --init" in str(e.value)

import copy
import glob
import json
import os
import shutil

import pytest

from core import template_registry as tr
from core.template_registry import SlotFillError, TemplateSchemaError
from core.validators.workflow_validator import WorkflowValidator

APP_PRESETS = [
    "ai.slack_send", "ai.discord_send", "ai.gmail_send",
    "ai.notion_create_page", "ai.notion_read_page", "ai.notion_search", "ai.notion_update_page",
    "ai.notion_append_block", "ai.notion_query_database",
    "ai.google_sheets_read", "ai.google_sheets_write", "ai.google_sheets_append",
    "ai.google_calendar_create", "ai.google_calendar_list", "ai.google_calendar_update",
    "ai.google_drive_read", "ai.google_drive_upload",
]


def _raw(tid: str) -> dict:
    with open(os.path.join(tr.TEMPLATES_DIR, f"{tid}.json"), encoding="utf-8") as f:
        return json.load(f)


# --- 파일 형식 ---------------------------------------------------------------------

def test_no_template_file_keeps_legacy_keys():
    for path in glob.glob(os.path.join(tr.TEMPLATES_DIR, "*.json")):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        assert not ({"slots", "allowed_config_fields"} & raw.keys()), path
        assert "serviceType" not in (raw["fixed"].get("config") or {}), path


def test_legacy_keys_rejected():
    bad = {**_raw("trigger.manual"), "slots": []}
    with pytest.raises(TemplateSchemaError, match="slots"):
        tr._validate_template(bad, os.path.join(tr.TEMPLATES_DIR, "trigger.manual.json"), tr._tool_map_keys())


def test_service_type_in_fixed_rejected():
    raw = _raw("ai.gmail_send")
    raw["fixed"]["config"]["serviceType"] = "GOOGLE"
    with pytest.raises(TemplateSchemaError, match="app"):
        tr._validate_template(raw, os.path.join(tr.TEMPLATES_DIR, "ai.gmail_send.json"), tr._tool_map_keys())


def test_fields_require_tool_key():
    raw = {**_raw("http"), "fields": {"url": {"title": "x"}}}
    with pytest.raises(TemplateSchemaError, match="tool_key"):
        tr._validate_template(raw, os.path.join(tr.TEMPLATES_DIR, "http.json"), tr._tool_map_keys())


def _load_from(tmp_path, monkeypatch, mutate):
    """실제 템플릿을 복사해 한 파일을 바꾼 뒤 강제 로드한다(캐시는 monkeypatch가 복원)."""
    for p in glob.glob(os.path.join(tr.TEMPLATES_DIR, "*.json")):
        shutil.copy(p, tmp_path)
    mutate(tmp_path)
    monkeypatch.setattr(tr, "TEMPLATES_DIR", str(tmp_path))
    monkeypatch.setattr(tr, "_cache", None)
    return tr.load_templates(force=True)


def test_duplicate_tool_field_owner_rejected(tmp_path, monkeypatch):
    def mutate(d):
        p = d / "ai.google_sheets_read.json"
        raw = json.loads(p.read_text(encoding="utf-8"))
        raw["fields"] = {"cell_range": {"title": "중복"}}
        p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(TemplateSchemaError, match="한 항목에만"):
        _load_from(tmp_path, monkeypatch, mutate)


def test_options_inputs_must_point_to_same_entry(tmp_path, monkeypatch):
    def mutate(d):
        p = d / "action.google_sheets_read.json"
        raw = json.loads(p.read_text(encoding="utf-8"))
        raw["fields"]["sheet_name"]["optionsInputs"] = ["nope"]
        p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(TemplateSchemaError, match="optionsInputs"):
        _load_from(tmp_path, monkeypatch, mutate)


# --- 액션 항목 ------------------------------------------------------------------------

def test_action_entries_mirror_app_presets():
    entries = {t["id"]: t for t in tr.all_entries()}
    for pid in APP_PRESETS:
        preset = entries[pid]
        action = entries["action." + pid[len("ai."):]]
        assert preset["builder"] is False and preset["generation"] is True, pid
        assert action["builder"] is True and action["generation"] is False, pid
        assert action["node_type"] == "ACTION"
        assert action["tool_key"] == preset["tool_key"]
        assert action["fixed"] == {"type": "ACTION", "config": {
            "tools": [{"name": preset["tool_key"]}], "serviceType": preset["app"]}}
    assert len([t for t in entries.values() if t["node_type"] == "ACTION"]) == 17


def test_tool_fields_shared_by_preset_and_action():
    entries = {t["id"]: t for t in tr.all_entries()}
    for pid in APP_PRESETS:
        tool = lambda e: [f for f in e["inputFields"] if f["path"].startswith("config.tools.0.config.")]
        assert tool(entries[pid]) == tool(entries["action." + pid[3:]]), pid


def test_input_fields_have_paths_and_no_injected_params():
    for t in tr.all_entries():
        for f in t["inputFields"]:
            assert f["path"].startswith("config."), (t["id"], f)
            assert f["key"] not in {"token", "access_token", "webhook_url", "sender_email",
                                    "sender_password", "smtp_host", "smtp_port",
                                    "workflow_context_data"}, (t["id"], f["key"])


# --- 의도된 차이 ②③ + Review Focus 1·4 --------------------------------------------------

def test_sheets_slots_renamed():
    for tid in ("ai.google_sheets_read", "ai.google_sheets_write", "ai.google_sheets_append"):
        names = [s["name"] for s in tr.get_template(tid)["slots"]]
        assert names[-3:] == ["spreadsheet_id", "spreadsheet_id_name", "sheet_name"], tid


def test_old_spreadsheet_name_slot_error_names_new_slot():
    """옛 채팅 세션의 slot 이름은 거부되고, 에러가 새 이름을 알려줘 재시도 루프가 고친다."""
    draft = {"id": "n2", "templateId": "ai.google_sheets_write", "slots": {
        "label": "l", "description": "d", "prompt": "p", "spreadsheet_name": "장부"}}
    with pytest.raises(SlotFillError) as e:
        tr.hydrate_node(draft, provider="GEMINI")
    assert "spreadsheet_name" in str(e.value) and "spreadsheet_id_name" in str(e.value)


def test_ai_node_with_top_level_names_passes_whitelist():
    node = {"id": "n2", "type": "AI", "label": "l", "config": {
        "agentType": "simple", "credentialId": "", "tools": [], "llmProvider": "GEMINI",
        "model": "m", "prompt": "p", "_names": {"credentialId": "내 키"}}}
    WorkflowValidator._validate_config_fields(node, "n2")


def test_http_allows_webhook_credential_but_no_slot():
    tpl = tr.get_template("http")
    assert {"webhookCredentialId", "_names"} <= set(tpl["allowed_config_fields"])
    assert "webhookCredentialId" not in {s["name"] for s in tpl["slots"]}


def test_designer_prompt_uses_new_name_slot():
    from core.workflow_chat import _SYSTEM_PROMPT_BASE
    assert "spreadsheet_id_name" in _SYSTEM_PROMPT_BASE
    assert "(spreadsheet_name)" not in _SYSTEM_PROMPT_BASE

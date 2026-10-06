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


# SMTP 자격증명(sender_email·sender_password) 주입 경로가 없어 실행할 수 없다 — OAuth 전환 전까지 빌더에서 숨긴다.
HIDDEN_ACTIONS = {"ai.gmail_send"}


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


def test_duplicate_input_field_key_rejected():
    raw = _raw("http")
    raw["inputFields"].append({"key": "url", "title": "두 번째 URL", "type": "string"})
    with pytest.raises(TemplateSchemaError, match="중복"):
        tr._validate_template(raw, os.path.join(tr.TEMPLATES_DIR, "http.json"), tr._tool_map_keys())


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


def _edit_raw(d, tid, change):
    p = d / f"{tid}.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    change(raw)
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")


def test_final_input_field_keys_unique(tmp_path, monkeypatch):
    """직속 필드와 도구 필드를 합친 최종 inputFields에서도 key가 겹치면 거부한다(FE 폼은 key로 칸을 찾는다)."""
    def mutate(d):
        _edit_raw(d, "ai.web_search", lambda raw: raw.setdefault("inputFields", []).append(
            {"key": "query", "title": "검색어", "type": "string"}))
    with pytest.raises(TemplateSchemaError, match="query"):
        _load_from(tmp_path, monkeypatch, mutate)


def test_agent_tool_choices_skip_builder_hidden_actions(tmp_path, monkeypatch):
    """builder: false 액션은 빌더에 없으니 ai.agent 도구 선택지에도 없다(그 항목은 title도 필수가 아니다)."""
    def hide(raw):
        raw["builder"] = False
        del raw["title"]
    templates = _load_from(tmp_path, monkeypatch, lambda d: _edit_raw(d, "action.slack_send", hide))
    tools = next(f for f in templates["ai.agent"]["inputFields"] if f["key"] == "tools")
    ids = {c["id"] for c in tools["choices"]}
    assert "slack" not in ids and "discord" in ids


def test_agent_entry_requires_tools_field(tmp_path, monkeypatch):
    def drop_tools(raw):
        raw["inputFields"] = [f for f in raw["inputFields"] if f["key"] != "tools"]
    with pytest.raises(TemplateSchemaError, match="ai.agent.*tools"):
        _load_from(tmp_path, monkeypatch, lambda d: _edit_raw(d, "ai.agent", drop_tools))


# --- 액션 항목 ------------------------------------------------------------------------

def test_action_entries_mirror_app_presets():
    entries = {t["id"]: t for t in tr.all_entries()}
    for pid in APP_PRESETS:
        preset = entries[pid]
        action = entries["action." + pid[len("ai."):]]
        assert preset["builder"] is False and preset["generation"] is True, pid
        assert action["builder"] is (pid not in HIDDEN_ACTIONS) and action["generation"] is False, pid
        assert action["node_type"] == "ACTION"
        assert action["tool_key"] == preset["tool_key"]
        assert action["fixed"] == {"type": "ACTION", "config": {
            "tools": [{"name": preset["tool_key"]}], "serviceType": preset["app"]}}
    # 앱 프리셋 17개의 거울 + 프리셋 없는 GitHub 액션 2개
    assert len([t for t in entries.values() if t["node_type"] == "ACTION"]) == 19


@pytest.mark.parametrize("tid, tool_key, keys", [
    ("action.github_list_issues", "builtin:github_list_issues", ["owner", "repo", "state"]),
    ("action.github_create_issue", "builtin:github_create_issue", ["owner", "repo", "title", "body"]),
])
def test_github_action_entries(tid, tool_key, keys):
    t = tr.get_template(tid)
    assert (t["node_type"], t["app"], t["tool_key"]) == ("ACTION", "GITHUB", tool_key)
    assert (t["builder"], t["generation"]) == (True, False)
    assert t["fixed"] == {"type": "ACTION", "config": {"tools": [{"name": tool_key}], "serviceType": "GITHUB"}}
    # 입력 칸 = 시그니처 순서. 실행 시 주입되는 token은 칸에서 빠진다.
    assert [f["key"] for f in t["inputFields"]] == keys
    assert all(f["path"] == f"config.tools.0.config.{f['key']}" for f in t["inputFields"])


def test_github_actions_hidden_from_generation_views():
    ids = {t["id"] for t in tr.all_templates()}
    assert not {i for i in ids if i.startswith("action.")}
    assert "builtin:github_create_issue" not in tr.menu_index()


def test_github_tool_key_drift_still_rejected():
    """builtin:github_* 허용이 접두사 통과가 아니라 실제 등록된 함수 기준이어야 한다."""
    path = os.path.join(tr.TEMPLATES_DIR, "action.github_list_issues.json")
    raw = _raw("action.github_list_issues")
    raw["tool_key"] = "builtin:github_nope"
    with pytest.raises(TemplateSchemaError, match="드리프트"):
        tr._validate_template(raw, path, tr._tool_map_keys())
    raw = _raw("action.github_list_issues")
    raw["fixed"]["config"]["tools"][0]["name"] = "builtin:github_nope"
    with pytest.raises(TemplateSchemaError, match="드리프트"):
        tr._validate_template(raw, path, tr._tool_map_keys())


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


# --- 의도된 차이 ①④ — Calendar·Drive·Notion 리소스 ID 슬롯 ---------------------------------

RESOURCE_SLOTS = {
    "ai.google_calendar_create": ["calendar_id", "calendar_id_name"],
    "ai.google_calendar_list": ["calendar_id", "calendar_id_name"],
    "ai.google_calendar_update": ["event_id", "event_id_name", "calendar_id", "calendar_id_name"],
    "ai.google_drive_read": ["file_id", "file_id_name"],
    "ai.google_drive_upload": ["folder_id", "folder_id_name"],
    "ai.notion_create_page": ["parent_page_id", "parent_page_id_name"],
    "ai.notion_read_page": ["page_id", "page_id_name"],
    "ai.notion_update_page": ["page_id", "page_id_name"],
    "ai.notion_append_block": ["page_id", "page_id_name"],
    "ai.notion_query_database": ["database_id", "database_id_name"],
}


@pytest.mark.parametrize("tid, names", RESOURCE_SLOTS.items())
def test_app_presets_gain_optional_resource_slots(tid, names):
    slots = {s["name"]: s for s in tr.get_template(tid)["slots"]}
    for name in names:
        assert slots[name]["required"] is False and slots[name]["kind"] == "string", (tid, name)
    key = names[0]
    assert slots[key]["path"] == f"config.tools.0.config.{key}"
    assert slots[f"{key}_name"]["path"] == f"config.tools.0.config._names.{key}"


@pytest.mark.parametrize("blank", ["", "  ", None])
def test_blank_calendar_slot_is_not_bound(blank):
    node = tr.hydrate_node({"id": "n2", "templateId": "ai.google_calendar_create", "slots": {
        "label": "l", "description": "d", "prompt": "p", "calendar_id": blank}}, provider="GEMINI")
    assert node["config"]["tools"] == [{"name": "builtin:google_calendar_create"}]


def test_calendar_slot_binds_with_display_name():
    node = tr.hydrate_node({"id": "n2", "templateId": "ai.google_calendar_update", "slots": {
        "label": "l", "description": "d", "prompt": "p",
        "event_id": "evt1", "calendar_id": "team@group", "calendar_id_name": "팀 캘린더"}},
        provider="GEMINI")
    assert node["config"]["tools"] == [{"name": "builtin:google_calendar_update", "config": {
        "event_id": "evt1", "calendar_id": "team@group", "_names": {"calendar_id": "팀 캘린더"}}}]


def test_field_meta_removed():
    import tools.registry as reg
    assert not hasattr(reg, "FIELD_META")


def test_designer_prompt_points_resource_ids_to_slots():
    from core.workflow_chat import _SYSTEM_PROMPT_BASE
    assert "Calendar calendar_id" in _SYSTEM_PROMPT_BASE
    assert "(Notion·GitHub 등)" not in _SYSTEM_PROMPT_BASE


def test_gmail_action_hidden_from_builder():
    from core.node_catalog import node_catalog

    assert tr.get_template("action.gmail_send")["builder"] is False
    assert "action.gmail_send" not in {e["id"] for e in node_catalog()["entries"]}
    tools = next(f for f in tr.get_template("ai.agent")["inputFields"] if f["key"] == "tools")
    assert "gmail" not in {c["id"] for c in tools["choices"]}
    # 저장된 gmail ACTION 노드는 어떤 빌더 항목에도 안 걸린다(FE가 폼을 못 연다 — 의도)
    assert tr.match_entry({"type": "ACTION", "config": {"tools": [{"name": "gmail"}]}}) is None


def test_gmail_generation_preset_unchanged_by_hiding_action():
    """숨기는 건 빌더용 액션뿐이다 — 채팅 생성이 쓰는 ai.gmail_send 프리셋은 그대로다."""
    assert "ai.gmail_send" in {t["id"] for t in tr.all_templates()}
    assert tr.get_template("ai.gmail_send")["generation"] is True

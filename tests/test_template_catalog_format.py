import glob
import json
import os

import pytest

from core import template_registry as tr
from core.template_registry import SlotFillError, TemplateSchemaError
from tests.catalog_paths import apps, copy_catalog, edit_entry, read_raw

APP_PRESETS = [
    "ai.slack_send", "ai.discord_send", "ai.gmail_send",
    "ai.notion_create_page", "ai.notion_read_page", "ai.notion_search", "ai.notion_update_page",
    "ai.notion_append_block", "ai.notion_query_database",
    "ai.google_sheets_read", "ai.google_sheets_write", "ai.google_sheets_append",
    "ai.google_calendar_create", "ai.google_calendar_find", "ai.google_calendar_update",
    "ai.google_drive_read", "ai.google_drive_upload",
]


# SMTP 자격증명(sender_email·sender_password) 주입 경로가 없어 실행할 수 없다 — OAuth 전환 전까지 빌더에서 숨긴다.
HIDDEN_ACTIONS = {"ai.gmail_send"}


# --- 파일 형식 ---------------------------------------------------------------------

def test_no_template_file_keeps_legacy_keys():
    raws = []
    for path in glob.glob(os.path.join(tr.CATALOG_DIR, "events", "*", "*.json")):
        with open(path, encoding="utf-8") as f:
            raws.append((path, json.load(f)))
    with open(os.path.join(tr.CATALOG_DIR, "presets.json"), encoding="utf-8") as f:
        raws += [(p["id"], p) for p in json.load(f)]
    for where, raw in raws:
        assert not ({"slots", "allowed_config_fields"} & raw.keys()), where
        assert "serviceType" not in (raw["fixed"].get("config") or {}), where


def test_legacy_keys_rejected():
    bad = {**read_raw("manual.run"), "slots": []}
    with pytest.raises(TemplateSchemaError, match="slots"):
        tr._validate_template(bad, bad["id"], tr._tool_map_keys(), apps())


def test_service_type_in_fixed_rejected():
    raw = read_raw("ai.gmail_send")
    raw["fixed"]["config"]["serviceType"] = "GOOGLE"
    with pytest.raises(TemplateSchemaError, match="serviceType은 폐지"):
        tr._validate_template(raw, raw["id"], tr._tool_map_keys(), apps())


def test_duplicate_input_field_key_rejected():
    raw = read_raw("http.request")
    raw["inputFields"].append({"key": "url", "title": "두 번째 URL", "type": "string"})
    with pytest.raises(TemplateSchemaError, match="중복"):
        tr._validate_template(raw, raw["id"], tr._tool_map_keys(), apps())


def test_fields_require_tool_key():
    raw = {**read_raw("http.request"), "fields": {"url": {"title": "x"}}}
    with pytest.raises(TemplateSchemaError, match="tool_key"):
        tr._validate_template(raw, raw["id"], tr._tool_map_keys(), apps())


def _load_from(tmp_path, monkeypatch, mutate):
    """카탈로그를 복사해 고친 뒤 강제 로드한다(경로·캐시는 monkeypatch가 복원)."""
    root = copy_catalog(tmp_path, monkeypatch)
    mutate(root)
    return tr.load_templates(force=True)


def test_builder_entries_follow_reload(tmp_path, monkeypatch):
    """builder_entries 메모는 force 재로드·_cache 교체 후 새 레지스트리를 반영한다."""
    before = {t["id"] for t in tr.builder_entries()}
    def mutate(d):
        (d / "events" / "webhook" / "catch.json").unlink()
    _load_from(tmp_path, monkeypatch, mutate)
    assert {t["id"] for t in tr.builder_entries()} == before - {"webhook.catch"}
    monkeypatch.setattr(tr, "_cache", {})
    assert tr.builder_entries() == []


def test_duplicate_tool_field_owner_rejected(tmp_path, monkeypatch):
    def mutate(d):
        edit_entry(d, "ai.google_sheets_read", lambda raw: raw.__setitem__("fields", {"cell_range": {"title": "중복"}}))
    with pytest.raises(TemplateSchemaError, match="한 항목에만"):
        _load_from(tmp_path, monkeypatch, mutate)


def test_options_inputs_must_point_to_same_entry(tmp_path, monkeypatch):
    def mutate(d):
        edit_entry(d, "google_sheets.read",
                   lambda raw: raw["fields"]["sheet_name"].__setitem__("optionsInputs", ["nope"]))
    with pytest.raises(TemplateSchemaError, match="optionsInputs"):
        _load_from(tmp_path, monkeypatch, mutate)


def _add_dynamic_field(inputs):
    return lambda d: edit_entry(d, "http.request", lambda raw: raw["inputFields"].append(
        {"key": "props", "title": "속성", "type": "dict", "fieldsSource": "x.props", "fieldsInputs": inputs}))


def test_fields_inputs_must_point_to_same_entry(tmp_path, monkeypatch):
    """fieldsInputs도 optionsInputs와 같은 규칙 — 최종 inputFields(직속 + 도구 필드) key만 가리킨다."""
    with pytest.raises(TemplateSchemaError, match=r"'props'의 fieldsInputs \['nope'\]"):
        _load_from(tmp_path, monkeypatch, _add_dynamic_field(["nope"]))


def test_fields_source_loads_and_reaches_entry(tmp_path, monkeypatch):
    templates = _load_from(tmp_path, monkeypatch, _add_dynamic_field(["url"]))
    props = next(f for f in templates["http.request"]["inputFields"] if f["key"] == "props")
    assert (props["fieldsSource"], props["fieldsInputs"]) == ("x.props", ["url"])


def test_final_input_field_keys_unique(tmp_path, monkeypatch):
    """직속 필드와 도구 필드를 합친 최종 inputFields에서도 key가 겹치면 거부한다(FE 폼은 key로 칸을 찾는다)."""
    def mutate(d):
        edit_entry(d, "ai.web_search", lambda raw: raw.setdefault("inputFields", []).append(
            {"key": "query", "title": "검색어", "type": "string"}))
    with pytest.raises(TemplateSchemaError, match="query"):
        _load_from(tmp_path, monkeypatch, mutate)


def test_agent_tool_choices_skip_builder_hidden_actions(tmp_path, monkeypatch):
    """builder: false 액션은 빌더에 없으니 ai.agent 도구 선택지에도 없다(그 항목은 title도 필수가 아니다)."""
    def hide(raw):
        raw["builder"] = False
        del raw["title"]
    templates = _load_from(tmp_path, monkeypatch, lambda d: edit_entry(d, "slack.send_message", hide))
    tools = next(f for f in templates["ai.agent"]["inputFields"] if f["key"] == "tools")
    ids = {c["id"] for c in tools["choices"]}
    assert "slack" not in ids and "discord" in ids


def test_agent_entry_requires_tools_field(tmp_path, monkeypatch):
    def drop_tools(raw):
        raw["inputFields"] = [f for f in raw["inputFields"] if f["key"] != "tools"]
    with pytest.raises(TemplateSchemaError, match="ai.agent.*tools"):
        _load_from(tmp_path, monkeypatch, lambda d: edit_entry(d, "ai.agent", drop_tools))


# --- 액션 항목 ------------------------------------------------------------------------

def test_action_entries_mirror_app_presets():
    entries = {t["id"]: t for t in tr.all_entries()}
    for pid in APP_PRESETS:
        preset = entries[pid]
        action = next(t for t in entries.values()
                      if t["node_type"] == "ACTION" and t["tool_key"] == preset["tool_key"])
        assert preset["builder"] is False and preset["generation"] is True, pid
        assert action["builder"] is (pid not in HIDDEN_ACTIONS) and action["generation"] is False, pid
        assert action["node_type"] == "ACTION"
        assert action["tool_key"] == preset["tool_key"]
        assert action["fixed"] == {"type": "ACTION", "event": action["id"], "config": {}}
    # 앱 프리셋의 거울 + 프리셋 없는 GitHub 액션 + AI에 싣지 않는 캘린더 삭제(ACTION 전용)
    # + 프리셋 없이 ai.agent 도구 선택지로만 AI에 싣는 Slack·Discord 봇·Notion DB 행(spec 2026-10-20 §9, 2026-10-21 §9,
    #   2026-10-26 §1)
    mirrored = {t["id"] for t in entries.values() if t["node_type"] == "ACTION"
                and any(entries[pid]["tool_key"] == t["tool_key"] for pid in APP_PRESETS)}
    assert {t["id"] for t in entries.values() if t["node_type"] == "ACTION"} == \
        mirrored | {"github.list_issues", "github.create_issue", "google_calendar.delete",
                    "slack_bot.send_channel_message", "discord_bot.send_channel_message",
                    "notion.create_row", "notion.update_row"}


@pytest.mark.parametrize("tid, tool_key, keys", [
    ("github.list_issues", "builtin:github_list_issues", ["owner", "repo", "state"]),
    ("github.create_issue", "builtin:github_create_issue", ["owner", "repo", "title", "body"]),
])
def test_github_action_entries(tid, tool_key, keys):
    t = tr.get_template(tid)
    assert (t["node_type"], t["app"], t["tool_key"]) == ("ACTION", "github", tool_key)
    assert (t["builder"], t["generation"]) == (True, False)
    assert t["fixed"] == {"type": "ACTION", "event": tid, "config": {}}
    # 입력 칸 = 시그니처 순서. 실행 시 주입되는 token은 칸에서 빠진다.
    assert [f["key"] for f in t["inputFields"]] == keys
    assert all(f["path"] == f"config.{f['key']}" for f in t["inputFields"])


def test_github_actions_hidden_from_generation_views():
    assert not {t["id"] for t in tr.all_templates() if t["node_type"] == "ACTION"}
    assert "builtin:github_create_issue" not in tr.menu_index()


def test_github_tool_key_drift_still_rejected():
    """builtin:github_* 허용이 접두사 통과가 아니라 실제 등록된 함수 기준이어야 한다."""
    raw = read_raw("github.list_issues")
    raw["tool_key"] = "builtin:github_nope"
    with pytest.raises(TemplateSchemaError, match="드리프트"):
        tr._validate_template(raw, raw["id"], tr._tool_map_keys(), apps())
    raw = read_raw("ai.web_search")              # fixed.config.tools 드리프트 검사는 AI 프리셋 몫으로 남는다
    raw["fixed"]["config"]["tools"][0]["name"] = "builtin:github_nope"
    with pytest.raises(TemplateSchemaError, match="드리프트"):
        tr._validate_template(raw, raw["id"], tr._tool_map_keys(), apps())


def test_tool_fields_shared_by_preset_and_action():
    """같은 도구의 칸 정의는 프리셋·액션이 공유하고 경로만 다르다(프리셋 config.tools.0.config.<칸>, 액션 config.<칸>)."""
    entries = {t["id"]: t for t in tr.all_entries()}
    for pid in APP_PRESETS:
        preset = [{**f, "path": f["path"].replace("config.tools.0.config.", "config.", 1)}
                  for f in entries[pid]["inputFields"] if f["path"].startswith("config.tools.0.config.")]
        action = next(t for t in entries.values()
                      if t["node_type"] == "ACTION" and t["tool_key"] == entries[pid]["tool_key"])
        assert preset == action["inputFields"], pid


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


def test_http_allows_webhook_credential_but_no_slot():
    tpl = tr.get_template("http.request")
    assert {"webhookCredentialId", "_names"} <= set(tpl["allowed_config_fields"])
    assert "webhookCredentialId" not in {s["name"] for s in tpl["slots"]}


# --- 의도된 차이 ①④ — Calendar·Drive·Notion 리소스 ID 슬롯 ---------------------------------

RESOURCE_SLOTS = {
    "ai.google_calendar_create": ["calendar_id", "calendar_id_name"],
    "ai.google_calendar_find": ["calendar_id", "calendar_id_name"],
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
    assert [n for n in slots if n in names] == names, tid
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


def test_gmail_action_hidden_from_builder():
    from core.node_catalog import node_catalog

    assert tr.get_template("gmail.send_email")["builder"] is False
    assert "gmail.send_email" not in {e["id"] for e in node_catalog()["entries"]}
    tools = next(f for f in tr.get_template("ai.agent")["inputFields"] if f["key"] == "tools")
    assert "gmail" not in {c["id"] for c in tools["choices"]}
    # 저장된 gmail ACTION 노드는 어떤 빌더 항목에도 안 걸린다(FE가 폼을 못 연다 — 의도)
    assert tr.match_entry({"type": "ACTION", "config": {"tools": [{"name": "gmail"}]}}) is None


def test_gmail_generation_preset_unchanged_by_hiding_action():
    """숨기는 건 빌더용 액션뿐이다 — 채팅 생성이 쓰는 ai.gmail_send 프리셋은 그대로다."""
    assert "ai.gmail_send" in {t["id"] for t in tr.all_templates()}
    assert tr.get_template("ai.gmail_send")["generation"] is True


def test_cold_load_never_enters_intent_brand_path(monkeypatch):
    """로더가 brand를 계산하는 중에 select_by_tags(→ load_templates 재진입 → 무한 재귀)를 부르면 기동이 실패한다."""
    def boom(*args, **kwargs):
        raise AssertionError("로더 안에서 intent 경로(select_by_tags)를 탔다")

    monkeypatch.setattr(tr, "select_by_tags", boom)
    monkeypatch.setattr(tr, "_cache", None)
    templates = tr.load_templates(force=True)
    assert templates["ai.github_query"]["brand"] == "github"
    assert templates["ai.reasoning"]["brand"] == "openai"
    assert templates["github.create_issue"]["brand"] == "github"


def test_calendar_delete는_ai_agent_도구_선택지에_없다():
    """삭제는 ACTION 전용(spec §4.3) — 선택지에 실리면 AI 노드가 일정을 지울 수 있다. 찾기는 실린다."""
    tools = next(f for f in tr.get_template("ai.agent")["inputFields"] if f["key"] == "tools")
    ids = {c["id"] for c in tools["choices"]}
    assert "builtin:google_calendar_delete" not in ids
    assert "builtin:google_calendar_find" in ids
    assert not any(t.get("tool_key") == "builtin:google_calendar_delete" for t in tr.all_templates()
                   if t.get("preset"))

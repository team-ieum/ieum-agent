import json

import pytest
from fastapi.testclient import TestClient

from core import template_registry as tr
from core.node_catalog import node_catalog
from tools.registry import RUNTIME_INJECTED_PARAMS


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def test_route_open_without_llm_headers():
    from main import app
    resp = TestClient(app).get("/v1/nodes/catalog")
    assert resp.status_code == 200
    assert resp.json() == node_catalog()


def test_old_tools_schema_route_removed():
    from main import app
    assert TestClient(app).get("/v1/tools/schema").status_code == 404


def test_catalog_lists_builder_entries_in_match_order():
    ids = [e["id"] for e in node_catalog()["entries"]]
    assert ids == [t["id"] for t in tr.builder_entries()]
    assert "ai.google_sheets_write" not in ids      # 생성 프리셋은 빌더에 없다
    assert {"slack.send_message", "ai.agent", "schedule.every"} <= set(ids)
    # 프리셋은 빌더 목록에 없다(설계 결정 4) — match를 쓴 프리셋도 매칭 후보로만 남는다
    assert not {"ai.web_search", "ai.http_fetch", "ai.mcp", "ai.github_query"} & set(ids)


def test_catalog_leaks_no_llm_or_injected_keys():
    for d in _walk(node_catalog()):
        assert "llm" not in d, d
        assert not set(d) & RUNTIME_INJECTED_PARAMS, d
        if "key" in d:
            assert d["key"] not in RUNTIME_INJECTED_PARAMS, d


def test_entry_shape_and_default_attrs_omitted():
    e = next(e for e in node_catalog()["entries"] if e["id"] == "schedule.every")
    assert e == {
        "id": "schedule.every", "kind": "trigger", "provider": "ieum",
        "nodeType": "TRIGGER", "app": "schedule", "appTitle": "스케줄",
        "title": "스케줄", "description": "정해둔 시각마다 시작해요.",
        "fixed": {"type": "TRIGGER", "event": "schedule.every", "config": {"brand": "webhook"}},
        "inputFields": [{"key": "cron", "title": "실행 주기", "type": "cron", "required": True,
                         "ref": False, "path": "config.cron",
                         "description": "표준 5필드 크론식 (예: 매일 9시 = 0 9 * * *)"}],
        "outputFields": [{"key": "triggeredAt", "title": "실행 시각", "type": "datetime"},
                         {"key": "cron", "title": "크론식", "type": "string"}],
    }


def test_entry_has_no_match_and_fixed_is_type_event_brand():
    """카탈로그 응답(spec §5.2): match 없음, fixed = {type, event, config}.
    비AI는 config = {brand}, AI(ai.agent·ai.reasoning)는 BE AgentNodeExecutor가 읽는 agentType·credentialId·tools를
    더 싣는다(spec §4 AI 노드 config 유지). 키 집합을 정확히 고정한다 — 빠져도 늘어도 실패."""
    ai_ids = set()
    for e in node_catalog()["entries"]:
        assert "match" not in e, e["id"]
        fixed = e["fixed"]
        assert set(fixed) == {"type", "event", "config"}, e["id"]
        assert (fixed["type"], fixed["event"]) == (e["nodeType"], e["id"])
        if e["nodeType"] == "AI":
            ai_ids.add(e["id"])
            assert set(fixed["config"]) == {"agentType", "credentialId", "tools", "brand"}, e["id"]
        else:
            assert set(fixed["config"]) == {"brand"}, e["id"]
    assert ai_ids == {"ai.agent", "ai.reasoning"}


def test_dynamic_markers_and_app_in_entries():
    by_id = {e["id"]: e for e in node_catalog()["entries"]}
    assert by_id["webhook.catch"]["outputDynamic"] is True
    assert by_id["transform.map"]["outputsFrom"] == "config.mappings"
    assert by_id["google_sheets.update_row"]["app"] == "google_sheets"
    assert by_id["gmail.new_email"]["app"] == "gmail"
    assert by_id["manual.run"]["app"] == "manual"      # 내장 항목도 앱 키가 있다


def test_도구_선택지마다_toolKey가_같은_ACTION_항목이_하나다():
    """FE가 AI 도구 칸을 ACTION 항목 inputFields로 그린다(FE 순서 0-F) — choice.id == entry.toolKey로 찾는다."""
    entries = node_catalog()["entries"]
    agent = next(e for e in entries if e["id"] == "ai.agent")
    choices = next(f for f in agent["inputFields"] if f["key"] == "tools")["choices"]
    assert choices
    for c in choices:
        hits = [e for e in entries if e.get("toolKey") == c["id"]]
        assert len(hits) == 1, c
        assert (hits[0]["nodeType"], hits[0]["title"]) == ("ACTION", c["name"])
    assert "toolKey" not in agent                      # 도구 없는 항목은 키를 생략한다


def test_앱_표시_이름은_apps_json_title이다():
    by_id = {e["id"]: e for e in node_catalog()["entries"]}
    assert by_id["slack.send_message"]["appTitle"] == "Slack (웹훅)"
    assert by_id["slack_bot.send_channel_message"]["appTitle"] == "Slack"
    assert by_id["discord.send_message"]["appTitle"] == "Discord (웹훅)"
    assert by_id["discord_bot.send_channel_message"]["appTitle"] == "Discord"
    assert by_id["google_calendar.find"]["appTitle"] == "Google Calendar"
    assert by_id["google_forms.get_form"]["appTitle"] == "Google Forms"


def test_agent_tools_field_is_not_ref():
    """tools는 목록 구조라 참조식 칸이 아니다(BE parseTools·agent 모두 리스트를 기대한다)."""
    agent = next(e for e in node_catalog()["entries"] if e["id"] == "ai.agent")
    assert next(f for f in agent["inputFields"] if f["key"] == "tools")["ref"] is False


def test_option_source_fields_fixed_set():
    """optionsSource가 붙은 (항목, 필드) 집합을 고정한다 — 의도치 않게 번지거나 빠지면 실패.
    (옛 tool_form_schema '공급원이 붙는 필드 집합' 테스트의 후신)"""
    got = {(e["id"], f["key"]) for e in node_catalog()["entries"]
           for f in e["inputFields"] if "optionsSource" in f}
    ai = {"ai.reasoning", "ai.agent"}  # 프리셋은 빌더 목록에 없다(설계 결정 4)
    expected = {(a, k) for a in ai for k in ("model", "credentialId")} | {
        ("http.request", "webhookCredentialId"),
        ("approval.request", "webhookCredentialId"), ("approval.collect", "webhookCredentialId"),
        ("slack.send_message", "webhookCredentialId"), ("discord.send_message", "webhookCredentialId"),
        ("slack_bot.send_channel_message", "channel"),
        ("discord_bot.send_channel_message", "guild_id"), ("discord_bot.send_channel_message", "channel_id"),
        *[(f"google_sheets.{x}", k)
          for x in ("read", "append", "append_top", "update_row", "lookup_row", "find_or_create_row",
                    "lookup_rows", "get_rows", "get_row", "append_rows", "update_rows", "clear_rows",
                    "delete_rows", "format_rows")
          for k in ("spreadsheet_id", "sheet_id")],
        *[(f"google_sheets.{x}", k) for x in ("lookup_row", "find_or_create_row", "lookup_rows")
          for k in ("lookup_column", "lookup_column_2")],
        ("google_sheets.find_worksheet", "spreadsheet_id"), ("google_sheets.get_spreadsheet", "spreadsheet_id"),
        ("google_calendar.create", "calendar_id"), ("google_calendar.find", "calendar_id"),
        ("google_calendar.update", "calendar_id"), ("google_calendar.update", "event_id"),
        ("google_calendar.delete", "calendar_id"), ("google_calendar.delete", "event_id"),
        ("google_drive.read", "file_id"), ("google_drive.upload", "folder_id"),
        ("notion.create_page", "parent_page_id"), ("notion.read_page", "page_id"),
        ("notion.update_page", "page_id"), ("notion.append_block", "page_id"),
        ("notion.query_database", "database_id"),
        ("notion.create_row", "data_source_id"), ("notion.create_row", "template"),
        ("notion.update_row", "data_source_id"), ("notion.update_row", "row_id"),
        *[(f"github.{x}", k) for x in ("list_issues", "create_issue", "get_pr_files", "submit_pr_review",
                                       "find_issue", "update_issue", "add_labels", "create_comment",
                                       "find_or_create_issue", "create_pull_request", "update_pull_request",
                                       "request_reviewers", "find_pull_request", "find_or_create_pull_request",
                                       "get_pr_reviews", "find_branch", "get_file")
          for k in ("owner", "repo")],
        ("github.find_repo", "owner"), ("github.check_org_membership", "org"),
        *[(f"github.{x}", "milestone") for x in ("create_issue", "update_issue", "find_or_create_issue")],
        *[(f"github.{x}", k) for x in ("create_pull_request", "find_or_create_pull_request")
          for k in ("head", "base")],
        ("github.update_pull_request", "base"), ("github.find_branch", "branch"),
        ("github.new_issue", "repoId"), ("github.new_pull_request", "repoId"),
        ("gmail.send_email", "from_email"), ("gmail.send_email", "label_ids"),
        ("gmail.find_email", "label_ids"), ("gmail.get_thread", "thread_id"),
        *[(f"gmail.{x}", k) for x in ("add_label", "remove_label") for k in ("message_id", "label_ids")],
        ("gmail.remove_thread_label", "thread_id"), ("gmail.remove_thread_label", "label_ids"),
        ("gmail.archive", "message_id"), ("gmail.trash", "message_id"),
        ("google_forms.set_accepting_responses", "form_id"), ("google_forms.get_form", "form_id"),
        ("gmail.reply", "thread_id"), ("gmail.create_reply_draft", "thread_id"),
        ("gmail.forward", "message_id"), ("gmail.find_or_send", "search_label_ids"),
        *[(f"gmail.{x}", "from_email") for x in ("reply", "forward", "create_draft", "create_reply_draft",
                                                  "find_or_send")],
        *[(f"gmail.{x}", "label_ids") for x in ("reply", "forward", "find_or_send")],
        ("google_forms.new_response", "form_id"), ("google_forms.new_or_updated_response", "form_id"),
        ("google_forms.list_responses", "form_id"), ("google_forms.get_response", "form_id"),
        ("google_forms.get_response", "response_id"),
    }
    assert got == expected


# --- 앱별 드롭다운 공급원 계약 (IEUM-AI-62, 옛 도구 폼 스키마 테스트에서 이전) ---------------

def _action(entry_id: str) -> dict:
    return next(e for e in node_catalog()["entries"] if e["id"] == entry_id)


def _field(entry_id: str, key: str) -> dict:
    return next(f for f in _action(entry_id)["inputFields"] if f["key"] == key)


def test_forms_응답_받기_칸은_참조식을_받지_않는다():
    """문자열 "false"가 참으로 읽혀 응답이 켜지는 길을 칸에서도 막는다(도구는 bool만 받는다)."""
    assert _field("google_forms.set_accepting_responses", "accepting")["ref"] is False


@pytest.mark.parametrize("field, title, sources, inputs", [
    ("calendar_id", "캘린더", {"google.calendars", "google.writable_calendars"}, None),
    ("event_id", "일정", {"google.events"}, ["calendar_id"]),
    ("file_id", "파일", {"google.files"}, None),
    ("folder_id", "폴더", {"google.folders"}, None),
    ("page_id", "페이지", {"notion.pages"}, None),
    ("parent_page_id", "상위 페이지", {"notion.pages"}, None),
    ("database_id", "데이터베이스", {"notion.databases"}, None),
    ("data_source_id", "데이터베이스", {"notion.databases"}, None),
    # D16: 공급원이 notion.pages인 page_id와 key를 나눈다 — 같은 key = 같은 공급원
    ("row_id", "행", {"notion.data_source_items"}, ["data_source_id"]),
    ("template", "템플릿", {"notion.templates"}, ["data_source_id"]),
    ("owner", "소유자", {"github.owners"}, None),
    ("repo", "저장소", {"github.repos"}, ["owner"]),
    ("spreadsheet_id", "스프레드시트", {"google.spreadsheets"}, None),
    ("sheet_id", "워크시트", {"google.worksheets"}, ["spreadsheet_id"]),
    ("lookup_column", "찾을 열", {"google.sheet_columns"}, ["spreadsheet_id", "sheet_id"]),
    ("lookup_column_2", "보조 열", {"google.sheet_columns"}, ["spreadsheet_id", "sheet_id"]),
    ("form_id", "폼", {"google.forms"}, None),
    ("response_id", "응답", {"google.form_responses"}, ["form_id"]),
])
def test_app_option_sources(field, title, sources, inputs):
    """공급원 키는 BE OptionSource.key()와 1:1 계약(IEUM-BE-72·73·76). 그 필드를 가진 액션 항목 전부에서
    제목·optionsInputs가 같고 공급원은 칸별 집합 안이어야 한다."""
    # github.find_repo의 repo는 남의 레포·참조식 조회용 입력 칸(드롭다운 없음, 2026-11-09 결정)
    found = [f for e in node_catalog()["entries"] if e["nodeType"] == "ACTION"
             and (e["id"], field) != ("github.find_repo", "repo")
             for f in e["inputFields"] if f["key"] == field]
    assert found, field
    for f in found:
        assert (f["title"], f.get("optionsInputs")) == (title, inputs)
        assert f["optionsSource"] in sources
        assert f["description"]


_SHEETS_FIELDS = {
    "google_sheets.append": ["spreadsheet_id", "sheet_id", "columns", "value_input_option"],
    "google_sheets.append_top": ["spreadsheet_id", "sheet_id", "columns", "value_input_option"],
    "google_sheets.update_row": ["spreadsheet_id", "sheet_id", "row_number", "columns", "overwrite_all",
                                 "value_input_option"],
    "google_sheets.lookup_row": ["spreadsheet_id", "sheet_id", "lookup_column", "lookup_value",
                                 "lookup_column_2", "lookup_value_2", "from_bottom"],
    "google_sheets.find_or_create_row": ["spreadsheet_id", "sheet_id", "lookup_column", "lookup_value",
                                         "lookup_column_2", "lookup_value_2", "from_bottom", "columns",
                                         "value_input_option"],
    "google_sheets.read": ["spreadsheet_id", "sheet_id", "cell_range"],
    "google_sheets.lookup_rows": ["spreadsheet_id", "sheet_id", "lookup_column", "lookup_value",
                                  "lookup_column_2", "lookup_value_2", "from_bottom", "start_row", "max_rows"],
    "google_sheets.get_rows": ["spreadsheet_id", "sheet_id", "column_range", "row_count", "start_row"],
    "google_sheets.get_row": ["spreadsheet_id", "sheet_id", "row_number"],
    "google_sheets.find_spreadsheet": ["name", "match"],
    "google_sheets.find_worksheet": ["spreadsheet_id", "title"],
    "google_sheets.get_spreadsheet": ["spreadsheet_id"],
    "google_sheets.append_rows": ["spreadsheet_id", "sheet_id", "rows", "column_map", "value_input_option"],
    "google_sheets.update_rows": ["spreadsheet_id", "sheet_id", "rows", "column_map", "overwrite_all",
                                  "value_input_option"],
    "google_sheets.clear_rows": ["spreadsheet_id", "sheet_id", "row_numbers"],
    "google_sheets.delete_rows": ["spreadsheet_id", "sheet_id", "row_numbers", "confirm_delete"],
    "google_sheets.format_rows": ["spreadsheet_id", "sheet_id", "row_numbers", "background_color", "text_color",
                                  "bold", "italic", "strikethrough"],
}


@pytest.mark.parametrize("event, keys", _SHEETS_FIELDS.items())
def test_sheets_열별_칸_노드_계약(event, keys):
    f = {x["key"]: x for x in _action(event)["inputFields"]}
    assert list(f) == keys
    if "spreadsheet_id" in f:
        assert (f["spreadsheet_id"]["required"], f["spreadsheet_id"]["ref"]) == (True, False)
    if "sheet_id" in f:
        assert (f["sheet_id"]["required"], f["sheet_id"]["ref"], f["sheet_id"]["optionsSource"]) == \
            (True, False, "google.worksheets")
    if "columns" in f:
        c = f["columns"]
        assert (c["type"], c["ref"], c["required"], c["fieldsSource"], c["fieldsInputs"]) == \
            ("dict", False, True, "google.sheet_columns", ["spreadsheet_id", "sheet_id"])
    if "value_input_option" in f:
        v = f["value_input_option"]
        assert ([x["id"] for x in v["choices"]], v["default"], v["ref"]) == \
            (["USER_ENTERED", "RAW"], "USER_ENTERED", False)
    if "lookup_value" in f:
        assert f["lookup_value"]["required"] is True and "default" not in f["lookup_value"]
        assert (f["from_bottom"]["default"], f["from_bottom"]["ref"]) == (False, False)
    if "overwrite_all" in f:
        assert (f["overwrite_all"]["default"], f["overwrite_all"]["ref"]) == (False, False)
        if "row_number" in f:
            assert (f["row_number"]["type"], f["row_number"]["required"]) == ("integer", True)
    if "rows" in f:
        assert (f["rows"]["type"], f["rows"]["required"]) == ("json", True)
    if "column_map" in f:
        c = f["column_map"]
        assert (c["type"], c["ref"], c["fieldsSource"], c["fieldsInputs"]) == \
            ("dict", False, "google.sheet_columns", ["spreadsheet_id", "sheet_id"])
        assert "required" not in c  # _public_field가 기본값(required False)을 뺀다
    if "row_numbers" in f:
        assert (f["row_numbers"]["type"], f["row_numbers"]["required"]) == ("string", True)
    if "confirm_delete" in f:
        c = f["confirm_delete"]
        assert (c["type"], c["required"], c["ref"]) == ("boolean", True, False) and "default" not in c
    for k in ("bold", "italic", "strikethrough"):
        if k in f:
            assert (f[k]["type"], f[k]["ref"]) == ("boolean", False)
            assert "required" not in f[k] and "default" not in f[k]


def test_sheets_출력_칸():
    outs = {e: [x["key"] for x in _action(e)["outputFields"]] for e in _SHEETS_FIELDS}
    for e in ("google_sheets.append", "google_sheets.append_top", "google_sheets.update_row"):
        assert outs[e] == ["updatedRange", "row_number"]
    assert outs["google_sheets.lookup_row"] == ["found", "total", "items", "row_number", "values"]
    assert outs["google_sheets.find_or_create_row"] == ["created", "row_number", "values"]
    assert _action("google_sheets.lookup_row")["kind"] == "search"
    assert outs["google_sheets.lookup_rows"] == outs["google_sheets.get_rows"] == \
        ["found", "total", "items", "truncated"]
    assert outs["google_sheets.get_row"] == ["found", "total", "items", "row_number", "values"]
    assert outs["google_sheets.find_spreadsheet"] == ["found", "total", "items", "truncated"]
    assert outs["google_sheets.find_worksheet"] == ["found", "total", "items", "sheet_id", "title"]
    assert outs["google_sheets.get_spreadsheet"] == ["found", "total", "items", "title", "url", "sheets"]
    for x in ("lookup_rows", "get_rows", "get_row", "find_spreadsheet", "find_worksheet", "get_spreadsheet"):
        assert _action(f"google_sheets.{x}")["kind"] == "search"
    assert outs["google_sheets.append_rows"] == ["updatedRange", "row_number", "row_count"]
    for x in ("update_rows", "clear_rows", "delete_rows", "format_rows"):
        assert outs[f"google_sheets.{x}"] == ["row_count", "row_numbers"]
    for x in ("append_rows", "update_rows", "clear_rows", "delete_rows", "format_rows"):
        assert _action(f"google_sheets.{x}")["kind"] == "action"
    assert "google_sheets.write" not in {e["id"] for e in node_catalog()["entries"]}


def test_event_id는_뒤의_calendar_id를_가리킨다():
    """필드 순서는 시그니처 순서 그대로 — optionsInputs가 뒤 필드를 가리킬 수 있다(FE-51 명세)."""
    keys = [f["key"] for f in _action("google_calendar.update")["inputFields"]]
    assert keys.index("event_id") < keys.index("calendar_id")


# --- fixed.config.brand (FE 목록 아이콘·BE 연동 목록은 config.brand만 본다) ---------------------

EXPECTED_BRANDS = {
    "ai.reasoning": "openai", "ai.agent": "openai",
    "manual.run": "webhook", "schedule.every": "webhook", "schedule.hourly": "webhook", "schedule.daily": "webhook",
    "schedule.weekly": "webhook", "schedule.monthly": "webhook", "webhook.catch": "webhook",
    "gmail.new_email": "gmail", "github.new_issue": "github", "github.new_pull_request": "github",
    "gmail.send_email": "gmail",
    **{f"gmail.{x}": "gmail" for x in (
        "find_email", "get_thread", "add_label", "remove_label", "remove_thread_label", "archive", "trash",
        "create_label", "reply", "forward", "create_draft", "create_reply_draft", "find_or_send")},
    "http.request": "webhook", "approval.request": "webhook", "approval.collect": "webhook", "branch.if": "filter", "transform.map": "filter",
    "slack.send_message": "slack", "discord.send_message": "discord",
    "slack_bot.send_channel_message": "slack",
    "discord_bot.send_channel_message": "discord",
    "notion.create_page": "notion", "notion.read_page": "notion",
    "notion.search": "notion", "notion.update_page": "notion",
    "notion.append_block": "notion", "notion.query_database": "notion",
    "notion.create_row": "notion", "notion.update_row": "notion",
    "google_sheets.read": "sheets", "google_sheets.append": "sheets", "google_sheets.append_top": "sheets",
    "google_sheets.update_row": "sheets", "google_sheets.lookup_row": "sheets",
    "google_sheets.find_or_create_row": "sheets",
    "google_sheets.lookup_rows": "sheets", "google_sheets.get_rows": "sheets", "google_sheets.get_row": "sheets",
    "google_sheets.find_spreadsheet": "sheets", "google_sheets.find_worksheet": "sheets",
    "google_sheets.get_spreadsheet": "sheets",
    **{f"google_sheets.{x}": "sheets" for x in (
        "append_rows", "update_rows", "clear_rows", "delete_rows", "format_rows")},
    "google_calendar.create": "google", "google_calendar.find": "google",
    "google_calendar.update": "google", "google_calendar.delete": "google", "google_drive.read": "google",
    "google_drive.upload": "google",
    "google_forms.set_accepting_responses": "google", "google_forms.get_form": "google",
    **{f"google_forms.{x}": "google" for x in (
        "list_responses", "get_response", "new_response", "new_or_updated_response")},
    "github.list_issues": "github", "github.create_issue": "github",
    "github.get_pr_files": "github", "github.submit_pr_review": "github",
    **{f"github.{x}": "github" for x in (
        "find_issue", "update_issue", "add_labels", "create_comment", "find_or_create_issue",
        "create_pull_request", "update_pull_request", "request_reviewers", "find_pull_request",
        "find_or_create_pull_request", "get_pr_reviews", "find_branch", "get_file", "find_repo", "find_org",
        "check_org_membership", "find_user")},
    "delay.for": "webhook", "delay.until": "webhook", "delay.queue": "webhook",
    "formatter.text_case": "filter",
    "formatter.text_trim": "filter",
    "formatter.text_replace": "filter",
    "formatter.text_extract": "filter",
    "formatter.text_extract_pattern": "filter",
    "formatter.text_split": "filter",
    "formatter.text_truncate": "filter",
    "formatter.text_length": "filter",
    "formatter.text_default": "filter",
    "formatter.text_strip_html": "filter",
    "formatter.text_markdown_to_html": "filter",
    "formatter.text_url_encode": "filter",
    "formatter.number_format": "filter",
    "formatter.number_currency": "filter",
    "formatter.number_math": "filter",
    "formatter.number_phone": "filter",
    "formatter.date_format": "filter",
    "formatter.date_add": "filter",
    "formatter.date_compare": "filter",
    "formatter.list_pick": "filter",
    "formatter.list_to_text": "filter",
    "formatter.lookup_table": "filter",
}


def test_catalog_fixed_brand_per_entry():
    got = {e["id"]: e["fixed"]["config"]["brand"] for e in node_catalog()["entries"]}
    assert got == EXPECTED_BRANDS


def test_trigger_repo_option_source_contract():
    """github.installed_repos는 BE OptionSource.key()와 1:1 계약(BE-b). 값은 repo id(문자열), 입력 없음.
    액션의 github.repos(owner 입력, 값=name)와 다른 공급원이다."""
    f = _field("github.new_issue", "repoId")
    assert (f["optionsSource"], f.get("optionsInputs"), f["required"], f["ref"]) == \
        ("github.installed_repos", None, True, False)


def test_action_entries_never_fall_through_to_default_brand():
    """_service_of가 서비스를 못 읽으면 ACTION이 기본값 webhook으로 떨어진다(github가 실제로 그랬다)."""
    for e in node_catalog()["entries"]:
        if e["nodeType"] == "ACTION":
            assert e["fixed"]["config"]["brand"] != "webhook", e["id"]


def test_catalog_does_not_mutate_registry_fixed():
    """brand는 응답 복사본에만 얹는다 — 레지스트리 fixed는 빌더 add_node와 공유한다."""
    node_catalog()
    node_catalog()
    for t in tr.all_entries():
        assert "brand" not in t["fixed"]["config"], t["id"]
    assert "brand" not in tr.get_template("ai.github_query")["fixed"]["config"]


def test_ai_reasoning_출력_칸_입력과_outputsFrom():
    """구조화 출력 spec §2.7. children도 non-partial 검증을 거쳐 title이 필수다(빠지면 카탈로그 전체가 죽는다)."""
    e = next(e for e in node_catalog()["entries"] if e["id"] == "ai.reasoning")
    field = next(f for f in e["inputFields"] if f["key"] == "outputFields")
    assert field == {
        "key": "outputFields", "title": "출력 칸", "type": "object", "list": True, "ref": False,
        "path": "config.outputFields",
        "description": "AI가 이 칸들로만 답해요. 다음 단계에서 칸 하나씩 골라 쓸 수 있어요. 비우면 결과 한 칸이에요.",
        "children": [
            {"key": "key", "title": "이름", "type": "string", "required": True},
            {"key": "type", "title": "타입", "type": "string", "required": True,
             "choices": [{"id": "text", "name": "텍스트"}, {"id": "number", "name": "숫자"},
                         {"id": "boolean", "name": "참/거짓"}]},
            {"key": "description", "title": "설명", "type": "text"},
        ],
    }
    assert e["outputsFrom"] == "config.outputFields"
    assert e["outputFields"] == [{"key": "output", "title": "결과", "type": "text"}]


def test_출력_칸_타입_선택지는_agent_규칙과_같다():
    from core.output_fields import _SCHEMA_TYPES
    e = next(e for e in node_catalog()["entries"] if e["id"] == "ai.reasoning")
    type_child = next(c for f in e["inputFields"] if f["key"] == "outputFields" for c in f["children"]
                      if c["key"] == "type")
    assert {c["id"] for c in type_child["choices"]} == set(_SCHEMA_TYPES)


def test_entry_has_kind_provider_app_and_fixed_event():
    entries = {e["id"]: e for e in node_catalog()["entries"]}
    gmail = entries["gmail.new_email"]
    assert (gmail["kind"], gmail["provider"], gmail["app"]) == ("trigger", "google", "gmail")
    assert gmail["fixed"]["event"] == "gmail.new_email"
    assert entries["notion.search"]["kind"] == "search"
    assert (entries["manual.run"]["provider"], entries["manual.run"]["app"]) == ("ieum", "manual")


def test_calendar_id_공급원은_찾기만_전체_캘린더다():
    """쓰기 노드는 쓰기 권한 캘린더만(BE google.writable_calendars) — 공휴일 캘린더에 쓰면 403(spec §4)."""
    got = {e["id"]: f["optionsSource"] for e in node_catalog()["entries"] if e["id"].startswith("google_calendar.")
           for f in e["inputFields"] if f["key"] == "calendar_id"}
    assert got == {"google_calendar.find": "google.calendars",
                   "google_calendar.create": "google.writable_calendars",
                   "google_calendar.update": "google.writable_calendars",
                   "google_calendar.delete": "google.writable_calendars"}


@pytest.mark.parametrize("entry, key", [
    ("google_calendar.update", "scope"), ("google_calendar.update", "send_updates"),
    ("google_calendar.update", "all_day"), ("google_calendar.delete", "scope"),
    ("google_calendar.delete", "send_updates"),
])
def test_캘린더_선택형_불리언_칸은_참조식을_받지_않는다(entry, key):
    """참조식이면 선택지 검사가 건너뛰어지고 "false" 문자열은 파이썬에서 참이다(spec §3)."""
    assert _field(entry, key)["ref"] is False


def test_find_출력은_검색_약속을_따른다():
    """SCHEMA 앱 이벤트 약속 1 — found·total·items 필수."""
    for event in ("google_calendar.find", "gmail.find_email"):
        keys = {f["key"] for f in tr.get_template(event)["outputFields"]}
        assert {"found", "total", "items"} <= keys, event


def test_slack_bot_채널_메시지_계약():
    """spec 2026-10-20 §9 — 칸·드롭다운·출력이 BE(slack.channels)·도구 시그니처와 맞물린다."""
    e = _action("slack_bot.send_channel_message")
    assert (e["app"], e["provider"], e["kind"], e["fixed"]["config"]["brand"]) == \
        ("slack_bot", "slack", "action", "slack")
    fields = {f["key"]: f for f in e["inputFields"]}
    assert list(fields) == ["channel", "text", "thread_ts"]           # token은 주입 인자라 빠진다
    assert (fields["channel"]["title"], fields["channel"]["optionsSource"], fields["channel"]["required"]) == \
        ("채널", "slack.channels", True)
    assert (fields["text"]["type"], fields["text"]["required"]) == ("text", True)
    assert not fields["thread_ts"].get("required")
    assert [f["key"] for f in e["outputFields"]] == ["ts", "channel", "permalink"]


def test_slack_웹훅_노드는_이름을_바꾸고_channel_칸이_없다():
    e = _action("slack.send_message")
    assert e["title"] == "Slack 웹훅으로 보내기"
    assert {f["key"] for f in e["inputFields"]} == {"message", "webhookCredentialId"}


def test_AI_노드_도구_선택지에_Slack_두_가지가_이름으로_나뉜다():
    tools = next(f for f in tr.get_template("ai.agent")["inputFields"] if f["key"] == "tools")
    names = {c["id"]: c["name"] for c in tools["choices"]}
    assert names["slack"] == "Slack 웹훅으로 보내기"
    assert names["builtin:slack_send_channel_message"] == "Slack 채널에 메시지 보내기"


def test_discord_bot_채널_메시지_계약():
    """spec 2026-10-21 §9 — 서버(직속 칸) → 채널 → 메시지 순서, 채널 드롭다운은 서버 값을 입력으로 받는다."""
    e = _action("discord_bot.send_channel_message")
    assert (e["app"], e["provider"], e["kind"], e["fixed"]["config"]["brand"], e["toolKey"]) == \
        ("discord_bot", "discord", "action", "discord", "builtin:discord_send_channel_message")
    fields = {f["key"]: f for f in e["inputFields"]}
    assert list(fields) == ["guild_id", "channel_id", "content"]    # token·allowed_guild_ids는 주입 인자라 빠진다
    guild = fields["guild_id"]
    assert (guild["title"], guild["optionsSource"], guild["ref"], guild.get("required", False)) == \
        ("서버", "discord.guilds", False, False)                    # 실행에 안 쓰는 칸 — 필수면 참조식 채널 노드를 막는다
    channel = fields["channel_id"]
    assert (channel["title"], channel["optionsSource"], channel["optionsInputs"], channel["required"]) == \
        ("채널", "discord.channels", ["guild_id"], True)
    assert (fields["content"]["type"], fields["content"]["required"]) == ("text", True)
    assert [f["key"] for f in e["outputFields"]] == ["id", "channel_id", "url"]


def test_discord_웹훅_노드는_이름을_바꾸고_메시지_ID를_출력한다():
    e = _action("discord.send_message")
    assert e["title"] == "Discord 웹훅으로 보내기"
    assert [f["key"] for f in e["outputFields"]] == ["id", "channel_id"]


def test_AI_노드_도구_선택지에_Discord_두_가지가_이름으로_나뉜다():
    tools = next(f for f in tr.get_template("ai.agent")["inputFields"] if f["key"] == "tools")
    names = {c["id"]: c["name"] for c in tools["choices"]}
    assert names["discord"] == "Discord 웹훅으로 보내기"
    assert names["builtin:discord_send_channel_message"] == "Discord 채널에 메시지 보내기"


def test_notion_DB_행_노드_계약():
    """spec 2026-10-26 §3-2·§3-3 — 속성 칸은 DB를 고른 뒤 /fields로 받는 동적 칸, 칸 전체 참조식은 막는다."""
    create, update = _action("notion.create_row"), _action("notion.update_row")
    assert (create["toolKey"], update["toolKey"]) == ("builtin:notion_create_row", "builtin:notion_update_row")
    assert (create["title"], update["title"]) == ("DB에 행 만들기", "DB 행 수정")
    c = {f["key"]: f for f in create["inputFields"]}
    u = {f["key"]: f for f in update["inputFields"]}
    assert list(c) == ["data_source_id", "properties", "template", "content"]   # token은 주입 인자라 빠진다
    assert list(u) == ["row_id", "properties", "content", "data_source_id"]     # 시그니처 밖 칸은 뒤에 붙는다
    for fields in (c, u):
        assert fields["properties"] == {"key": "properties", "title": "속성", "type": "dict", "ref": False,
                                        "fieldsSource": "notion.properties", "fieldsInputs": ["data_source_id"],
                                        "path": "config.properties"}
        assert fields["data_source_id"]["ref"] is False
        assert (fields["content"]["type"], fields["content"].get("required", False)) == ("text", False)
    assert (c["data_source_id"]["required"], u["data_source_id"].get("required", False)) == (True, False)
    assert (u["row_id"]["required"], u["row_id"].get("ref", True)) == (True, True)   # 앞 노드가 찾은 행을 참조식으로
    assert (c["template"]["ref"], c["template"].get("required", False)) == (False, False)
    for e in (create, update):
        assert [f["key"] for f in e["outputFields"]] == ["pageId", "url"]
        assert "optionsParams" not in json.dumps(e)   # /fields 응답 전용


def test_AI_노드_도구_선택지에_Notion_DB_행_도구가_있다():
    tools = next(f for f in tr.get_template("ai.agent")["inputFields"] if f["key"] == "tools")
    names = {c["id"]: c["name"] for c in tools["choices"]}
    assert names["builtin:notion_create_row"] == "DB에 행 만들기"
    assert names["builtin:notion_update_row"] == "DB 행 수정"


def test_catalog_entries_snapshot():
    """빌더가 보는 모양을 통째로 고정한다(spec §7). 의도한 변경이면 fixture를 다시 만든다:
    python -c "import json; from core.node_catalog import node_catalog; print(json.dumps(node_catalog(), ensure_ascii=False, indent=2, sort_keys=True))" > tests/fixtures/node_catalog_snapshot.json"""
    import json, os
    path = os.path.join(os.path.dirname(__file__), "fixtures", "node_catalog_snapshot.json")
    with open(path, encoding="utf-8") as f:
        assert node_catalog() == json.load(f)


def test_gmail_send_email_칸():
    f = {x["key"]: x for x in _action("gmail.send_email")["inputFields"]}
    assert list(f) == ["to", "cc", "bcc", "from_email", "from_name", "reply_to", "subject",
                       "body_format", "body", "include_signature", "label_ids"]
    assert {k for k, x in f.items() if x.get("required")} == {"subject", "body"}
    assert (f["label_ids"]["list"], f["label_ids"]["optionsSource"], f["label_ids"]["ref"]) == \
        (True, "google.gmail_labels", False)
    assert (f["include_signature"]["type"], f["include_signature"]["default"]) == ("boolean", False)
    assert f["body_format"]["default"] == "text/plain" and f["body"]["type"] == "text"
    assert f["to"]["type"] == "string" and "list" not in f["to"]


def test_gmail_정리_노드_칸():
    """spec 2026-11-10 §3 — 칸 순서·필수·참조식 여부·드롭다운이 도구 시그니처·카탈로그와 맞물린다."""
    find = {x["key"]: x for x in _action("gmail.find_email")["inputFields"]}
    assert list(find) == ["query", "label_ids", "include_spam_trash", "max_results"]
    assert {k for k, x in find.items() if x.get("required")} == {"query"}
    assert find["max_results"]["default"] == 20
    assert find["include_spam_trash"]["ref"] is False
    for event in ("find_email", "add_label", "remove_label", "remove_thread_label"):
        assert _field(f"gmail.{event}", "label_ids")["ref"] is False, event
    for event, key, source in (("add_label", "message_id", "google.gmail_messages"),
                               ("remove_label", "message_id", "google.gmail_messages"),
                               ("archive", "message_id", "google.gmail_messages"),
                               ("trash", "message_id", "google.gmail_messages"),
                               ("get_thread", "thread_id", "google.gmail_threads"),
                               ("remove_thread_label", "thread_id", "google.gmail_threads")):
        f = _field(f"gmail.{event}", key)
        assert "ref" not in f and f["optionsSource"] == source and f["required"], (event, key)
    label = {x["key"]: x for x in _action("gmail.create_label")["inputFields"]}
    assert {k for k, x in label.items() if x.get("required")} == {"name"}
    for key in ("label_list_visibility", "message_list_visibility", "text_color", "background_color"):
        assert label[key]["ref"] is False, key
    colors = {c["id"] for c in label["text_color"]["choices"]}
    assert {"#000000", "#ffffff"} <= colors
    assert label["text_color"]["choices"] == label["background_color"]["choices"]


def test_gmail_보내기_계열_칸():
    """spec 2026-11-10 gmail-forms-9 §3 — 필수 칸과 대화·메일 드롭다운이 도구 시그니처와 맞물린다."""
    required = {"reply": {"thread_id", "body"}, "forward": {"message_id"}, "create_draft": set(),
                "create_reply_draft": {"thread_id", "body"}, "find_or_send": {"query", "subject", "body"}}
    for event, keys in required.items():
        got = {x["key"] for x in _action(f"gmail.{event}")["inputFields"] if x.get("required")}
        assert got == keys, event
    for event, key, source in (("reply", "thread_id", "google.gmail_threads"),
                               ("create_reply_draft", "thread_id", "google.gmail_threads"),
                               ("forward", "message_id", "google.gmail_messages")):
        f = _field(f"gmail.{event}", key)
        assert "ref" not in f and f["optionsSource"] == source, (event, key)
    search = _field("gmail.find_or_send", "search_label_ids")
    assert search["ref"] is False and search["optionsSource"] == "google.gmail_labels"

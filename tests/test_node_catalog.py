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
    assert {"action.slack_send", "ai.agent", "trigger.schedule"} <= set(ids)


def test_catalog_leaks_no_llm_or_injected_keys():
    for d in _walk(node_catalog()):
        assert "llm" not in d, d
        assert not set(d) & RUNTIME_INJECTED_PARAMS, d
        if "key" in d:
            assert d["key"] not in RUNTIME_INJECTED_PARAMS, d


def test_entry_shape_and_default_attrs_omitted():
    e = next(e for e in node_catalog()["entries"] if e["id"] == "trigger.schedule")
    assert e == {
        "id": "trigger.schedule", "nodeType": "TRIGGER", "app": None,
        "title": "스케줄", "description": "정해둔 시각마다 시작해요.",
        "match": {"type": "TRIGGER", "config.triggerType": "SCHEDULE"},
        "fixed": {"type": "TRIGGER", "config": {"triggerType": "SCHEDULE", "brand": "webhook"}},
        "inputFields": [{"key": "cron", "title": "실행 주기", "type": "cron", "required": True,
                         "ref": False, "path": "config.cron",
                         "description": "표준 5필드 크론식 (예: 매일 9시 = 0 9 * * *)"}],
        "outputFields": [{"key": "triggeredAt", "title": "실행 시각", "type": "datetime"},
                         {"key": "cron", "title": "크론식", "type": "string"}],
    }


def test_dynamic_markers_and_app_in_entries():
    by_id = {e["id"]: e for e in node_catalog()["entries"]}
    assert by_id["trigger.webhook"]["outputDynamic"] is True
    assert by_id["transform"]["outputsFrom"] == "config.mappings"
    assert by_id["action.google_sheets_write"]["app"] == "GOOGLE"
    assert by_id["action.google_sheets_write"]["fixed"]["config"]["serviceType"] == "GOOGLE"


def test_agent_tools_field_is_not_ref():
    """tools는 목록 구조라 참조식 칸이 아니다(BE parseTools·agent 모두 리스트를 기대한다)."""
    agent = next(e for e in node_catalog()["entries"] if e["id"] == "ai.agent")
    assert next(f for f in agent["inputFields"] if f["key"] == "tools")["ref"] is False


def test_option_source_fields_fixed_set():
    """optionsSource가 붙은 (항목, 필드) 집합을 고정한다 — 의도치 않게 번지거나 빠지면 실패.
    (옛 tool_form_schema '공급원이 붙는 필드 집합' 테스트의 후신)"""
    got = {(e["id"], f["key"]) for e in node_catalog()["entries"]
           for f in e["inputFields"] if "optionsSource" in f}
    ai = {"ai.reasoning", "ai.agent", "ai.mcp", "ai.github_query", "ai.web_search", "ai.http_fetch"}
    expected = {(a, k) for a in ai for k in ("model", "credentialId")} | {
        ("ai.mcp", "catalogId"), ("http", "webhookCredentialId"),
        ("action.slack_send", "webhookCredentialId"), ("action.discord_send", "webhookCredentialId"),
        *[(f"action.google_sheets_{x}", k) for x in ("read", "write", "append")
          for k in ("spreadsheet_id", "sheet_name")],
        ("action.google_calendar_create", "calendar_id"), ("action.google_calendar_list", "calendar_id"),
        ("action.google_calendar_update", "calendar_id"), ("action.google_calendar_update", "event_id"),
        ("action.google_drive_read", "file_id"), ("action.google_drive_upload", "folder_id"),
        ("action.notion_create_page", "parent_page_id"), ("action.notion_read_page", "page_id"),
        ("action.notion_update_page", "page_id"), ("action.notion_append_block", "page_id"),
        ("action.notion_query_database", "database_id"),
        *[(f"action.github_{x}", k) for x in ("list_issues", "create_issue") for k in ("owner", "repo")],
    }
    assert got == expected


# --- 앱별 드롭다운 공급원 계약 (IEUM-AI-62, 옛 도구 폼 스키마 테스트에서 이전) ---------------

def _action(entry_id: str) -> dict:
    return next(e for e in node_catalog()["entries"] if e["id"] == entry_id)


def _field(entry_id: str, key: str) -> dict:
    return next(f for f in _action(entry_id)["inputFields"] if f["key"] == key)


@pytest.mark.parametrize("field, title, source, inputs", [
    ("calendar_id", "캘린더", "google.calendars", None),
    ("event_id", "일정", "google.events", ["calendar_id"]),
    ("file_id", "파일", "google.files", None),
    ("folder_id", "폴더", "google.folders", None),
    ("page_id", "페이지", "notion.pages", None),
    ("parent_page_id", "상위 페이지", "notion.pages", None),
    ("database_id", "데이터베이스", "notion.databases", None),
    ("owner", "소유자", "github.owners", None),
    ("repo", "저장소", "github.repos", ["owner"]),
])
def test_app_option_sources(field, title, source, inputs):
    """공급원 키는 BE OptionSource.key()와 1:1 계약(IEUM-BE-72·73·76). 그 필드를 가진 액션 항목 전부에서 같아야 한다."""
    found = [f for e in node_catalog()["entries"] if e["nodeType"] == "ACTION"
             for f in e["inputFields"] if f["key"] == field]
    assert found, field
    for f in found:
        assert (f["title"], f["optionsSource"], f.get("optionsInputs")) == (title, source, inputs)
        assert f["description"]


def test_sheets_append_option_source_contract():
    sid = _field("action.google_sheets_append", "spreadsheet_id")
    assert (sid["title"], sid["optionsSource"], sid.get("optionsInputs"), sid.get("required")) == \
        ("스프레드시트", "google.spreadsheets", None, True)
    sheet = _field("action.google_sheets_append", "sheet_name")
    assert (sheet["title"], sheet["optionsSource"], sheet["optionsInputs"]) == \
        ("워크시트", "google.worksheets", ["spreadsheet_id"])


def test_event_id는_뒤의_calendar_id를_가리킨다():
    """필드 순서는 시그니처 순서 그대로 — optionsInputs가 뒤 필드를 가리킬 수 있다(FE-51 명세)."""
    keys = [f["key"] for f in _action("action.google_calendar_update")["inputFields"]]
    assert keys.index("event_id") < keys.index("calendar_id")


# --- fixed.config.brand (FE 목록 아이콘·BE 연동 목록은 config.brand만 본다) ---------------------

EXPECTED_BRANDS = {
    "ai.reasoning": "openai", "ai.agent": "openai", "ai.mcp": "openai", "ai.web_search": "openai",
    "ai.http_fetch": "openai", "ai.github_query": "github",
    "trigger.manual": "webhook", "trigger.schedule": "webhook", "trigger.webhook": "webhook",
    "http": "webhook", "approval": "webhook", "condition": "filter", "transform": "filter",
    "action.slack_send": "slack", "action.discord_send": "discord",
    "action.notion_create_page": "notion", "action.notion_read_page": "notion",
    "action.notion_search": "notion", "action.notion_update_page": "notion",
    "action.notion_append_block": "notion", "action.notion_query_database": "notion",
    "action.google_sheets_read": "sheets", "action.google_sheets_write": "sheets",
    "action.google_sheets_append": "sheets",
    "action.google_calendar_create": "google", "action.google_calendar_list": "google",
    "action.google_calendar_update": "google", "action.google_drive_read": "google",
    "action.google_drive_upload": "google",
    "action.github_list_issues": "github", "action.github_create_issue": "github",
}


def test_catalog_fixed_brand_per_entry():
    got = {e["id"]: e["fixed"]["config"]["brand"] for e in node_catalog()["entries"]}
    assert got == EXPECTED_BRANDS


def test_action_entries_never_fall_through_to_default_brand():
    """_service_of가 서비스를 못 읽으면 ACTION이 기본값 webhook으로 떨어진다(github가 실제로 그랬다)."""
    for e in node_catalog()["entries"]:
        if e["nodeType"] == "ACTION":
            assert e["fixed"]["config"]["brand"] != "webhook", e["id"]


def test_catalog_does_not_mutate_registry_fixed():
    """brand는 응답 복사본에만 얹는다 — 레지스트리 fixed는 생성 경로(hydrate_node)와 공유한다."""
    node_catalog()
    node_catalog()
    for t in tr.all_entries():
        assert "brand" not in t["fixed"]["config"], t["id"]
    assert "brand" not in tr.get_template("ai.github_query")["fixed"]["config"]

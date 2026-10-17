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
        "nodeType": "TRIGGER", "app": "schedule",
        "title": "스케줄", "description": "정해둔 시각마다 시작해요.",
        "fixed": {"type": "TRIGGER", "event": "schedule.every", "config": {"brand": "webhook"}},
        "inputFields": [{"key": "cron", "title": "실행 주기", "type": "cron", "required": True,
                         "ref": False, "path": "config.cron",
                         "description": "표준 5필드 크론식 (예: 매일 9시 = 0 9 * * *)"}],
        "outputFields": [{"key": "triggeredAt", "title": "실행 시각", "type": "datetime"},
                         {"key": "cron", "title": "크론식", "type": "string"}],
    }


def test_dynamic_markers_and_app_in_entries():
    by_id = {e["id"]: e for e in node_catalog()["entries"]}
    assert by_id["webhook.catch"]["outputDynamic"] is True
    assert by_id["transform.map"]["outputsFrom"] == "config.mappings"
    assert by_id["google_sheets.write"]["app"] == "google_sheets"
    assert by_id["gmail.new_email"]["app"] == "gmail"
    assert by_id["manual.run"]["app"] == "manual"      # 내장 항목도 앱 키가 있다


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
        ("slack.send_message", "webhookCredentialId"), ("discord.send_message", "webhookCredentialId"),
        *[(f"google_sheets.{x}", k) for x in ("read", "write", "append")
          for k in ("spreadsheet_id", "sheet_name")],
        ("google_calendar.create", "calendar_id"), ("google_calendar.list", "calendar_id"),
        ("google_calendar.update", "calendar_id"), ("google_calendar.update", "event_id"),
        ("google_drive.read", "file_id"), ("google_drive.upload", "folder_id"),
        ("notion.create_page", "parent_page_id"), ("notion.read_page", "page_id"),
        ("notion.update_page", "page_id"), ("notion.append_block", "page_id"),
        ("notion.query_database", "database_id"),
        *[(f"github.{x}", k) for x in ("list_issues", "create_issue") for k in ("owner", "repo")],
        ("github.new_issue", "repoId"),
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
    sid = _field("google_sheets.append", "spreadsheet_id")
    assert (sid["title"], sid["optionsSource"], sid.get("optionsInputs"), sid.get("required")) == \
        ("스프레드시트", "google.spreadsheets", None, True)
    sheet = _field("google_sheets.append", "sheet_name")
    assert (sheet["title"], sheet["optionsSource"], sheet["optionsInputs"]) == \
        ("워크시트", "google.worksheets", ["spreadsheet_id"])


def test_event_id는_뒤의_calendar_id를_가리킨다():
    """필드 순서는 시그니처 순서 그대로 — optionsInputs가 뒤 필드를 가리킬 수 있다(FE-51 명세)."""
    keys = [f["key"] for f in _action("google_calendar.update")["inputFields"]]
    assert keys.index("event_id") < keys.index("calendar_id")


# --- fixed.config.brand (FE 목록 아이콘·BE 연동 목록은 config.brand만 본다) ---------------------

EXPECTED_BRANDS = {
    "ai.reasoning": "openai", "ai.agent": "openai",
    "manual.run": "webhook", "schedule.every": "webhook", "webhook.catch": "webhook",
    "gmail.new_email": "gmail", "github.new_issue": "github",
    "http.request": "webhook", "approval.request": "webhook", "branch.if": "filter", "transform.map": "filter",
    "slack.send_message": "slack", "discord.send_message": "discord",
    "notion.create_page": "notion", "notion.read_page": "notion",
    "notion.search": "notion", "notion.update_page": "notion",
    "notion.append_block": "notion", "notion.query_database": "notion",
    "google_sheets.read": "sheets", "google_sheets.write": "sheets",
    "google_sheets.append": "sheets",
    "google_calendar.create": "google", "google_calendar.list": "google",
    "google_calendar.update": "google", "google_drive.read": "google",
    "google_drive.upload": "google",
    "github.list_issues": "github", "github.create_issue": "github",
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
    """brand는 응답 복사본에만 얹는다 — 레지스트리 fixed는 생성 경로(hydrate_node)와 공유한다."""
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


def test_출력_칸은_생성_슬롯이_아니다():
    """LLM이 칸을 설계하는 건 ⑤단계다. 슬롯이 되면 채팅 draft에 실려 LLM이 날조·삭제할 수 있다."""
    assert "outputFields" not in {s["name"] for s in tr.get_template("ai.reasoning")["slots"]}
    assert "outputFields" in tr.get_template("ai.reasoning")["allowed_config_fields"]


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


def test_catalog_entries_snapshot():
    """빌더가 보는 모양을 통째로 고정한다(spec §7). 의도한 변경이면 fixture를 다시 만든다:
    python -c "import json; from core.node_catalog import node_catalog; print(json.dumps(node_catalog(), ensure_ascii=False, indent=2, sort_keys=True))" > tests/fixtures/node_catalog_snapshot.json"""
    import json, os
    path = os.path.join(os.path.dirname(__file__), "fixtures", "node_catalog_snapshot.json")
    with open(path, encoding="utf-8") as f:
        assert node_catalog() == json.load(f)

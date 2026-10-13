"""AI-64 회귀 가드 — 템플릿 원본 형식을 바꿔도 생성 경로가 보는 것은 의도된 차이 말고 그대로다.

fixture는 AI-64 착수 전 dev(fb97575)의 레지스트리에서 떴다(아래 __main__). 의도된 차이(spec §7.3)는
INTENDED_* 상수로 명시하고, 그 밖의 차이는 실패한다. slot description은 어떤 LLM 프롬프트에도
들어가지 않아(slot_catalog_text는 이름·kind·필수만 출력) 비교에서 뺀다."""
import copy
import json
import os

from core import template_registry as tr

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "template_snapshot_before_ai64.json")

_SHEETS = ("ai.google_sheets_read", "ai.google_sheets_write", "ai.google_sheets_append")
_AI = (
    "ai.discord_send", "ai.github_query", "ai.gmail_send", "ai.google_calendar_create",
    "ai.google_calendar_list", "ai.google_calendar_update", "ai.google_drive_read",
    "ai.google_drive_upload", *_SHEETS, "ai.http_fetch", "ai.mcp", "ai.notion_append_block",
    "ai.notion_create_page", "ai.notion_query_database", "ai.notion_read_page", "ai.notion_search",
    "ai.notion_update_page", "ai.reasoning", "ai.slack_send", "ai.web_search",
)
# spec §7.3 ② — 표시 이름 슬롯 이름 규칙 `<key>_name`
INTENDED_SLOT_RENAMES: dict[str, str] = {"spreadsheet_name": "spreadsheet_id_name"}
# spec §7.3 ③ — 허용 config 키는 늘기만 한다
INTENDED_ALLOWED_ADDED: dict[str, set[str]] = {
    **{tid: {"_names"} for tid in _AI},
    "ai.reasoning": {"_names", "outputFields"},  # 구조화 출력 spec §2.7 — 슬롯 아님(llm.slot false)
    "http.request": {"_names", "webhookCredentialId"},
    "webhook.catch": {"includeRaw"},  # AI-76 — derive_allowed_config는 llm.slot과 무관하게 직속 필드를 허용 키에 넣는다
}
# spec §7.3 ① — Calendar·Drive·Notion 프리셋의 리소스 ID 슬롯
INTENDED_NEW_SLOTS: dict[str, list[str]] = {
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


with open(os.path.join(tr.CATALOG_DIR, "aliases.json"), encoding="utf-8") as _f:
    # 이름이 바뀐 이벤트만 번역한다. 프리셋은 옛 id 그대로다(별칭 값 ai.agent).
    RENAMED = {old: new for old, new in json.load(_f).items() if new != "ai.agent" and old != new}


def _renamed_menu(menu: str) -> list[str]:
    out = []
    for line in menu.splitlines():
        tid, sep, rest = line[len("- "):].partition(":")
        out.append(f"- {RENAMED.get(tid, tid)}{sep}{rest}")
    return sorted(out)


def _roundtrip(snippet: dict) -> dict:
    draft = tr.dehydrate_node(copy.deepcopy(snippet))
    node = tr.hydrate_node(draft, provider="GEMINI")
    node.get("config", {}).pop("model", None)  # .env마다 기본 모델이 달라 비교에서 뺀다
    return node


def capture() -> dict:
    out = {"menu_index": tr.menu_index(), "templates": {}}
    for tpl in tr.all_templates():
        snip = tpl.get("golden_snippet")
        out["templates"][tpl["id"]] = {
            "node_type": tpl["node_type"],
            "tool_key": tpl["tool_key"],
            "tags": tpl["tags"],
            "menu": tpl["menu"],
            "fixed": tpl["fixed"],
            "slots": [[s["name"], s["path"], s["required"], s["kind"]] for s in tpl["slots"]],
            "allowed_config_fields": sorted(tpl["allowed_config_fields"]),
            "golden_snippet": snip,
            "golden_roundtrip": _roundtrip(snip) if snip else None,
        }
    return out


def _load_fixture() -> dict:
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def test_generation_view_matches_snapshot_except_intended_diffs():
    before = _load_fixture()
    after = json.loads(json.dumps(capture(), ensure_ascii=False))
    # 로드 순서는 id 정렬이라 이름이 바뀌면 메뉴 줄 순서도 바뀐다 — 줄 집합으로 비교한다.
    assert sorted(after["menu_index"].splitlines()) == _renamed_menu(before["menu_index"])
    before_templates = {RENAMED.get(tid, tid): b for tid, b in before["templates"].items()}
    assert after["templates"].keys() == before_templates.keys()
    for tid, b in before_templates.items():
        expected = copy.deepcopy(b)
        expected["fixed"] = {**expected["fixed"], "event": tr.get_template(tid)["event"]}  # 카탈로그 전환: fixed.event
        if expected["golden_roundtrip"] is not None:  # hydrate가 event를 기록한다(Task 8)
            expected["golden_roundtrip"]["event"] = tr.get_template(tid)["event"]
        expected["slots"] = [[INTENDED_SLOT_RENAMES.get(s[0], s[0]), *s[1:]] for s in expected["slots"]]
        expected["allowed_config_fields"] = sorted(
            set(expected["allowed_config_fields"]) | INTENDED_ALLOWED_ADDED.get(tid, set()))
        actual = copy.deepcopy(after["templates"][tid])
        new = set(INTENDED_NEW_SLOTS.get(tid, ()))
        actual["slots"] = [s for s in actual["slots"] if s[0] not in new]
        assert actual == expected, tid


if __name__ == "__main__":  # 리팩터 전 dev에서 1회만 실행해 fixture를 만든다
    os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
    with open(FIXTURE, "w", encoding="utf-8") as f:
        json.dump(capture(), f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")

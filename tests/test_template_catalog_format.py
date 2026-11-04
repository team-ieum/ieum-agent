import glob
import json
import os

import pytest

from core import template_registry as tr
from core.template_registry import TemplateSchemaError
from tests.catalog_paths import apps, copy_catalog, edit_entry, read_raw

# --- 파일 형식 ---------------------------------------------------------------------

def test_no_template_file_keeps_legacy_keys():
    raws = []
    for path in glob.glob(os.path.join(tr.CATALOG_DIR, "events", "*", "*.json")):
        with open(path, encoding="utf-8") as f:
            raws.append((path, json.load(f)))
    with open(os.path.join(tr.CATALOG_DIR, "presets.json"), encoding="utf-8") as f:
        raws += [(p["id"], p) for p in json.load(f)]
    for where, raw in raws:
        assert not (tr._LEGACY_TOP_KEYS & raw.keys()), where
        assert "serviceType" not in (raw["fixed"].get("config") or {}), where


@pytest.mark.parametrize("key", ["slots", "allowed_config_fields", "tags", "menu", "golden_snippet", "generation"])
def test_legacy_keys_rejected(key):
    bad = {**read_raw("manual.run"), key: []}
    with pytest.raises(TemplateSchemaError, match=key):
        tr._validate_template(bad, bad["id"], tr._tool_map_keys(), apps())


def test_service_type_in_fixed_rejected():
    raw = read_raw("ai.web_search")
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
    def mutate(d):  # 같은 도구(builtin:google_sheets_read)에 fields를 쓰는 이벤트가 둘
        src = d / "events" / "google_sheets" / "read.json"
        (d / "events" / "google_sheets" / "read_copy.json").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
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

@pytest.mark.parametrize("tid, tool_key, keys", [
    ("github.list_issues", "builtin:github_list_issues", ["owner", "repo", "state"]),
    ("github.create_issue", "builtin:github_create_issue", ["owner", "repo", "title", "body"]),
])
def test_github_action_entries(tid, tool_key, keys):
    t = tr.get_template(tid)
    assert (t["node_type"], t["app"], t["tool_key"]) == ("ACTION", "github", tool_key)
    assert t["builder"] is True
    assert t["fixed"] == {"type": "ACTION", "event": tid, "config": {}}
    # 입력 칸 = 시그니처 순서. 실행 시 주입되는 token은 칸에서 빠진다.
    assert [f["key"] for f in t["inputFields"]] == keys
    assert all(f["path"] == f"config.{f['key']}" for f in t["inputFields"])


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


def test_input_fields_have_paths_and_no_injected_params():
    for t in tr.all_entries():
        for f in t["inputFields"]:
            assert f["path"].startswith("config."), (t["id"], f)
            assert f["key"] not in {"token", "access_token", "webhook_url",
                                    "workflow_context_data"}, (t["id"], f["key"])


def test_field_meta_removed():
    import tools.registry as reg
    assert not hasattr(reg, "FIELD_META")


def test_gmail_send_email은_빌더에_있고_AI_도구_선택지에_없다():
    from core.node_catalog import node_catalog

    assert tr.get_template("gmail.send_email")["builder"] is True
    assert tr.get_template("gmail.send_email")["tool_key"] == "builtin:google_gmail_send_email"
    assert "gmail.send_email" in {e["id"] for e in node_catalog()["entries"]}
    tools = next(f for f in tr.get_template("ai.agent")["inputFields"] if f["key"] == "tools")
    assert not {"gmail", "builtin:google_gmail_send_email"} & {c["id"] for c in tools["choices"]}


def test_cold_load_never_enters_match_entry_brand_path(monkeypatch):
    """로더가 brand를 계산하는 중에 match_entry(→ load_templates 재진입 → 무한 재귀)를 부르면 기동이 실패한다."""
    def boom(*args, **kwargs):
        raise AssertionError("로더 안에서 brand_for_node의 match_entry 경로를 탔다")

    monkeypatch.setattr(tr, "match_entry", boom)
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

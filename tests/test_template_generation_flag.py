import copy
import json

import pytest

from core import template_registry as tr
from tests.catalog_paths import apps, event_path

# 메모리상 정규화된 모양만 쓴다(파일 형식과 무관) — 생성 경로 필터만 검증한다.
_FAKE = {
    "id": "action.fake_send", "node_type": "ACTION", "tool_key": "slack",
    "tags": ["가짜전송"], "menu": "가짜 전송", "builder": True, "generation": False,
    "fixed": {"type": "ACTION", "config": {"tools": [{"name": "slack"}]}},
}


@pytest.fixture
def with_fake(monkeypatch):
    templates = dict(tr.load_templates())
    templates[_FAKE["id"]] = copy.deepcopy(_FAKE)
    monkeypatch.setattr(tr, "_cache", templates)


def test_builder_only_entry_hidden_from_generation_views(with_fake):
    assert _FAKE["id"] in {t["id"] for t in tr.all_entries()}
    assert _FAKE["id"] not in tr._generation_templates()
    assert all(t["id"] != _FAKE["id"] for t in tr.select_by_tags("가짜전송 해줘"))


def test_generation_flag_must_be_bool():
    with open(event_path("manual.run"), encoding="utf-8") as f:
        raw = {"id": "manual.run", "app": "manual", **json.load(f)}
    raw["generation"] = "no"
    with pytest.raises(tr.TemplateSchemaError, match="generation"):
        tr._validate_template(raw, raw["id"], tr._tool_map_keys(), apps())

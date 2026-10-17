import pytest
from pydantic import ValidationError

from api.schemas.generate_workflow import WorkflowNode
from core.template_registry import event_entry, trigger_type_of


def _trigger(**kw):
    return WorkflowNode(id="n1", type="TRIGGER", label="t", **kw)


def test_event_is_kept_on_round_trip():
    node = _trigger(event="manual.run", config={})
    assert node.model_dump()["event"] == "manual.run"


def test_trigger_with_only_event_passes():
    _trigger(event="gmail.new_email", config={"query": "is:unread"})


def test_trigger_with_neither_is_rejected():
    with pytest.raises(ValidationError, match="event 또는 config.triggerType"):
        _trigger(config={})


def test_unknown_event_is_not_rejected():
    # BE 카탈로그 ⊇ agent 카탈로그 — agent가 모르는 이벤트도 막지 않는다(spec §4 모르는 event).
    _trigger(event="stripe.new_payment", config={})


def test_schedule_event_requires_cron():
    with pytest.raises(ValidationError, match="cron"):
        _trigger(event="schedule.every", config={})
    _trigger(event="schedule.every", config={"cron": "0 9 * * *"})


def test_event가_옛_triggerType을_이겨_cron을_요구한다():
    # 옛 판정(레거시 우선)에선 MANUAL로 보고 통과시켰다 — 이제 event가 유일한 판정이다.
    with pytest.raises(ValidationError, match="cron"):
        _trigger(event="schedule.every", config={"triggerType": "MANUAL"})


def test_event_entry():
    assert event_entry({"event": "gmail.new_email"})["id"] == "gmail.new_email"
    assert event_entry({"event": "slack.send_message", "type": "TRIGGER"})["id"] == "slack.send_message"
    assert event_entry({"event": "ai.notion_create_page"}) is None      # 프리셋 id는 이벤트가 아니다
    assert event_entry({"event": "nope.nope"}) is None
    assert event_entry({"event": ["gmail.new_email"]}) is None
    assert event_entry({}) is None
    assert event_entry(None) is None


def test_trigger_type_of은_event만_본다():
    assert trigger_type_of({"event": "schedule.every", "config": {}}) == "SCHEDULE"
    assert trigger_type_of({"event": "gmail.new_email", "config": {}}) == "GMAIL_NEW_EMAIL"
    # 옛 config.triggerType은 읽지 않는다
    assert trigger_type_of({"event": "schedule.every", "config": {"triggerType": "MANUAL"}}) == "SCHEDULE"
    assert trigger_type_of({"config": {"triggerType": "WEBHOOK"}}) is None
    assert trigger_type_of({"event": "nope.nope", "config": {"triggerType": "WEBHOOK"}}) is None
    # 트리거가 아닌 event
    assert trigger_type_of({"event": "slack.send_message", "config": {}}) is None
    assert trigger_type_of({"event": "ai.agent", "config": {}}) is None


def test_trigger_type_of은_카탈로그_트리거_전부를_안다():
    from core import template_registry as tr
    triggers = [t for t in tr.all_entries() if t["node_type"] == "TRIGGER" and not t.get("preset")]
    assert triggers
    for t in triggers:
        assert trigger_type_of({"event": t["id"]}) == t["trigger_type"], t["id"]

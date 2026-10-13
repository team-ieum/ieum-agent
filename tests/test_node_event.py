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


def test_trigger_with_only_legacy_trigger_type_passes():
    _trigger(config={"triggerType": "MANUAL"})


def test_trigger_with_neither_is_rejected():
    with pytest.raises(ValidationError, match="event 또는 config.triggerType"):
        _trigger(config={})


def test_unknown_event_is_not_rejected():
    # BE 카탈로그 ⊇ agent 카탈로그 — agent가 모르는 이벤트도 막지 않는다(spec §5.4). BE는 레거시 triggerType을
    # event에서 계산해 기록하므로 그 값도 agent가 모르는 값일 수 있다.
    _trigger(event="stripe.new_payment", config={})
    _trigger(event="stripe.new_payment", config={"triggerType": "STRIPE_NEW_PAYMENT"})


def test_invalid_legacy_trigger_type_still_rejected():
    for bad in ("SLACK_NEW_MESSAGE", "", "gmail_new_email"):
        with pytest.raises(ValidationError):
            _trigger(config={"triggerType": bad})


def test_schedule_event_requires_cron():
    with pytest.raises(ValidationError, match="cron"):
        _trigger(event="schedule.every", config={})
    _trigger(event="schedule.every", config={"cron": "0 9 * * *"})


def test_event_entry_and_trigger_type_of():
    assert event_entry({"event": "gmail.new_email"})["id"] == "gmail.new_email"
    assert event_entry({"event": "ai.notion_create_page"}) is None      # 프리셋 id는 이벤트가 아니다
    assert event_entry({"event": "nope.nope"}) is None
    assert event_entry({}) is None
    assert trigger_type_of({"event": "schedule.every", "config": {}}) == "SCHEDULE"
    assert trigger_type_of({"event": "nope.nope", "config": {"triggerType": "WEBHOOK"}}) == "WEBHOOK"
    assert trigger_type_of({"config": {"triggerType": ["x"]}}) is None
    # 과도기엔 레거시가 이긴다 — 빌더로 MANUAL로 바꾼 뒤 BE 병합이 옛 event를 이어받은 노드("spec과 다른 점" 1)
    assert trigger_type_of({"event": "schedule.every", "config": {"triggerType": "MANUAL"}}) == "MANUAL"


def test_event_identity_only_when_legacy_fields_missing():
    from core.template_registry import event_identity
    assert event_identity({"type": "TRIGGER", "event": "gmail.new_email", "config": {}})["id"] == "gmail.new_email"
    assert event_identity({"type": "TRIGGER", "event": "schedule.every", "config": {"triggerType": "MANUAL"}}) is None
    assert event_identity({"type": "ACTION", "event": "slack.send_message", "config": {}})["id"] == "slack.send_message"
    assert event_identity({"type": "ACTION", "event": "slack.send_message",
                           "config": {"tools": [{"name": "discord"}]}}) is None
    assert event_identity({"type": "AI", "event": "ai.reasoning", "config": {}}) is None
    assert event_identity({"type": "HTTP", "event": "http.request", "config": {}}) is None

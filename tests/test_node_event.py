import pytest
from pydantic import ValidationError

from api.schemas.generate_workflow import WorkflowNode
from core.template_registry import event_entry, trigger_type_of


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


# ── WorkflowNode 스키마: event 필수, type은 event로 계산(spec §4) ──────────────

def _node(**kw):
    return WorkflowNode(**{"id": "n1", "label": "t", "config": {}, **kw})


@pytest.mark.parametrize("event", [None, ""])
def test_event_없거나_비면_422(event):
    with pytest.raises(ValidationError, match="event"):
        _node(type="APPROVAL", event=event)
    with pytest.raises(ValidationError, match="event"):
        _node(type="APPROVAL")


def test_아는_event면_type을_카탈로그_node_type으로_덮는다():
    assert _node(event="manual.run").type == "TRIGGER"                    # type 없이도 통과
    assert _node(event="slack.send_message", type="TRIGGER").type == "ACTION"   # 요청 type은 무시
    assert _node(event="gmail.new_email", type="trigger").type == "TRIGGER"
    assert _node(event="ai.agent", config={"llmProvider": "CLAUDE"}).type == "AI"
    assert _node(event="manual.run").model_dump()["event"] == "manual.run"


def test_덮인_type으로_config를_검사한다():
    # 요청이 TRIGGER라 해도 event가 AI면 AI 규칙(llmProvider 필수)을 탄다.
    with pytest.raises(ValidationError, match="llmProvider"):
        _node(event="ai.agent", type="TRIGGER")


def test_모르는_event는_받은_type을_유지하고_type이_없으면_422():
    # BE 카탈로그 ⊇ agent 카탈로그 — agent가 모르는 이벤트도 막지 않는다(spec §4).
    assert _node(event="stripe.new_payment", type="TRIGGER").type == "TRIGGER"
    assert _node(event="stripe.new_payment", type="ACTION", config={"amount": 1}).type == "ACTION"
    with pytest.raises(ValidationError, match="type이 필요"):
        _node(event="stripe.new_payment")
    with pytest.raises(ValidationError, match="유효하지 않은 노드 type"):
        _node(event="stripe.new_payment", type="NOPE")


def test_schedule_event만_cron을_요구한다():
    with pytest.raises(ValidationError, match="cron"):
        _node(event="schedule.every")
    _node(event="schedule.every", config={"cron": "0 9 * * *"})
    _node(event="manual.run")                                    # MANUAL은 cron 없음
    _node(event="stripe.new_payment", type="TRIGGER")            # 모르는 event는 cron 검사 대상이 아니다


def test_옛_필드는_판정에_쓰지_않는다():
    # config.triggerType이 MANUAL이어도 event가 schedule.every면 SCHEDULE — cron 필수.
    with pytest.raises(ValidationError, match="cron"):
        _node(event="schedule.every", config={"triggerType": "MANUAL"})
    # ACTION config.tools 검사는 없다 — ACTION config는 칸 값뿐.
    assert _node(event="slack.send_message", config={"tools": 5}).config == {"tools": 5}

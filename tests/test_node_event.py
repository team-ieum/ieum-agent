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

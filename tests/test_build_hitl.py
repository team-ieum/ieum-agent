"""테스트 판정·확인 응답·코드 요약 (spec 2026-10-28 §5.1·§5.2, IEUM-AI-84)."""
import pytest

from core.builder.hitl import classify_reply, finish_summary, sample_summary, schedule_text, target_summary, how_to_test


@pytest.mark.parametrize("text", ["네", "네!", " 예 ", "응.", "ㅇㅇ", "ㄱ", "좋아~", "진행", "테스트해", "YES", "ok"])
def test_긍정(text):
    assert classify_reply(text) == "yes"


@pytest.mark.parametrize("text", ["아니", "아니요.", "건너뛰기", "건너뛰어", "스킵", "No"])
def test_부정(text):
    assert classify_reply(text) == "no"


@pytest.mark.parametrize("text", ["네 근데 채널 바꿔줘", "아니 슬랙 말고 디스코드", "", "네네", "okay", "테스트"])
def test_그_밖은_other(text):
    assert classify_reply(text) == "other"


@pytest.mark.parametrize("event,kind", [
    ("slack_bot.send_channel_message", "write"),
    ("google_calendar.delete", "write"),
    ("http.request", "write"),
    ("ai.agent", "write"),
    ("unknown.thing", "write"),
    ("google_calendar.find", "read"),
    ("gmail.send_email", "write"),
    ("notion.search", "read"),
    ("gmail.new_email", "app_trigger"),
    ("github.new_issue", "app_trigger"),
    ("schedule.every", "free"),
    ("manual.run", "free"),
    ("ai.reasoning", "free"),
    ("branch.if", "free"),
    ("transform.map", "free"),
    ("approval.request", "free"),
    ("approval.collect", "free"),
    ("webhook.catch", "listen"),
])
def test_테스트_판정(event, kind):
    assert how_to_test({"id": "n", "event": event, "config": {}}) == kind



def test_요약에_메일_받는_사람을_보인다():
    node = {"id": "g", "event": "gmail.send_email", "label": "보고",
            "config": {"to": "kim@ieum.io", "cc": "lee@ieum.io", "bcc": "park@ieum.io"}}
    assert target_summary(node, {}) == \
        'Gmail 메일 보내기 · 받는 사람: kim@ieum.io · 참조: lee@ieum.io · 숨은참조: park@ieum.io · 이름 "보고"'

def test_http는_GET도_쓰기이고_요약에_method와_호스트():
    node = {"id": "h", "event": "http.request", "label": "조회", "config": {"method": "GET", "url": "https://api.x.com/a?k=1"}}
    assert how_to_test(node) == "write"
    assert target_summary(node, {}) == 'HTTP 요청 · GET api.x.com · 이름 "조회"'


def test_요약은_후보_이름을_id로_찾고_없으면_id만():
    node = {"id": "s", "event": "slack_bot.send_channel_message", "label": "알림",
            "config": {"channel": "C1", "text": "x", "_names": {"channel": "#가짜이름"}}}
    assert target_summary(node, {"s|config.channel": {"C1": "#general"}}).endswith('채널: #general · 이름 "알림"')
    summary = target_summary(node, {})
    assert "(목록에서 확인 안 됨, id=C1)" in summary and "가짜이름" not in summary


def test_요약에_참석자_메일을_보인다():
    node = {"id": "c", "event": "google_calendar.update", "label": "회의",
            "config": {"attendees": "kim@ieum.io, evil@x.com"}}
    assert target_summary(node, {}) == '캘린더 일정 수정 · 참석자: kim@ieum.io, evil@x.com · 이름 "회의"'


def test_완료_요약은_트리거와_쓰기_노드():
    nodes = [{"id": "t", "event": "schedule.every", "label": "매일", "config": {"cron": "0 9 * * *"}},
             {"id": "a", "event": "ai.reasoning", "label": "요약", "config": {}},
             {"id": "s", "event": "slack_bot.send_channel_message", "label": "알림", "config": {"channel": "C1"}}]
    text = finish_summary(nodes, {"s|config.channel": {"C1": "#general"}})
    assert text.startswith("켜지는 트리거: ")
    assert "cron 0 9 * * *" in text and "#general" in text and "요약" not in text


def test_샘플_요약은_필드_4개_값_30자():
    out = {"a": "x" * 50, "b": {"k": 1}, "c": 3, "d": None, "e": "숨김"}
    text = sample_summary(out)
    assert text.startswith("테스트 성공 — a: " + "x" * 30 + " · b: {\"k\": 1}")
    assert "e:" not in text and "\n" not in text


def test_옛_http_fetch_프리셋_요약은_method와_호스트():
    node = {"id": "f", "event": "ai.agent", "label": "조회", "config": {"tools": [
        {"name": "builtin:http_fetch", "config": {"url": "https://evil.com/x?k=1", "method": "POST"}}]}}
    summary = target_summary(node, {})
    assert "POST evil.com" in summary and "/x?k=1" not in summary


def test_ai_agent_요약은_도구_인자를_보이고_credentialId는_뺀다():
    node = {"id": "g", "event": "ai.agent", "label": "알림", "config": {"tools": [
        {"name": "builtin:slack_send", "config": {"channel": "C1", "text": "hi", "credentialId": "secret-cred"}}]}}
    summary = target_summary(node, {})
    assert "slack_send(channel=C1" in summary and "secret-cred" not in summary
    assert "slack_send(channel=C1" in finish_summary([node], {})


def test_드롭다운_값이_dict여도_예외_없이_요약():
    node = {"id": "s", "event": "slack_bot.send_channel_message", "label": "알림", "config": {"channel": {"id": "C1"}}}
    assert "C1" in target_summary(node, {})


def test_label은_코드가_만든_대상_뒤에_따옴표로():
    node = {"id": "s", "event": "slack_bot.send_channel_message", "label": "채널: #팀공지", "config": {"channel": "C1"}}
    summary = target_summary(node, {"s|config.channel": {"C1": "#general"}})
    assert summary == 'Slack 채널에 메시지 보내기 · 채널: #general · 이름 "채널: #팀공지"'


@pytest.mark.parametrize("event,config,expected", [
    ("schedule.hourly", {}, "매시간"),
    ("schedule.hourly", {"weekends": False}, "매시간(주말 제외)"),
    ("schedule.daily", {"hour": "9"}, "매일 오전 9시"),
    ("schedule.daily", {"hour": "0", "weekends": False}, "매일 자정(주말 제외)"),
    ("schedule.weekly", {"weekday": "FRI", "hour": "17"}, "매주 금요일 오후 5시"),
    ("schedule.monthly", {"day": "L", "hour": "12"}, "매월 마지막 날 정오"),
    ("schedule.monthly", {"day": "15", "hour": "9"}, "매월 15일 오전 9시"),
    ("schedule.weekly", {"weekday": "SAT", "hour": "9", "weekends": False}, "매주 토요일 오전 9시"),
    ("schedule.every", {"cron": "0 9 * * *"}, None),
])
def test_스케줄_문장(event, config, expected):
    assert schedule_text({"id": "t", "event": event, "config": config}) == expected


def test_완료_요약은_새_스케줄을_문장으로_cron_없이():
    node = {"id": "t", "event": "schedule.weekly", "config": {"weekday": "FRI", "hour": "17"}}
    text = finish_summary([node], {})
    assert text.startswith("켜지는 트리거: 매주 금요일 오후 5시") and "cron" not in text

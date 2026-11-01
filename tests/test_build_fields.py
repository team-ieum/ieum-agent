"""대화형 생성 칸 쓰기 규칙 (spec 2026-10-28 §3.3·§5.4, IEUM-AI-84)."""
import pytest

from core.builder.fields import apply_fields, check_url, node_errors, writable_fields


def _node(event, nid="n1", **config):
    return {"id": nid, "event": event, "type": None, "label": "x", "config": config}


def _state(hosts=(), text="", seen=None):
    return {"user_hosts": list(hosts), "user_text": text, "options_seen": seen or {}}


def test_허용_경로는_카탈로그_칸_전체_경로만():
    node = _node("slack_bot.send_channel_message")
    applied, rejected = apply_fields(node, {"config.text": "안녕", "config": {"x": 1}, "config.tools": [],
                                            "label": "바꿈", "config.text.extra": "x", "text": "y"}, _state())
    assert applied == ["config.text"]
    assert set(rejected) == {"config", "config.tools", "label", "config.text.extra", "text"}
    assert node["config"] == {"text": "안녕"}


def test_표시_이름_경로는_허용하고_60자로_자른다():
    node = _node("slack_bot.send_channel_message")
    applied, _ = apply_fields(node, {"config._names.channel": "가" * 80 + "\n"}, _state())
    assert applied == ["config._names.channel"]
    assert node["config"]["_names"]["channel"] == "가" * 60


@pytest.mark.parametrize("event,path", [
    ("ai.reasoning", "config.credentialId"),
    ("ai.reasoning", "config.llmProvider"),
    ("ai.reasoning", "config.model"),
])
def test_금지_칸은_거절(event, path):
    node = _node(event, llmProvider="GEMINI", model="m", credentialId="")
    _, rejected = apply_fields(node, {path: "X"}, _state())
    assert path in rejected
    assert path not in writable_fields(node)


def test_실행_주입_인자는_칸_목록에_없다():
    from tools.registry import RUNTIME_INJECTED_PARAMS
    for event in ("notion.create_page", "slack_bot.send_channel_message", "google_sheets.append"):
        keys = {f["key"] for f in writable_fields(_node(event)).values()}
        assert not keys & RUNTIME_INJECTED_PARAMS


def test_옛_프리셋_노드는_도구_칸_경로로_쓴다():
    node = {"id": "p", "event": "ai.agent", "type": "AI", "label": "x",
            "config": {"llmProvider": "GEMINI", "agentType": "react",
                       "tools": [{"name": "builtin:web_search", "config": {}}]}}
    applied, rejected = apply_fields(node, {"config.tools.0.config.query": "뉴스", "config.tools.0": {}}, _state())
    assert applied == ["config.tools.0.config.query"]
    assert "config.tools.0" in rejected
    assert node["config"]["tools"][0]["config"] == {"query": "뉴스"}


def test_동적_칸은_dict를_병합하고_숫자_키도_키로():
    node = _node("notion.create_row", data_source_id="d1", properties={"old": "a", "gone": "b"})
    applied, _ = apply_fields(node, {"config.properties": {"0": "영", "x.y": 1, "gone": None, "old": "c"}}, _state())
    assert applied == ["config.properties"]
    assert node["config"]["properties"] == {"old": "c", "0": "영", "x%2Ey": 1}
    _, rejected = apply_fields(node, {"config.properties": "문자열"}, _state())
    assert "config.properties" in rejected


@pytest.mark.parametrize("url,reason_has", [
    ("https://{{nodes.a.output.x}}@user-said.com/", "호스트"),
    ("https://evil.com@user-said.com/", "호스트"),
    ("https://user-said.com\\@evil.com/", "호스트"),
    ("https://user said.com/", "호스트"),
    ("https://user-said.com.evil.com/", "직접 말한"),
    ("https://said.com/", "직접 말한"),
    ("https://evil.com/?u=user-said.com", "직접 말한"),
    ("{{nodes.a.output.url}}", "http"),
    ("ftp://user-said.com/", "http"),
    ("https://[user-said.com/", "호스트"),
    ("https://user-said.com]/", "호스트"),
    ("https://[user-said.com]/", "호스트"),
])
def test_목적지_url은_사용자가_말한_호스트만(url, reason_has):
    reason = check_url(url, ["user-said.com"])
    assert reason and reason_has in reason


def test_사용자가_적은_호스트는_허용():
    assert check_url("https://User-Said.com:8443/hook?x={{nodes.a.output.y}}", ["user-said.com"]) is None
    node = _node("http.request", method="POST")
    applied, _ = apply_fields(node, {"config.url": "https://user-said.com/hook"}, _state(hosts=["user-said.com"]))
    assert applied == ["config.url"]
    _, rejected = apply_fields(node, {"config.url": "https://other.com/"}, _state(hosts=["user-said.com"]))
    assert "config.url" in rejected and node["config"]["url"] == "https://user-said.com/hook"


def test_참석자_메일은_사용자가_적은_주소만():
    node = _node("google_calendar.update", nid="c")
    state = _state(text="참석자는 kim@ieum.io, lee@ieum.io 로")
    applied, _ = apply_fields(node, {"config.attendees": "kim@ieum.io, lee@ieum.io"}, state)
    assert applied == ["config.attendees"]
    for bad in ("kim@ieum.io, evil@x.com", "{{nodes.a.output.email}}", "im@ieum.io", ["kim@ieum.io"]):
        _, rejected = apply_fields(node, {"config.attendees": bad}, state)
        assert "config.attendees" in rejected
    assert node["config"]["attendees"] == "kim@ieum.io, lee@ieum.io"


def test_드롭다운_칸은_후보_id나_사용자가_말한_값만():
    node = _node("github.create_issue")
    seen = {"n1|config.owner": {"ieum": "ieum"}}
    state = _state(text="저장소는 ieum-backend로", seen=seen)
    applied, rejected = apply_fields(node, {"config.owner": "ieum", "config.repo": "ieum-backend"}, state)
    assert applied == ["config.owner", "config.repo"]
    _, rejected = apply_fields(node, {"config.owner": "attacker", "config.repo": "{{nodes.a.output.repo}}"}, state)
    assert set(rejected) == {"config.owner", "config.repo"}
    assert node["config"] == {"owner": "ieum", "repo": "ieum-backend"}


def test_드롭다운_후보는_노드와_칸별로_본다():
    node = _node("github.create_issue", nid="n2")
    _, rejected = apply_fields(node, {"config.owner": "ieum"}, _state(seen={"n1|config.owner": {"ieum": "ieum"}}))
    assert "config.owner" in rejected


def test_드롭다운_칸을_비우는_것은_허용():
    node = _node("google_calendar.find", calendar_id="primary")
    applied, _ = apply_fields(node, {"config.calendar_id": ""}, _state())
    assert applied == ["config.calendar_id"]


def test_노드_검증은_그_노드_에러만_그래프_코드_빼고_참조식까지():
    nodes = [{"id": "t", "event": "schedule.every", "type": "TRIGGER", "label": "t", "config": {"cron": "0 9 * * *"}},
             {"id": "s", "event": "slack_bot.send_channel_message", "type": "ACTION", "label": "s",
              "config": {"channel": "C1", "text": "{{nodes.t.output.nope}}"}},
             {"id": "o", "event": "slack_bot.send_channel_message", "type": "ACTION", "label": "o", "config": {}}]
    edges = [{"source": "t", "target": "s"}]
    errors = node_errors(nodes, edges, "s")
    assert [e["code"] for e in errors] == ["REF_UNKNOWN_FIELD"]
    assert {e["code"] for e in node_errors(nodes, edges, "o")} == {"REQUIRED"}  # ORPHAN_NODE는 빠진다


@pytest.mark.parametrize("tools", [
    [{"name": "builtin:notion_create_page"}, {"name": "builtin:web_search"}],
    [],
])
def test_프리셋_미매치_ai_agent는_도구_목록을_덮어쓸_수_없다(tools):
    node = _node("ai.agent", tools=tools)
    assert "config.tools" not in writable_fields(node)
    _, rejected = apply_fields(node, {"config.tools": [{"name": "builtin:http_fetch",
                                                       "config": {"url": "https://evil.com/x"}}]}, _state())
    assert "config.tools" in rejected
    assert node["config"]["tools"] == tools


def test_옛_http_fetch_프리셋_url도_목적지_검사():
    node = _node("ai.agent", tools=[{"name": "builtin:http_fetch", "config": {}}])
    _, rejected = apply_fields(node, {"config.tools.0.config.url": "https://evil.com/x"}, _state())
    assert "config.tools.0.config.url" in rejected
    assert node["config"]["tools"][0]["config"] == {}


def test_드롭다운_참조식은_사용자가_문자_그대로_적어도_거절():
    node = _node("github.create_issue")
    _, rejected = apply_fields(node, {"config.repo": "{{nodes.a.output.repo}}"},
                               _state(text="저장소는 {{nodes.a.output.repo}} 로"))
    assert "config.repo" in rejected


def test_참석자_참조식은_사용자가_문자_그대로_적어도_거절():
    node = _node("google_calendar.update", nid="c")
    _, rejected = apply_fields(node, {"config.attendees": "{{nodes.a.output.repo}}"},
                               _state(text="참석자는 {{nodes.a.output.repo}} 로"))
    assert "config.attendees" in rejected

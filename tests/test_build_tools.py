"""대화형 생성 도구 — 메뉴·연결·노드·엣지 (spec 2026-10-28 §3.2·§3.3·§3.4, IEUM-AI-84). BE는 Backend를 목으로 바꾼다."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.builder import tools
from core.builder.tools import Turn


def _backend(connections=(), webhooks=()):
    be = MagicMock()
    be.connections = AsyncMock(return_value={"data": [{"provider": p} for p in connections]})
    be.options = AsyncMock(return_value={"data": {"items": [{"id": w, "name": w} for w in webhooks], "nextCursor": None}})
    return be


def _turn(nodes=None, edges=None, **backend):
    return Turn(workflow_id="wf", provider="gemini", backend=_backend(**backend),
                nodes=nodes if nodes is not None else [], edges=edges if edges is not None else [])


def _trigger():
    return {"id": "t", "event": "manual.run", "type": "TRIGGER", "label": "시작", "config": {}}


def test_메뉴는_빌더_목록에서_ai_agent_빼고_프리셋_없음():
    events = {e["event"] for e in tools.find_events("")["events"]}
    assert "ai.agent" not in events and "gmail.send_email" not in events
    assert {"ai.reasoning", "slack_bot.send_channel_message", "gmail.new_email", "http.request"} <= events
    assert not any(e.startswith("ai.") and e != "ai.reasoning" for e in events)


def test_메뉴_검색은_단어가_맞는_항목만_없으면_전체():
    hits = tools.find_events("calendar")["events"]
    assert hits and all("calendar" in h["event"] for h in hits)
    assert len(tools.find_events("전혀없는말")["events"]) == len(tools.find_events("")["events"])


def test_개요_저장():
    assert "error" in tools.set_outline({}, "트리거 → 보내기", "이름")
    state = {}
    tools.set_outline(state, ["트리거", "AI 요약", "x" * 100], "일정 요약 " * 20)
    assert state["outline"]["steps"][2] == "x" * 80
    assert len(state["outline"]["workflowName"]) == 60


@pytest.mark.asyncio
async def test_연결_판정_OAuth는_connections_provider():
    turn = _turn(connections=["GOOGLE"])
    assert (await tools.check_connection(turn, "google_calendar"))["connected"] is True
    result = await tools.check_connection(turn, "slack_bot")
    assert result["connected"] is False and "Slack" in result["message"] and "연결했어" in result["message"]
    assert (await tools.check_connection(turn, "manual"))["connected"] is True
    turn.backend.connections.assert_awaited()


@pytest.mark.asyncio
async def test_연결_판정_웹훅_앱은_웹훅_후보():
    turn = _turn(webhooks=["w1"])
    assert (await tools.check_connection(turn, "discord"))["connected"] is True
    turn.backend.options.assert_awaited_with("discord.webhooks", {})
    empty = _turn()
    result = await tools.check_connection(empty, "slack")
    assert result["connected"] is False and "웹훅" in result["message"]


@pytest.mark.asyncio
async def test_연결_판정_BE_오류는_미연결과_메시지():
    turn = _turn()
    turn.backend.connections = AsyncMock(return_value={"error": {"code": "BACKEND_UNAVAILABLE", "message": "조회 불가"}})
    assert await tools.check_connection(turn, "notion") == {"connected": False, "message": "조회 불가"}


@pytest.mark.asyncio
async def test_미연결_앱은_노드를_만들지_않는다():
    turn = _turn(nodes=[_trigger()])
    result = await tools.add_node(turn, "slack_bot.send_channel_message", "알림", after="t")
    assert "error" in result and "연결했어" in result["message"]
    assert len(turn.nodes) == 1 and turn.edges == []


@pytest.mark.asyncio
async def test_노드는_UUID_id_고정값_엣지와_함께():
    turn = _turn(nodes=[_trigger()], connections=["SLACK"])
    result = await tools.add_node(turn, "slack_bot.send_channel_message", "알림\n" + "가" * 80, after="t")
    node = turn.node(result["node_id"])
    assert len(result["node_id"]) == 36
    assert node == {"id": result["node_id"], "event": "slack_bot.send_channel_message", "type": "ACTION",
                    "label": "알림 " + "가" * 57, "config": {}}
    assert "position" not in node and turn.created == [result["node_id"]]
    assert turn.edges == [{"source": "t", "target": result["node_id"]}]


@pytest.mark.asyncio
async def test_AI_노드_초기값():
    turn = _turn(nodes=[_trigger()])
    node = turn.node((await tools.add_node(turn, "ai.reasoning", "요약", after="t"))["node_id"])
    cfg = node["config"]
    assert cfg["llmProvider"] == "GEMINI" and cfg["model"]
    assert cfg["agentType"] == "simple" and cfg["tools"] == [] and cfg["credentialId"] == ""
    assert cfg["brand"]


@pytest.mark.asyncio
async def test_메뉴_밖_event는_거절():
    turn = _turn()
    for event in ("ai.agent", "gmail.send_email", "ai.slack_send", "nope.x"):
        assert "error" in await tools.add_node(turn, event, "x")
    assert turn.nodes == []


@pytest.mark.asyncio
async def test_분기_노드_뒤는_branch_필수_그_밖은_금지():
    branch = {"id": "b", "event": "branch.if", "type": "CONDITION", "label": "분기", "config": {}}
    turn = _turn(nodes=[_trigger(), branch])
    assert "error" in await tools.add_node(turn, "transform.map", "x", after="b")
    assert "error" in await tools.add_node(turn, "transform.map", "x", after="b", branch="yes")
    assert "error" in await tools.add_node(turn, "transform.map", "x", after="t", branch="true")
    ok = await tools.add_node(turn, "transform.map", "참일 때", after="b", branch="true")
    assert turn.edges[-1] == {"source": "b", "target": ok["node_id"], "conditionType": "true"}
    assert "error" in tools.connect(turn, "b", ok["node_id"])
    assert "error" in tools.connect(turn, "t", ok["node_id"], branch="false")
    assert tools.connect(turn, "b", ok["node_id"], branch="false") == {"ok": True}


def test_연결_규칙():
    a = {"id": "a", "event": "transform.map", "type": "TRANSFORM", "label": "a", "config": {}}
    turn = _turn(nodes=[_trigger(), a])
    assert "error" in tools.connect(turn, "a", "t")
    assert "error" in tools.connect(turn, "a", "a")
    assert "error" in tools.connect(turn, "a", "nope")
    assert tools.connect(turn, "t", "a") == {"ok": True}
    assert tools.connect(turn, "t", "a") == {"ok": True}
    assert turn.edges == [{"source": "t", "target": "a"}]


def test_삭제는_엣지도_지우고_마지막_노드는_거절():
    a = {"id": "a", "event": "transform.map", "type": "TRANSFORM", "label": "a", "config": {}}
    turn = _turn(nodes=[_trigger(), a], edges=[{"source": "t", "target": "a"}])
    assert tools.remove_node(turn, "a") == {"ok": True}
    assert turn.edges == [] and [n["id"] for n in turn.nodes] == ["t"]
    assert "error" in tools.remove_node(turn, "t")
    assert "error" in tools.remove_node(turn, "nope")
    assert len(turn.nodes) == 1

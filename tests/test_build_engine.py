"""대화형 생성 엔진 한 턴 — 실제 ADK Runner + 각본 LLM(목) + InMemory 세션 (spec 2026-10-28 §2·§3.5·§4·§5, IEUM-AI-84).
BE는 Backend 목, LLM은 ScriptLlm — 네트워크를 쓰지 않는다."""
import asyncio
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.adk.events import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions import InMemorySessionService
from google.genai import types

from core.builder import engine
from core.builder.state import node_hash

SECRET = "IGNORE-PREVIOUS-샘플값"


class ScriptLlm(BaseLlm):
    """각본대로 함수 호출·텍스트를 내는 LLM. 각본이 끝나면 마지막 단계를 반복한다. delay는 두 번째 호출부터 건다."""
    script: list = []
    calls: int = 0
    requests: list = []
    delay: float = 0.0

    async def generate_content_async(self, llm_request, stream=False) -> AsyncGenerator[LlmResponse, None]:
        self.requests.append(str(llm_request.contents) + str(llm_request.config.system_instruction))
        step = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(step, Exception):  # LLM 오류 흉내
            raise step
        if self.delay and self.calls > 1:
            await asyncio.sleep(self.delay)
        calls = step if isinstance(step, list) else [step]  # 목록이면 한 응답에 함수 호출 여러 개(병렬 호출)
        parts = [types.Part(text=c) if isinstance(c, str) else
                 types.Part(function_call=types.FunctionCall(name=c[0], args=c[1])) for c in calls]
        yield LlmResponse(content=types.Content(role="model", parts=parts))


TRIGGER = {"id": "t", "event": "schedule.every", "type": "TRIGGER", "label": "매일",
           "config": {"cron": "0 9 * * *"}, "position": {"x": 1.5, "y": 2}}
SLACK = {"id": "s", "event": "slack_bot.send_channel_message", "type": "ACTION", "label": "알림",
         "config": {"channel": "C1", "text": "{{nodes.t.output.triggeredAt}}"}, "position": {"x": 3, "y": 4}}
EDGE = {"id": "e1", "source": "t", "target": "s"}
HOOK = {"id": "w", "event": "webhook.catch", "type": "TRIGGER", "label": "수신", "config": {}}


@pytest.fixture
def env():
    svc = InMemorySessionService()
    be = MagicMock()
    be.connections = AsyncMock(return_value={"data": [{"provider": "SLACK"}]})
    be.options = AsyncMock(return_value={"data": {"items": [{"id": "C1", "name": "#general"}], "nextCursor": None}})
    be.test_node = AsyncMock(return_value={"data": {"status": "SUCCESS", "output": {"ts": SECRET, "ok": True}}})
    llm = ScriptLlm(model="fake")
    with patch.object(engine, "_session_service", return_value=svc), \
            patch.object(engine, "Backend", return_value=be), \
            patch.object(engine, "build_model_param", return_value=llm):
        yield svc, be, llm


async def _turn(prompt, nodes=(), edges=(), script=None, llm=None):
    if script is not None:
        llm.script, llm.calls = script, 0
    return await engine.build_turn(prompt=prompt, provider="GEMINI", api_key="k", user_id="u", workflow_id="wf",
                                   current_nodes=[dict(n) for n in nodes], current_edges=[dict(e) for e in edges],
                                   delegation_token="tok")


async def _state(svc):
    return (await svc.get_session(app_name="ieum-agent", user_id="u", session_id="build-u-wf")).state


def test_도구는_12개이고_토큰_인자가_없다():
    from google.adk.tools.function_tool import FunctionTool
    from core.builder.tools import Turn, make_tools
    turn = Turn(workflow_id="wf", provider="GEMINI", backend=MagicMock(), nodes=[], edges=[])
    decls = {d.name: d for d in (FunctionTool(fn)._get_declaration() for fn in make_tools(turn))}
    assert set(decls) == {"find_events", "set_outline", "check_connection", "add_node", "get_fields", "list_options",
                          "load_dynamic_fields", "set_fields", "connect", "remove_node", "test_node", "finish"}
    params = {name: set((d.parameters_json_schema or {}).get("properties") or {}) for name, d in decls.items()}
    assert params["set_fields"] == {"node_id", "values"} and params["finish"] == set()
    assert params["add_node"] == {"event", "label", "after", "branch"}
    assert not set().union(*params.values()) & {"tool_context", "token", "delegation_token", "turn", "state"}


@pytest.mark.asyncio
async def test_개요만_말한_턴은_CLARIFICATION_노드_비움(env):
    svc, be, llm = env
    resp = await _turn("매일 9시 https://Hook.Example.com/x 로 보내줘", [], [], llm=llm, script=[
        ("set_outline", {"steps": ["트리거", "보내기"], "workflowName": "아침 알림"}),
        "트리거: 매일 9시 → 보내기, 2개로 만들게요.\n괜찮을까요?"])
    assert resp.type.value == "CLARIFICATION_NEEDED" and resp.nodes is None and resp.edges is None
    assert resp.buildState == "BUILDING" and resp.message == "트리거: 매일 9시 → 보내기, 2개로 만들게요. 괜찮을까요?"
    state = await _state(svc)
    assert state["outline"]["workflowName"] == "아침 알림" and state["baseline"] == {}
    assert state["user_hosts"] == ["hook.example.com"]


@pytest.mark.asyncio
async def test_노드가_생긴_턴은_전체_노드와_이름_위치_보존(env):
    svc, be, llm = env
    await _turn("만들어줘", llm=llm, script=[("set_outline", {"steps": ["a"], "workflowName": "이름"}), "개요"])
    resp = await _turn("좋아", [], [], llm=llm, script=[
        ("add_node", {"event": "manual.run", "label": "시작"}), "트리거를 만들었어요."])
    assert resp.type.value == "WORKFLOW_MODIFIED" and resp.workflowName == "이름"
    assert [n["event"] for n in resp.nodes] == ["manual.run"] and "position" not in resp.nodes[0]
    resp = await _turn("슬랙도", [TRIGGER, SLACK], [EDGE], llm=llm, script=[
        ("add_node", {"event": "transform.map", "label": "변환", "after": "s"}), "변환을 붙였어요."])
    assert resp.workflowName is None
    assert resp.nodes[0]["position"] == {"x": 1.5, "y": 2} and resp.edges[0] == EDGE
    assert resp.edges[1]["source"] == "s" and len(resp.edges) == 2


@pytest.mark.asyncio
async def test_쓰기_노드_확인_흐름과_샘플은_LLM에_안_간다(env):
    svc, be, llm = env
    resp = await _turn("테스트해 봐", [TRIGGER, SLACK], [EDGE], llm=llm,
                       script=[("test_node", {"node_id": "s"}), "확인해 주세요"])
    be.test_node.assert_not_awaited()
    assert resp.type.value == "CLARIFICATION_NEEDED"
    assert resp.message.endswith("· 채널: (목록에서 확인 안 됨, id=C1). 테스트할까요? (네 / 건너뛰기)")
    assert (await _state(svc))["pending"]["node_id"] == "s"
    resp = await _turn("네", [TRIGGER, SLACK], [EDGE], llm=llm,
                       script=[("test_node", {"node_id": "s"}), ("test_node", {"node_id": "s"}), "테스트했어요"])
    be.test_node.assert_awaited_once()
    assert SECRET in resp.message and "테스트 성공" in resp.message
    assert not any(SECRET in r for r in llm.requests)
    state = await _state(svc)
    assert state["tests"]["s"] == {"status": "success", "hash": node_hash(SLACK), "fields": ["ts", "ok"]}
    assert state["confirmed"] is None and state["pending"] is None  # 같은 턴 두 번째 호출은 already_tested — 새 확인 없음
    assert "테스트할까요" not in resp.message


@pytest.mark.asyncio
async def test_같은_확인으로_병렬_두_번_불러도_한_번만_실행(env):
    svc, be, llm = env
    await _turn("테스트", [TRIGGER, SLACK], [EDGE], llm=llm, script=[("test_node", {"node_id": "s"}), "확인"])
    await _turn("네", [TRIGGER, SLACK], [EDGE], llm=llm,
                script=[[("test_node", {"node_id": "s"}), ("test_node", {"node_id": "s"})], "했어요"])
    be.test_node.assert_awaited_once()


@pytest.mark.asyncio
async def test_확인_뒤_노드가_바뀌면_네여도_실행하지_않는다(env):
    svc, be, llm = env
    await _turn("테스트", [TRIGGER, SLACK], [EDGE], llm=llm, script=[("test_node", {"node_id": "s"}), "확인"])
    changed = {**SLACK, "config": {**SLACK["config"], "channel": "C9"}}  # 사용자가 팔레트로 고쳐 저장
    resp = await _turn("네", [TRIGGER, changed], [EDGE], llm=llm, script=[("test_node", {"node_id": "s"}), "다시 확인"])
    be.test_node.assert_not_awaited()
    assert "id=C9" in resp.message and resp.message.endswith("테스트할까요? (네 / 건너뛰기)")


@pytest.mark.asyncio
async def test_BE가_brand를_넣어_저장해도_확인은_한_번에(env):
    svc, be, llm = env
    await _turn("테스트", [TRIGGER, SLACK], [EDGE], llm=llm, script=[("test_node", {"node_id": "s"}), "확인"])
    saved = {**SLACK, "config": {**SLACK["config"], "brand": "slack"}}  # BE NodeDerivation.derive가 저장 때 넣는다
    resp = await _turn("네", [TRIGGER, saved], [EDGE], llm=llm, script=[("test_node", {"node_id": "s"}), "했어요"])
    be.test_node.assert_awaited_once()
    assert "테스트할까요" not in resp.message


@pytest.mark.asyncio
async def test_건너뛰기만_묻는_확인에_네는_건너뛰기(env):
    svc, be, llm = env
    fixed = {**SLACK, "config": {"channel": "C1", "text": "고정 문구"}}  # 웹훅 값을 참조하지 않아도 상류가 skipped다
    nodes, edges = [HOOK, fixed], [{"source": "w", "target": "s"}]
    resp = await _turn("테스트", nodes, edges, llm=llm,
                       script=[("test_node", {"node_id": "w"}), ("test_node", {"node_id": "s"}), "확인"])
    assert resp.message.endswith("건너뛸까요? (건너뛰기)")
    await _turn("네", nodes, edges, llm=llm, script=[("test_node", {"node_id": "s"}), "넘어갈게요"])
    be.test_node.assert_not_awaited()
    assert (await _state(svc))["tests"]["s"] == {"status": "skipped", "hash": node_hash(fixed)}


@pytest.mark.asyncio
async def test_턴이_오류로_끝나면_확인_대기가_남지_않는다(env):
    svc, be, llm = env
    with pytest.raises(RuntimeError):
        await _turn("테스트", [TRIGGER, SLACK], [EDGE], llm=llm,
                    script=[("test_node", {"node_id": "s"}), RuntimeError("LLM 오류")])
    assert (await _state(svc))["pending"] is None  # 사용자는 확인 질문을 못 봤다
    await _turn("네", [TRIGGER, SLACK], [EDGE], llm=llm, script=[("test_node", {"node_id": "s"}), "확인"])
    be.test_node.assert_not_awaited()


@pytest.mark.asyncio
async def test_이름과_개요에_중괄호가_있어도_턴이_돈다(env):
    svc, be, llm = env
    await svc.create_session(app_name="ieum-agent", user_id="u", session_id="build-u-wf",
                             state={"outline": {"steps": ["{date} 일정 조회"], "workflowName": "w"}})
    braced = {**TRIGGER, "label": "{날짜} 알림"}
    resp = await _turn("안녕", [braced], [], llm=llm, script=["안녕하세요"])
    assert resp.message == "안녕하세요"
    assert any("{날짜} 알림" in r and "{date} 일정 조회" in r for r in llm.requests)


@pytest.mark.asyncio
async def test_확인은_바로_다음_턴만_그_밖_입력은_폐기(env):
    svc, be, llm = env
    await _turn("테스트", [TRIGGER, SLACK], [EDGE], llm=llm, script=[("test_node", {"node_id": "s"}), "확인"])
    await _turn("근데 채널 이름이 뭐였지", [TRIGGER, SLACK], [EDGE], llm=llm, script=["general이에요"])
    assert (await _state(svc))["pending"] is None
    assert any("확인 질문은 취소됐어요" in r for r in llm.requests)
    await _turn("네", [TRIGGER, SLACK], [EDGE], llm=llm, script=[("test_node", {"node_id": "s"}), "다시 확인"])
    be.test_node.assert_not_awaited()


@pytest.mark.asyncio
async def test_건너뛰기는_skipped로_기록(env):
    svc, be, llm = env
    await _turn("테스트", [TRIGGER, SLACK], [EDGE], llm=llm, script=[("test_node", {"node_id": "s"}), "확인"])
    await _turn("건너뛰기", [TRIGGER, SLACK], [EDGE], llm=llm, script=["넘어갈게요"])
    assert (await _state(svc))["tests"]["s"] == {"status": "skipped", "hash": node_hash(SLACK)}
    be.test_node.assert_not_awaited()


@pytest.mark.asyncio
async def test_LLM_말이_비어도_확인_질문은_가고_아무것도_없으면_기본_안내(env):
    svc, be, llm = env
    resp = await _turn("테스트", [TRIGGER, SLACK], [EDGE], llm=llm, script=[("test_node", {"node_id": "s"}), ""])
    assert resp.message.startswith("테스트 전 확인 — ")
    resp = await _turn("음", [TRIGGER, SLACK], [EDGE], llm=llm, script=[""])
    assert resp.message == engine.EMPTY_MESSAGE


@pytest.mark.asyncio
async def test_완료_확인_턴은_LLM_없이_DONE과_전체_노드(env):
    svc, be, llm = env
    await _turn("시작", [TRIGGER, SLACK], [EDGE], llm=llm, script=["안내"])  # baseline = 두 노드 → 통과
    resp = await _turn("끝내자", [TRIGGER, SLACK], [EDGE], llm=llm, script=[("finish", {}), "확인해 주세요"])
    assert resp.type.value == "CLARIFICATION_NEEDED" and resp.message.endswith("완료할까요? (네 / 아니요)")
    calls = llm.calls
    resp = await _turn("네", [TRIGGER, SLACK], [EDGE], llm=None)
    assert llm.calls == calls
    assert resp.buildState == "DONE" and resp.type.value == "WORKFLOW_MODIFIED"
    assert resp.nodes == [TRIGGER, SLACK] and resp.edges == [EDGE]
    assert resp.model_dump(mode="json")["buildState"] == "DONE"


@pytest.mark.asyncio
async def test_완료_확인_뒤_노드가_바뀌면_DONE이_아니다(env):
    svc, be, llm = env
    await _turn("시작", [TRIGGER, SLACK], [EDGE], llm=llm, script=["안내"])
    await _turn("끝내자", [TRIGGER, SLACK], [EDGE], llm=llm, script=[("finish", {}), "확인"])
    changed = {**SLACK, "config": {**SLACK["config"], "channel": "C2"}}
    resp = await _turn("네", [TRIGGER, changed], [EDGE], llm=llm, script=["다시 볼게요"])
    assert resp.buildState == "BUILDING"
    assert any("finish를 다시 부르세요" in r for r in llm.requests)


@pytest.mark.asyncio
async def test_LLM_호출_상한이면_바뀐_노드를_BUILDING으로(env):
    svc, be, llm = env
    resp = await _turn("만들어", [TRIGGER], [], llm=llm, script=[
        ("add_node", {"event": "transform.map", "label": "변환", "after": "t"}), ("find_events", {"query": ""})])
    assert llm.calls == engine.MAX_LLM_CALLS == 20
    assert resp.type.value == "WORKFLOW_MODIFIED" and resp.buildState == "BUILDING"
    assert resp.message.startswith(engine.CUT_SHORT_MESSAGE) and len(resp.nodes) == 2


@pytest.mark.asyncio
async def test_시간_상한이면_진행_상황으로_끝낸다(env, monkeypatch):
    svc, be, llm = env
    monkeypatch.setattr(engine, "TURN_TIMEOUT_SECONDS", 1)
    llm.delay = 5  # 첫 호출(add_node)은 바로, 두 번째 호출부터 늦다
    resp = await _turn("만들어", [TRIGGER], [], llm=llm, script=[
        ("add_node", {"event": "transform.map", "label": "변환", "after": "t"}), ("find_events", {"query": ""})])
    assert resp.message.startswith(engine.CUT_SHORT_MESSAGE) and resp.type.value == "WORKFLOW_MODIFIED"


@pytest.mark.asyncio
async def test_기존_워크플로우_첫_변경_턴에만_자동_실행_멈춤_안내(env):
    svc, be, llm = env
    step = [("add_node", {"event": "transform.map", "label": "변환", "after": "s"}), "붙였어요"]
    resp = await _turn("변환 추가", [TRIGGER, SLACK], [EDGE], llm=llm, script=step)
    assert engine.PAUSED_NOTICE in resp.message
    resp = await _turn("하나 더", [TRIGGER, SLACK], [EDGE], llm=llm, script=step)
    assert engine.PAUSED_NOTICE not in resp.message


@pytest.mark.asyncio
async def test_새_워크플로우는_멈춤_안내_없음(env):
    svc, be, llm = env
    resp = await _turn("만들어", [], [], llm=llm, script=[("add_node", {"event": "manual.run", "label": "시작"}), "됐어요"])
    assert engine.PAUSED_NOTICE not in resp.message


@pytest.mark.asyncio
async def test_새로_만들어_완료한_뒤_고치면_멈춤_안내(env):
    svc, be, llm = env
    await svc.create_session(app_name="ieum-agent", user_id="u", session_id="build-u-wf", state={
        "baseline": {}, "tests": {"t": {"status": "success", "hash": node_hash(TRIGGER)},
                                  "s": {"status": "skipped", "hash": node_hash(SLACK)}}})  # 채팅으로 새로 만든 워크플로우
    await _turn("끝내자", [TRIGGER, SLACK], [EDGE], llm=llm, script=[("finish", {}), "확인"])
    assert (await _turn("네", [TRIGGER, SLACK], [EDGE], llm=llm)).buildState == "DONE"
    resp = await _turn("변환 추가", [TRIGGER, SLACK], [EDGE], llm=llm,
                       script=[("add_node", {"event": "transform.map", "label": "변환", "after": "s"}), "붙였어요"])
    assert engine.PAUSED_NOTICE in resp.message


@pytest.mark.asyncio
async def test_세션_events가_300을_넘으면_state만_옮긴다(env):
    svc, be, llm = env
    session = await svc.create_session(app_name="ieum-agent", user_id="u", session_id="build-u-wf",
                                       state={"baseline": {"t": "h"}, "outline": {"steps": ["x"], "workflowName": "w"}})
    for _ in range(301):
        await svc.append_event(session, Event(author="user"))
    await _turn("안녕", [TRIGGER], [], llm=llm, script=["안녕하세요"])
    session = await svc.get_session(app_name="ieum-agent", user_id="u", session_id="build-u-wf")
    assert len(session.events) < 10 and session.state["outline"]["workflowName"] == "w"


@pytest.mark.asyncio
async def test_옛_Designer_세션과_섞이지_않는다(env):
    svc, be, llm = env
    old = await svc.create_session(app_name="ieum-agent", user_id="u", session_id="u-wf",
                                   state={"pending": {"node_id": "s"}})
    await svc.append_event(old, Event(author="user", content=types.Content(role="user", parts=[types.Part(text="옛대화")])))
    await _turn("네", [TRIGGER, SLACK], [EDGE], llm=llm, script=["안녕하세요"])
    assert not any("test_node('s')" in r for r in llm.requests)
    new = await svc.get_session(app_name="ieum-agent", user_id="u", session_id="build-u-wf")
    assert "옛대화" not in str([e.content for e in new.events]) and new.state["pending"] is None


@pytest.mark.asyncio
async def test_스트림은_stage_하나_뒤_done_워크플로우_없으면_error(env):
    svc, be, llm = env
    llm.script, llm.calls = ["안녕하세요"], 0
    events = [e async for e in engine.build_turn_stream(
        prompt="안녕", provider="GEMINI", api_key="k", user_id="u", workflow_id="wf",
        current_nodes=[], current_edges=[], delegation_token=None)]
    assert events[0] == ("stage", {"stage": "designing"}) and events[1][0] == "done" and len(events) == 2
    events = [e async for e in engine.build_turn_stream(
        prompt="안녕", provider="GEMINI", api_key="k", user_id="u", workflow_id=None,
        current_nodes=[], current_edges=[], delegation_token=None)]
    assert events[-1] == ("error", {"message": "워크플로우를 먼저 저장한 뒤 채팅해 주세요."})


@pytest.mark.asyncio
async def test_없는_도구_이름을_불러도_턴이_끝까지_간다(env):
    svc, be, llm = env
    resp = await _turn("만들어", [TRIGGER], [], llm=llm, script=[
        ("add_node", {"event": "transform.map", "label": "변환", "after": "t"}), ("no_such_tool", {}), "붙였어요"])
    assert resp.type.value == "WORKFLOW_MODIFIED" and len(resp.nodes) == 2 and resp.message.startswith("붙였어요")
    assert any("no_such_tool" in r and "error" in r for r in llm.requests[2:])


@pytest.mark.asyncio
async def test_도구가_예외를_던져도_바뀐_노드는_남고_턴이_끝난다(env):
    svc, be, llm = env
    with patch("core.builder.tools.find_events", side_effect=AttributeError("boom")):
        resp = await _turn("만들어", [TRIGGER], [], llm=llm, script=[
            ("add_node", {"event": "transform.map", "label": "변환", "after": "t"}), ("find_events", {"query": "x"}),
            "붙였어요"])
    assert resp.type.value == "WORKFLOW_MODIFIED" and len(resp.nodes) == 2 and resp.message.startswith("붙였어요")
    assert any("AttributeError" in r for r in llm.requests[2:])

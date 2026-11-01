"""빌더 AI 노드 모델 카드 (spec 2026-10-30 §5, IEUM-AI-85). BE는 Backend.options 목(공급원별 응답)."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.builder import model_pick as mp
from core.provider_config import resolve_model

CLAUDE, GEMINI = resolve_model("CLAUDE"), resolve_model("GEMINI")
KEYS = {"CLAUDE": [("cred-c1", "업무용"), ("cred-c0", "옛 키")], "GEMINI": [("cred-g1", "개인")]}
MODELS = {"CLAUDE": [("claude-haiku-x", "Claude Haiku"), (CLAUDE, "Claude 기본"), ("claude-opus-x", "Claude Opus"),
                     ("claude-extra", "넷째")],
          "GEMINI": [(GEMINI, "Gemini 기본")]}


def _opts(keys=KEYS, models=MODELS, fail=None):
    async def options(source, params):
        p = params["llmProvider"]
        if fail == source:
            return {"error": {"code": "BACKEND_UNAVAILABLE", "message": "조회 불가", "data": None}}
        rows = (keys if source == "ieum.credentials" else models).get(p, [])
        return {"data": {"items": [{"id": i, "name": n} for i, n in rows], "nextCursor": None}}
    return AsyncMock(side_effect=options)


def _be(**kw):
    be = MagicMock()
    be.options = _opts(**kw)
    return be


def _calls(be):
    return [(c.args[0], c.args[1]["llmProvider"]) for c in be.options.await_args_list]


@pytest.mark.asyncio
async def test_카드_순서_상한_추천():
    choices = (await mp.load_choices(_be(), "gemini"))["choices"]
    assert [(c["provider"], c["model"]) for c in choices] == [
        ("GEMINI", GEMINI), ("CLAUDE", CLAUDE), ("CLAUDE", "claude-haiku-x"), ("CLAUDE", "claude-opus-x")]
    assert [c["n"] for c in choices] == [1, 2, 3, 4]
    assert all(c["credentialId"] == "cred-c1" and c["key"] == "업무용" for c in choices if c["provider"] == "CLAUDE")
    assert choices[0]["credentialId"] == "cred-g1"
    assert [c["recommended"] for c in choices] == [True, False, False, False]
    assert [c["label"] for c in choices] == ["Gemini 기본", "Claude 기본", "Claude Haiku", "Claude Opus"]


@pytest.mark.asyncio
async def test_조회는_키_3번_모델은_키_있는_제공사만():
    be = _be()
    await mp.load_choices(be, "GEMINI")
    calls = _calls(be)
    assert len(calls) == 5
    assert set(calls) == {("ieum.credentials", "CLAUDE"), ("ieum.credentials", "OPENAI"),
                          ("ieum.credentials", "GEMINI"), ("ai.models", "CLAUDE"), ("ai.models", "GEMINI")}


@pytest.mark.asyncio
async def test_채팅_제공사에_키가_없으면_추천_없음():
    choices = (await mp.load_choices(_be(), "OPENAI"))["choices"]
    assert [c["provider"] for c in choices] == ["CLAUDE"] * 3 + ["GEMINI"]
    assert not any(c["recommended"] for c in choices)


@pytest.mark.asyncio
async def test_키가_없으면_빈_후보():
    be = _be(keys={})
    assert await mp.load_choices(be, "GEMINI") == {"choices": []}
    assert all(src == "ieum.credentials" for src, _ in _calls(be))


@pytest.mark.asyncio
async def test_키_조회_오류는_오류_문구():
    assert await mp.load_choices(_be(fail="ieum.credentials"), "GEMINI") == {"error": "조회 불가"}


@pytest.mark.asyncio
async def test_모델_조회_오류면_기본_모델_하나():
    choices = (await mp.load_choices(_be(fail="ai.models"), "CLAUDE"))["choices"]
    assert [(c["provider"], c["model"], c["label"]) for c in choices] == [
        ("CLAUDE", CLAUDE, CLAUDE), ("GEMINI", GEMINI, GEMINI)]


@pytest.mark.asyncio
async def test_이상한_키_항목은_건너뛴다():
    keys = {"CLAUDE": [("", "빈"), ("a\nb", "제어"), ("x" * 201, "김"), ("ok", None)]}
    choices = (await mp.load_choices(_be(keys=keys), "CLAUDE"))["choices"]
    assert choices and all(c["credentialId"] == "ok" and c["key"] == "AI 키" for c in choices)


@pytest.mark.asyncio
async def test_BE_응답_모양이_이상해도_예외_없음():
    be = MagicMock()
    be.options = AsyncMock(return_value={"data": None})
    assert await mp.load_choices(be, "CLAUDE") == {"choices": []}
    be.options = AsyncMock(return_value={"data": {"items": "x"}})
    assert await mp.load_choices(be, "CLAUDE") == {"choices": []}


def _two():
    return mp.build_choices("CLAUDE", {"CLAUDE": {"id": "cred-c1", "name": "업무용"}},
                            {"CLAUDE": [{"id": CLAUDE, "name": "Claude 기본"}, {"id": "h", "name": "Haiku"}]})


def test_옵션과_글자_목록에_키_id가_없다():
    choices = _two()
    assert mp.options_of(choices) == [
        {"value": "1", "label": "Claude 기본", "description": '내 Claude 키 "업무용" · 추천'},
        {"value": "2", "label": "Haiku", "description": '내 Claude 키 "업무용"'}]
    assert mp.ask_text(choices) == (
        'AI 노드에 쓸 모델을 골라 주세요: ① Claude 기본 (내 Claude 키 "업무용", 추천) · ② Haiku (내 Claude 키 "업무용")')
    assert "cred-c1" not in str(mp.options_of(choices)) + mp.ask_text(choices)


@pytest.mark.parametrize("text,n", [("1", 1), ("①", 1), ("2번", 2), (" ② ", 2), ("2.", 2), ("2!", 2), ("Haiku", 2)])
def test_번호_판정(text, n):
    assert mp.match_choice(text, _two())["n"] == n


@pytest.mark.parametrize("text", ["3", "0", "10", "음 글쎄", "네", "1 그리고 2", "haiku", ""])
def test_번호_판정_아님(text):
    assert mp.match_choice(text, _two()) is None


def test_같은_label이_둘이면_이름으로_못_고른다():
    choices = mp.build_choices("CLAUDE", {"CLAUDE": {"id": "k", "name": "n"}},
                               {"CLAUDE": [{"id": CLAUDE, "name": "같음"}, {"id": "h", "name": "같음"}]})
    assert mp.match_choice("같음", choices) is None
    assert mp.match_choice("2", choices)["n"] == 2


from core.builder import engine  # noqa: E402
from core.builder.state import node_hash  # noqa: E402

AI = {"id": "a", "event": "ai.reasoning", "type": "AI", "label": "요약",
      "config": {"agentType": "simple", "credentialId": "", "tools": [], "llmProvider": "GEMINI",
                 "model": GEMINI, "prompt": "요약"}}


def _pick_state(node=AI):
    choices = mp.build_choices("GEMINI", {"GEMINI": {"id": "cred-g1", "name": "개인"},
                                          "CLAUDE": {"id": "cred-c1", "name": "업무용"}},
                               {"CLAUDE": [{"id": "claude-haiku-x", "name": "Claude Haiku"}]})
    return {"pending": {"kind": "model_pick", "node_id": "a", "hash": node_hash(node), "choices": choices}}


def _ai_nodes():
    return [dict(AI, config=dict(AI["config"]))]


def _three(node):
    c = node["config"]
    return c["credentialId"], c["llmProvider"], c["model"]


@pytest.mark.parametrize("prompt", ["3", "③", "3번", "Claude Haiku"])
def test_번호로_고르면_세_칸을_한_번에_쓴다(prompt):
    nodes = _ai_nodes()
    out = engine.judge_turn_start(_pick_state(), prompt, nodes, [])
    assert _three(nodes[0]) == ("cred-c1", "CLAUDE", "claude-haiku-x")
    assert {k: v for k, v in nodes[0]["config"].items() if k not in ("credentialId", "llmProvider", "model")} == \
        {k: v for k, v in AI["config"].items() if k not in ("credentialId", "llmProvider", "model")}
    assert out["delta"]["pending"] is None
    assert "Claude Haiku" in out["hint"] and "업무용" in out["hint"] and "cred-c1" not in out["hint"]
    assert "test_node('a')" in out["hint"]
    assert out["confirmed"] is None and out["done"] is False


def test_label로도_고른다():
    nodes = _ai_nodes()
    out = engine.judge_turn_start(_pick_state(), "Claude Haiku", nodes, [])
    assert _three(nodes[0]) == ("cred-c1", "CLAUDE", "claude-haiku-x")
    assert out["delta"]["pending"] is None


@pytest.mark.parametrize("prompt", ["음 글쎄", "네"])
def test_엉뚱한_답은_대기_폐기_노드_그대로(prompt):
    nodes = _ai_nodes()
    out = engine.judge_turn_start(_pick_state(), prompt, nodes, [])
    assert nodes[0]["config"] == AI["config"]
    assert "취소" in out["hint"] and out["delta"]["pending"] is None
    assert out["confirmed"] is None and out["done"] is False


def test_노드가_바뀌었거나_지워졌으면_넣지_않는다():
    nodes = _ai_nodes()
    nodes[0]["config"]["prompt"] = "바뀐 요약"
    out = engine.judge_turn_start(_pick_state(), "3", nodes, [])
    assert nodes[0]["config"]["credentialId"] == "" and "pick_model" in out["hint"]
    assert out["delta"]["pending"] is None
    out = engine.judge_turn_start(_pick_state(), "3", [], [])
    assert "pick_model" in out["hint"] and out["delta"]["pending"] is None


def test_apply_choice는_config가_없으면_새로_만든다():
    node = {"id": "a", "event": "ai.reasoning", "config": None}
    mp.apply_choice(node, {"credentialId": "c", "provider": "OPENAI", "model": "m", "label": "x"})
    assert node["config"] == {"credentialId": "c", "llmProvider": "OPENAI", "model": "m"}


# ── 카드를 띄우는 자리(add_node·pick_model·test_node·finish) ───────────────────

import time  # noqa: E402

from core.builder import tools  # noqa: E402
from core.builder.state import baseline_of  # noqa: E402
from core.builder.tools import Turn  # noqa: E402

T = {"id": "t", "event": "manual.run", "type": "TRIGGER", "label": "시작", "config": {}}
SLACK = {"id": "s", "event": "slack_bot.send_channel_message", "type": "ACTION", "label": "알림",
         "config": {"channel": "C1", "text": "x"}}


def _turn(nodes, **kw):
    return Turn(workflow_id="wf", provider="GEMINI", backend=_be(**kw), nodes=[dict(n) for n in nodes],
                edges=[{"source": "t", "target": n["id"]} for n in nodes if n["id"] != "t"])


@pytest.mark.asyncio
async def test_키가_없으면_AI_노드를_만들지_않고_안내():
    turn = _turn([T], keys={})
    r = await tools.add_node(turn, "ai.reasoning", "요약", after="t")
    assert "error" in r and r["message"] == mp.NO_KEY_MESSAGE
    assert len(turn.nodes) == 1 and turn.edges == []
    assert turn.pending is None


@pytest.mark.asyncio
async def test_AI_노드는_키_빈_채로_만들고_카드_대기():
    turn = _turn([T])
    r = await tools.add_node(turn, "ai.reasoning", "요약", after="t")
    assert r["status"] == "needs_model"
    node = turn.node(r["node_id"])
    assert node["config"]["credentialId"] == ""
    assert turn.pending["kind"] == "model_pick" and turn.pending["node_id"] == r["node_id"]
    assert turn.pending["hash"] == node_hash(node)
    assert turn.ask.startswith("AI 노드에 쓸 모델을 골라 주세요: ① ")
    assert "cred-" not in str(r)


@pytest.mark.asyncio
async def test_AI_노드_둘이면_조회_한_번_카드_하나():
    turn = _turn([T])
    first = await tools.add_node(turn, "ai.reasoning", "a", after="t")
    second = await tools.add_node(turn, "ai.reasoning", "b", after="t")
    assert second["status"] == "needs_model"
    assert sum(1 for c in turn.backend.options.await_args_list if c.args[0] == "ieum.credentials") == 3
    assert turn.pending["node_id"] == first["node_id"]


@pytest.mark.asyncio
async def test_시간이_70초_안_남으면_카드_조회를_시작하지_않는다():
    turn = _turn([T])
    turn.deadline = time.monotonic() + 50
    r = await tools.add_node(turn, "ai.reasoning", "요약", after="t")
    assert "error" in r and r["message"] == tools.SHORT_ON_TIME
    turn.backend.options.assert_not_awaited()
    assert len(turn.nodes) == 1


@pytest.mark.asyncio
async def test_키_조회_오류는_노드를_만들지_않는다():
    turn = _turn([T], fail="ieum.credentials")
    r = await tools.add_node(turn, "ai.reasoning", "요약", after="t")
    assert "error" in r and r["message"] == "조회 불가"
    assert len(turn.nodes) == 1 and turn.pending is None


@pytest.mark.asyncio
async def test_pick_model은_ai_reasoning에만():
    old = {"id": "g", "event": "ai.agent", "type": "AI", "label": "옛", "config": {"credentialId": "", "tools": []}}
    turn = _turn([T, SLACK, old, AI])
    for node_id in ("s", "g", "nope"):
        assert "error" in await tools.pick_model(turn, node_id)
    assert (await tools.pick_model(turn, "a"))["status"] == "needs_model"


@pytest.mark.asyncio
async def test_키가_빈_AI_노드는_테스트하지_않고_카드():
    turn = _turn([T, AI])
    turn.backend.test_node = AsyncMock()
    assert (await tools.test_node(turn, {}, "a"))["status"] == "needs_model"
    turn.backend.test_node.assert_not_awaited()
    assert turn.pending["kind"] == "model_pick"


@pytest.mark.asyncio
async def test_카드_대기가_있으면_쓰기_확인은_미룬다():
    turn = _turn([T, AI, SLACK])
    await tools.add_node(turn, "ai.reasoning", "요약", after="t")
    assert await tools.test_node(turn, {}, "s") == {"status": "later", "message": tools.MODEL_FIRST}
    assert turn.pending["kind"] == "model_pick"
    assert "테스트할까요" not in turn.ask


@pytest.mark.asyncio
async def test_finish는_키_빈_AI_노드에_카드():
    turn = _turn([T, AI])
    state = {"baseline": baseline_of([T, AI])}
    assert (await tools.finish(turn, state))["status"] == "needs_model"
    assert "완료할까요" not in turn.ask


@pytest.mark.asyncio
async def test_finish는_옛_ai_agent는_건드리지_않는다():
    old = {"id": "g", "event": "ai.agent", "type": "AI", "label": "옛", "config": {"credentialId": "", "tools": []}}
    turn = _turn([T, old])
    out = await tools.finish(turn, {"baseline": baseline_of([T, old])})
    assert out.get("status") != "needs_model"
    turn.backend.options.assert_not_awaited()

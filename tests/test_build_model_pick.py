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

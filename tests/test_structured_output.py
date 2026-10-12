"""run_agent 구조화 출력 통합(spec §3.1·§3.2·§2.6). LLM·Mongo는 전부 mock."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from google.genai import types

from api.schemas.request import AgentNodeRequest
from core.agent import run_agent

FIELDS = [
    {"key": "urgent", "type": "boolean", "description": "긴급하면 true"},
    {"key": "reason", "type": "text"},
    {"key": "count", "type": "number"},
]
GOOD = '{"urgent": true, "reason": "마감 임박", "count": 4.0}'


def _req(**kw) -> AgentNodeRequest:
    return AgentNodeRequest(nodeId="n1", renderedPrompt="판단해줘", **kw)


async def _run(request, simple_return=(GOOD, 100, 20, 120)):
    simple = AsyncMock(return_value=simple_return)
    react = AsyncMock(return_value=("react 결과", 1, 1, 2))
    with patch("core.agent.run_simple_agent", simple), \
         patch("core.agent.run_react_agent", react), \
         patch("core.agent.execution_logs.insert_one", new=AsyncMock()) as insert_one:
        result = await run_agent(request, provider="GEMINI", api_key="k", user_id="u1")
    return result, simple, react, insert_one


@pytest.mark.asyncio
async def test_칸이_있으면_스키마를_넘기고_선언한_칸_객체를_돌려준다():
    result, simple, _, _ = await _run(_req(outputFields=FIELDS))

    assert result.success is True and result.errorCode is None
    assert result.output == {"urgent": True, "reason": "마감 임박", "count": 4}
    schema = simple.await_args.kwargs["output_schema"]
    assert schema["required"] == ["urgent", "reason", "count"]
    assert schema["properties"]["urgent"] == {"type": "boolean", "description": "긴급하면 true"}


@pytest.mark.asyncio
async def test_칸이_없으면_스키마_없이_output_한_칸():
    result, simple, _, _ = await _run(_req(), simple_return=("문장 결과", 1, 1, 2))

    assert result.output == {"output": "문장 결과"}
    assert simple.await_args.kwargs["output_schema"] is None


@pytest.mark.asyncio
async def test_react에_칸이_있으면_INVALID_OUTPUT_FIELDS_이고_모델을_부르지_않는다():
    result, simple, react, _ = await _run(_req(agentType="react", tools=[], outputFields=FIELDS))

    assert result.success is False
    assert result.errorCode == "INVALID_OUTPUT_FIELDS"
    simple.assert_not_awaited()
    react.assert_not_awaited()


@pytest.mark.asyncio
async def test_react에_칸이_없으면_기존대로_react_경로():
    result, simple, react, _ = await _run(_req(agentType="react"))

    react.assert_awaited_once()
    simple.assert_not_awaited()
    assert result.output == {"output": "react 결과"}


@pytest.mark.asyncio
async def test_불일치는_AI_OUTPUT_MISMATCH_와_usage():
    result, _, _, insert_one = await _run(_req(outputFields=FIELDS),
                                          simple_return=('{"urgent": "true", "reason": "r", "count": 1}', 100, 20, 120))

    assert result.success is False
    assert result.errorCode == "AI_OUTPUT_MISMATCH"
    assert "'urgent'는 boolean" in result.errorMessage
    assert result.output is None
    assert (result.usage.promptTokens, result.usage.completionTokens, result.usage.totalTokens) == (100, 20, 120)
    assert insert_one.await_args.args[0]["errorCode"] == "AI_OUTPUT_MISMATCH"


@pytest.mark.asyncio
async def test_규칙_위반은_INVALID_OUTPUT_FIELDS_이고_모델을_부르지_않는다():
    result, simple, react, insert_one = await _run(_req(outputFields=[{"key": "my-field", "type": "text"}]))

    assert result.success is False
    assert result.errorCode == "INVALID_OUTPUT_FIELDS"
    assert "my-field" in result.errorMessage
    simple.assert_not_awaited()
    react.assert_not_awaited()
    insert_one.assert_awaited_once()  # 실패도 실행 로그에 남는다


@pytest.mark.asyncio
async def test_칸과_도구가_같이_있으면_INVALID_OUTPUT_FIELDS():
    result, simple, react, _ = await _run(
        _req(agentType="react", tools=[{"name": "builtin:web_search"}], outputFields=FIELDS))

    assert result.errorCode == "INVALID_OUTPUT_FIELDS"
    simple.assert_not_awaited()
    react.assert_not_awaited()


@pytest.mark.asyncio
async def test_객체_값마다_응답_마스킹이_걸린다():
    text = '{"urgent": false, "reason": "토큰 ya29.abcdefghijk 노출", "count": 1}'
    result, _, _, _ = await _run(_req(outputFields=FIELDS), simple_return=(text, 1, 1, 2))

    assert result.output["reason"] == "토큰 [MASKED_GOOGLE_TOKEN] 노출"


def test_라우트는_문자열_outputFields도_422가_아니라_200_INVALID_OUTPUT_FIELDS():
    """BE는 config 값을 캐스트 없이 싣는다. 사용자가 문자열을 저장했어도 재시도 안 하는 코드로 떨어져야 한다."""
    from main import app

    with patch("core.agent.run_simple_agent", AsyncMock()) as simple, \
         patch("core.agent.execution_logs.insert_one", new=AsyncMock()):
        resp = TestClient(app).post(
            "/v1/execute",
            json={"nodeId": "n1", "renderedPrompt": "p", "outputFields": "urgent"},
            headers={"X-LLM-Provider": "GEMINI", "X-LLM-Api-Key": "k", "X-User-Id": "u1"},
        )

    assert resp.status_code == 200
    assert resp.json()["errorCode"] == "INVALID_OUTPUT_FIELDS"
    simple.assert_not_awaited()


# --- factory: 스키마 경로의 최종 텍스트 조립 ----------------------------------------

def _final_event(parts):
    event = MagicMock()
    event.is_final_response.return_value = True
    event.content = types.Content(role="model", parts=parts)
    return event


async def _simple(parts, output_schema):
    from agents.execute.factory import run_simple_agent

    async def _run_async(**kwargs):
        yield _final_event(parts)

    runner = MagicMock()
    runner.run_async = _run_async
    session = MagicMock()
    session.id = "s1"
    ss = MagicMock()
    ss.create_session = AsyncMock(return_value=session)
    ss.delete_session = AsyncMock()
    with patch("agents.execute.factory.LlmAgent") as agent_cls, \
         patch("agents.execute.factory.Runner", return_value=runner):
        out, *_ = await run_simple_agent(
            model="gemini-3.7-flash", provider="GEMINI", request=_req(), api_key="k",
            user_id="u1", session_service=ss, output_schema=output_schema,
        )
    return out, agent_cls.call_args.kwargs


@pytest.mark.asyncio
async def test_스키마_경로는_thought_파트를_빼고_빈_문자열로_잇는다():
    parts = [types.Part(text="먼저 생각해보면…", thought=True),
             types.Part(text='{"reason": "줄'), types.Part(text='바꿈 없음"}')]
    out, kwargs = await _simple(parts, {"type": "object", "properties": {}, "required": []})

    assert out == '{"reason": "줄바꿈 없음"}'
    assert kwargs["output_schema"] == {"type": "object", "properties": {}, "required": []}


@pytest.mark.asyncio
async def test_스키마_없는_경로는_그대로_개행으로_잇고_output_schema를_넘기지_않는다():
    out, kwargs = await _simple([types.Part(text="한 줄"), types.Part(text="두 줄")], None)

    assert out == "한 줄\n두 줄"
    assert "output_schema" not in kwargs

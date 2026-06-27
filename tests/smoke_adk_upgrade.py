"""ADK/genai SDK 업그레이드 스모크 — **실제 Gemini API를 호출하는 수동 테스트**.

목적: SDK 버전을 올릴 때(예: google-adk 1.x→2.x, genai 1.x→2.x) CustomGemini의
몽키패치·structured output·tool-call 경로가 실제 round-trip에서 동작하는지 확인한다.
구조 호환성(test_custom_gemini_compat.py)만으로는 "메서드가 존재한다"까지만 보이고,
"genai 2.x 요청/응답 스키마로 실제 유효 출력이 나온다"는 실콜로만 증명된다.

실행 방법 (기본 pytest에서는 제외됨 — pytest.ini의 `-m "not smoke"`):
    GOOGLE_API_KEY=... pytest tests/smoke_adk_upgrade.py -m smoke -v

주의:
- 실 API 호출이라 비용/네트워크/플레이크가 있다. CI 기본 실행에서 빼고 수동/별도 job만.
- 키는 GOOGLE_API_KEY env로만 받는다. 하드코딩·.env 자동로드 금지.
- 키가 없으면 전체 skip된다.
- **SDK 업그레이드 시 이 파일을 다시 돌려라.** (이게 이 파일의 존재 이유다.)
"""
import os
import json

import pytest

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("GOOGLE_API_KEY"),
        reason="GOOGLE_API_KEY 미설정 — 실 API 스모크 skip",
    ),
]

MODEL = os.environ.get("SMOKE_MODEL", "gemini-3.5-flash")


def _key() -> str:
    return os.environ["GOOGLE_API_KEY"]


async def _run(agent, prompt: str, max_calls: int = 8):
    """agent를 1회 실행하고 (응답텍스트, function_call수, in토큰, out토큰)을 반환한다."""
    from google.adk.runners import Runner
    from google.adk.agents.run_config import RunConfig
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    svc = InMemorySessionService()
    runner = Runner(agent=agent, app_name="smoke", session_service=svc)
    session = await svc.create_session(app_name="smoke", user_id="u1")
    msg = types.Content(role="user", parts=[types.Part(text=prompt)])

    text, calls, tin, tout = [], 0, 0, 0
    async for ev in runner.run_async(
        user_id="u1", session_id=session.id, new_message=msg,
        run_config=RunConfig(max_llm_calls=max_calls),
    ):
        calls += len(ev.get_function_calls() or [])
        if ev.is_final_response() and ev.content and ev.content.parts:
            for p in ev.content.parts:
                if getattr(p, "text", None):
                    text.append(p.text)
        if getattr(ev, "usage_metadata", None):
            um = ev.usage_metadata
            tin += um.prompt_token_count or 0
            tout += um.candidates_token_count or 0
    return "".join(text).strip(), calls, tin, tout


def _model():
    from core.custom_gemini import CustomGemini
    return CustomGemini(model=MODEL, api_key=_key())


async def test_smoke_plain_text():
    """단순 텍스트 응답 — genai 2.x 응답 파싱 + usage_metadata."""
    from google.adk.agents import LlmAgent
    agent = LlmAgent(name="smoke", model=_model(),
                     instruction="You are concise.")
    text, _, tin, tout = await _run(agent, "Reply with exactly: pong")
    assert text, "빈 응답 — 응답 파싱 실패"
    assert tin > 0 and tout > 0, "토큰 집계 실패"


async def test_smoke_tool_call_happy():
    """tool-call happy path — _clean_tools가 적용된 요청이 genai 2.x에서 수락된다."""
    from google.adk.agents import LlmAgent
    from google.adk.tools.function_tool import FunctionTool

    def add_numbers(a: int, b: int) -> dict:
        """두 정수를 더한다."""
        return {"result": a + b}

    agent = LlmAgent(name="smoke", model=_model(),
                     instruction="Use tools when relevant.",
                     tools=[FunctionTool(add_numbers)])
    text, calls, _, _ = await _run(agent, "Use add_numbers to add 17 and 25 and report the result.")
    assert calls >= 1, "tool 미호출 — _clean_tools 요청 거부 의심"
    assert "42" in text, "tool 결과 미반영"


async def test_smoke_tool_returns_error():
    """엣지: tool이 에러를 반환하는 경로 — 에이전트가 크래시 없이 처리하는가."""
    from google.adk.agents import LlmAgent
    from google.adk.tools.function_tool import FunctionTool

    def fetch_record(record_id: str) -> dict:
        """레코드를 조회한다(항상 에러 반환 스텁)."""
        return {"error": "not_found", "record_id": record_id}

    agent = LlmAgent(name="smoke", model=_model(),
                     instruction="Use fetch_record. If it errors, explain the error to the user.",
                     tools=[FunctionTool(fetch_record)])
    text, calls, _, _ = await _run(agent, "Fetch record 'abc' and tell me what happened.")
    assert calls >= 1, "tool 미호출"
    assert text, "에러 처리 후 빈 응답 — 에러 경로 크래시 의심"


async def test_smoke_agent_tool_delegation():
    """멀티에이전트 AgentTool 위임 — Main이 서브에이전트를 도구로 호출한다."""
    from google.adk.agents import LlmAgent
    from google.adk.tools.function_tool import FunctionTool
    from google.adk.tools.agent_tool import AgentTool

    def get_weather(city: str) -> dict:
        """도시 날씨를 반환한다(스텁)."""
        return {"city": city, "weather": "맑음", "temp_c": 23}

    sub = LlmAgent(name="weather_agent", model=_model(),
                   instruction="get_weather로 조회해 답하라.",
                   tools=[FunctionTool(get_weather)])
    main = LlmAgent(name="main_agent", model=_model(),
                    instruction="날씨 질문은 weather_agent에 위임하라.",
                    tools=[AgentTool(agent=sub)])
    text, calls, _, _ = await _run(main, "서울 날씨 알려줘.", max_calls=12)
    assert calls >= 1, "위임/도구 호출 0 — AgentTool 경로 실패"
    assert "맑음" in text or "23" in text, "서브 결과 미반영"


async def test_smoke_structured_output():
    """structured output(output_schema) — planner/reviewer가 쓰는 경로.
    genai 2.x response_schema 변환 + 응답이 스키마로 파싱되는지."""
    from google.adk.agents import LlmAgent
    from api.schemas.generate_workflow import WorkflowPlanSchema

    agent = LlmAgent(name="planner", model=_model(),
                     instruction="사용자 요청을 nodes/edges/justification JSON Plan으로 출력하라.",
                     output_schema=WorkflowPlanSchema)
    text, _, _, _ = await _run(agent, "매일 아침 뉴스를 검색해 요약하는 워크플로를 계획해줘.", max_calls=6)
    data = json.loads(text)
    parsed = WorkflowPlanSchema(**data)  # 스키마 검증
    assert parsed.nodes, "nodes 비어있음 — structured output 파싱 실패"

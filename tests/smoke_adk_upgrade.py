"""ADK/genai SDK 업그레이드 스모크 — **실제 Gemini API를 호출하는 수동 테스트**.

목적: SDK 버전을 올릴 때(예: google-adk 1.x→2.x, genai 1.x→2.x) CustomGemini의
몽키패치·structured output·tool-call 경로가 실제 round-trip에서 동작하는지 확인한다.
구조 호환성(test_custom_gemini_compat.py)만으로는 "메서드가 존재한다"까지만 보이고,
"genai 2.x 요청/응답 스키마로 실제 유효 출력이 나온다"는 실콜로만 증명된다.

실행 방법 (기본 pytest에서는 제외됨 — pytest.ini의 `-m "not smoke"`):

  권장 A) gitignore된 .env.smoke 파일에 키를 두고 source (키가 셸 히스토리에 안 남음):
      echo 'export GOOGLE_API_KEY=...' > .env.smoke   # .gitignore 처리됨
      source .env.smoke && pytest tests/smoke_adk_upgrade.py -m smoke -v && unset GOOGLE_API_KEY

  권장 B) read -s로 입력 (키가 커맨드라인/로그에 절대 안 뜸):
      read -rs GOOGLE_API_KEY && export GOOGLE_API_KEY
      pytest tests/smoke_adk_upgrade.py -m smoke -v; unset GOOGLE_API_KEY

  ⚠️ 금지: `GOOGLE_API_KEY=... pytest ...` 인라인 — 그 줄이 셸 히스토리/프로세스 로그에
  통째로 남아 키가 노출된다. 위 A/B로 키가 커맨드라인에 뜨지 않게 하라.

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
    # output_schema 경로는 순수 JSON을 보장하지만, 모델이 ```json 펜스로 감싸는
    # 플레이크를 방어해 파싱한다.
    import re
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    raw = m.group(1) if m else text
    data = json.loads(raw)
    parsed = WorkflowPlanSchema(**data)  # 스키마 검증
    assert parsed.nodes, "nodes 비어있음 — structured output 파싱 실패"


async def test_smoke_tool_with_additional_properties():
    """R1a 영구 회귀 가드 — additionalProperties 유발 도구가 (CustomGemini의 _clean_tools
    제거 후에도) 동작하는지. ADK 2.3이 _gemini_schema_util로 additionalProperties를 자체
    discard하므로 통과해야 한다. 실패(400 INVALID_ARGUMENT)면 ADK가 더는 self-sanitize 안
    한다는 뜻 → _clean_tools 재도입 또는 다른 대응 필요."""
    from google.adk.agents import LlmAgent
    from google.adk.tools.function_tool import FunctionTool

    def save_config(name: str, config: dict[str, str]) -> dict:
        """자유형 설정 dict를 저장한다. dict[str, str] 파라미터가 JSON 스키마에서
        additionalProperties를 유발한다(과거 Gemini 거부 케이스)."""
        return {"saved": name, "keys": list(config.keys())}

    agent = LlmAgent(name="smoke", model=_model(),
                     instruction="save_config로 설정을 저장하라.",
                     tools=[FunctionTool(save_config)])
    text, calls, _, _ = await _run(
        agent, "이름 'prod', 설정 {env: production, region: seoul}으로 save_config 호출해줘.")
    assert calls >= 1, "tool 미호출 — additionalProperties 도구가 거부됐을 수 있음"
    assert text, "빈 응답"


async def test_smoke_thinking_budget_path():
    """R1d 회귀 가드 — thinking 예산 경로. thinking 주입은 generate_content_async
    오버라이드로 이관됐고(monkeypatch 제거 완료), 이 경로로 실 호출이 성공하는지와
    thoughts 토큰 집계가 어떻게 나오는지 출력한다."""
    from google.adk.agents import LlmAgent
    from google.adk.runners import Runner
    from google.adk.agents.run_config import RunConfig
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    agent = LlmAgent(name="smoke", model=_model(),
                     instruction="간결히 답하라.")
    svc = InMemorySessionService()
    runner = Runner(agent=agent, app_name="smoke", session_service=svc)
    s = await svc.create_session(app_name="smoke", user_id="u1")
    msg = types.Content(role="user", parts=[types.Part(text="3+4는? 숫자만.")])

    thoughts, answered = 0, False
    async for ev in runner.run_async(user_id="u1", session_id=s.id, new_message=msg,
                                     run_config=RunConfig(max_llm_calls=4)):
        um = getattr(ev, "usage_metadata", None)
        if um:
            # genai 2.x: thinking 모델이면 thoughts_token_count 존재(없으면 None/0)
            thoughts += getattr(um, "thoughts_token_count", 0) or 0
        if ev.is_final_response() and ev.content and ev.content.parts:
            answered = any(getattr(p, "text", None) for p in ev.content.parts)
    print(f"[thinking-baseline] model={MODEL} thoughts_token_count 합계={thoughts}")
    assert answered, "응답 없음 — thinking 경로 호출 실패"


async def test_smoke_r2_behavioral_instruction_honored_via_agent_tool():
    """R2 행동 레벨 가드 — AgentTool로 위임된 서브의 행동규칙 instruction이 런타임에 실제로
    준수되는지. github_agent의 'search 금지·list 써라·JSON' 규칙을 일반화한 케이스다.
    회귀 테스트(test_execute_factory)는 '래핑됨'이라는 구조만 보지만, 이 버그는 정의상
    '규칙이 지켜지느냐'(행동)에서만 진짜로 닫힌다 → 실 LLM으로 검증.

    + R1d×R2 교차점: 위임된 서브 턴에도 thinking이 적용되는지 thoughts 토큰으로 관찰."""
    from google.adk.agents import LlmAgent
    from google.adk.tools.function_tool import FunctionTool
    from google.adk.tools.agent_tool import AgentTool
    from google.adk.runners import Runner
    from google.adk.agents.run_config import RunConfig
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    calls = {"search": 0, "list": 0}

    def search_records(query: str) -> dict:
        """레코드를 검색한다."""
        calls["search"] += 1
        return {"via": "search", "items": []}

    def list_records() -> dict:
        """레코드 목록을 반환한다."""
        calls["list"] += 1
        return {"via": "list", "items": [{"id": 1, "status": "merged"}]}

    # 행동규칙을 instruction에 담은 서브 (github의 search 금지·list·JSON 규칙 일반화)
    ledger = LlmAgent(
        name="ledger_agent", model=_model(),
        instruction=("레코드 조회는 반드시 list_records를 사용한다. search_records는 절대 쓰지 않는다. "
                     "최종 응답은 JSON만 반환한다."),
        tools=[FunctionTool(search_records), FunctionTool(list_records)],
    )
    main = LlmAgent(name="main", model=_model(),
                    instruction="레코드 관련 요청은 ledger_agent에 위임하라.",
                    tools=[AgentTool(agent=ledger)])

    svc = InMemorySessionService()
    runner = Runner(agent=main, app_name="smoke", session_service=svc)
    s = await svc.create_session(app_name="smoke", user_id="u1")
    msg = types.Content(role="user", parts=[types.Part(text="레코드 조회해줘.")])

    thoughts = 0
    async for ev in runner.run_async(user_id="u1", session_id=s.id, new_message=msg,
                                     run_config=RunConfig(max_llm_calls=12)):
        um = getattr(ev, "usage_metadata", None)
        if um:
            thoughts += getattr(um, "thoughts_token_count", 0) or 0
    print(f"[r2-behavior] list={calls['list']} search={calls['search']} delegated_thoughts={thoughts}")
    assert calls["list"] >= 1, "list_records 미호출 — 위임된 서브가 instruction 규칙을 안 따름(R2 미해결)"
    assert calls["search"] == 0, "search_records 호출됨 — 행동규칙(search 금지) 미준수 = R2가 행동 레벨에서 안 닫힘"

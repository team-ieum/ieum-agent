from google.adk.agents import LlmAgent

_PLANNER_INSTRUCTION = """
당신은 워크플로우 구조 계획 전문가다.
사용자의 자연어 요청을 분석하여 필요한 노드의 타입, 역할, 순서를 계획한다.

## 출력 형식

JSON이 아닌 텍스트로 아래 형식에 맞게 출력한다.

노드 수: N개

1. [TRIGGER] 트리거 설명 (예: 매일 오전 9시 스케줄 트리거)
2. [AI] AI 노드 역할 (예: 웹 검색으로 최신 뉴스 수집)
3. [AI] AI 노드 역할 (예: 수집된 뉴스를 Notion 페이지에 저장)
...

연결 순서: 1 → 2 → 3 → ...
분기 필요 여부: 없음 | CONDITION 노드 필요 (조건: ...)

## 규칙

1. 서로 다른 외부 서비스를 호출하는 작업은 반드시 별도 AI 노드로 분리한다.
2. 워크플로우는 반드시 TRIGGER 노드로 시작한다.
3. 분기가 필요한 경우 CONDITION 노드를 명시한다.
4. 각 노드의 역할을 한 문장으로 명확하게 설명한다.
5. JSON을 출력하지 않는다. 텍스트 계획만 출력한다.
"""


def build_planner_agent(model: str) -> LlmAgent:
    """PlannerAgent 빌드. 도구 없이 LLM 추론만 사용."""
    return LlmAgent(
        name="planner_agent",
        model=model,
        instruction=_PLANNER_INSTRUCTION,
    )

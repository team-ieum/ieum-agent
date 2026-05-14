import json
import logging
import os
import time
from datetime import datetime, timezone

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from api.schemas.generate_workflow import WorkflowNode, WorkflowEdge
from api.schemas.modify_workflow import ModifyWorkflowResponse
from common.error_code import ErrorCode
from core.env_lock import get_env_lock
from core.provider_config import resolve_model, resolve_env_key

logger = logging.getLogger(__name__)

_MODIFY_SYSTEM_PROMPT = """
You are a workflow modification assistant.
Given a user's modification request and the current workflow JSON, produce the updated workflow JSON.

## Current Workflow
{current_workflow_json}

## Output Format
Respond ONLY with a valid JSON object. No explanation, no markdown, no code fences.

{{
  "nodes": [...],
  "edges": [...],
  "changeDescription": "변경 내용을 한 문장으로 요약"
}}

## Node Types and Config Schema

### TRIGGER
{{
  "triggerType": "SCHEDULE | MANUAL | WEBHOOK",
  "cron": "0 9 * * *"   // SCHEDULE일 때만 포함. cron 표현식은 5자리 (분 시 일 월 요일)
}}

### AI
{{
  "llmProvider": "CLAUDE | OPENAI | GEMINI",
  "credentialId": "",
  "prompt": "프롬프트 텍스트. 이전 노드 결과 참조: {{{{nodes.<node-id>.output.<field>}}}}",
  "systemMessage": "시스템 메시지 (optional)",
  "model": null,
  "agentType": "simple | react",   // 도구 사용이 필요하면 react, 아니면 simple
  "tools": [
    {{"name": "builtin:http_fetch"}},
    {{"name": "builtin:notion_create_page"}}
  ]
}}

### HTTP
{{
  "method": "GET | POST | PUT | DELETE",
  "url": "https://...",
  "headers": {{}},
  "body": null
}}

### CONDITION
{{
  "operator": "equals | notEquals | contains | notContains | greaterThan | lessThan | greaterThanOrEqual | lessThanOrEqual | isEmpty | isNotEmpty",
  "leftValue": "{{{{nodes.<node-id>.output.<field>}}}}",
  "rightValue": "비교값"
}}

### TRANSFORM
{{
  "mappings": {{
    "newKey": "{{{{nodes.<node-id>.output.<field>}}}}"
  }}
}}

## Available Tools (AI 노드에서 사용 가능)
- builtin:http_fetch        : 외부 URL에 HTTP 요청 (뉴스 API, 외부 서비스 호출 등)
- builtin:notion_create_page: Notion 페이지 생성 (token, parent_page_id 필요)
- builtin:notion_read_page  : Notion 페이지 내용 읽기 (token, page_id 필요)
- builtin:notion_search     : Notion 워크스페이스 검색 (token, query 필요)
- builtin:notion_update_page: Notion 페이지 제목/내용 수정 (token, page_id 필요)
- builtin:notion_append_block: Notion 페이지에 블록 추가 (token, page_id 필요)
- builtin:json_parse        : JSON 문자열에서 특정 키 값 추출 (key_path 점 표기법 지원)
- builtin:text_extract      : 텍스트에서 정규식 패턴으로 값 추출
- builtin:date_format       : 날짜 문자열 포맷 변환 (ISO 8601 자동 파싱, 타임존 지원)
- slack                     : Slack 메시지 발송
- discord                   : Discord 웹훅 메시지 발송
- gmail                     : Gmail 발송

## Variable Reference Syntax
이전 노드의 결과를 참조할 때는 반드시 아래 형식을 사용한다.
{{{{nodes.<node-id>.output.<field>}}}}

## Rules
1. 기존 노드의 id 체계를 유지한다. 새 노드를 추가할 때는 기존 노드 중 가장 큰 번호 + 1로 부여한다.
2. 수정 요청에 해당하지 않는 노드는 원본 그대로 유지한다.
3. edges의 source/target은 반드시 nodes에 존재하는 id를 참조한다.
4. CONDITION 노드의 true/false 분기는 conditionType: "true" | "false" 로 표현한다.
5. AI 노드에서 외부 API 호출이나 Notion 저장이 필요하면 agentType을 "react"로 설정한다.
6. credentialId는 빈 문자열("")로 설정한다. Spring Boot에서 주입한다.
7. JSON 외 어떤 텍스트도 출력하지 않는다.
8. AI 노드의 llmProvider는 반드시 기존 워크플로우에서 사용된 provider를 유지한다.
   새로 추가하는 AI 노드도 동일한 provider를 사용한다.
9. parent_page_id 등 사용자가 명시하지 않은 값은 빈 문자열("")로 설정한다.
   절대 플레이스홀더(YOUR_XXX_HERE, <값> 형태 등)를 사용하지 않는다.
10. changeDescription은 반드시 사용자 요청과 동일한 언어로 작성한다.
    한국어로 요청하면 한국어로, 영어로 요청하면 영어로 작성한다.
11. 서로 다른 외부 서비스를 호출하는 작업은 반드시 별도의 AI 노드로 분리한다.
"""


async def modify_workflow(
    prompt: str,
    current_nodes: list,
    current_edges: list,
    provider: str,
    api_key: str,
) -> ModifyWorkflowResponse:
    start = time.monotonic()
    model = resolve_model(provider)
    env_key = resolve_env_key(provider)
    lock = get_env_lock(env_key) if env_key else None

    current_workflow_json = json.dumps(
        {"nodes": current_nodes, "edges": current_edges},
        ensure_ascii=False,
        indent=2,
    )
    instruction = _MODIFY_SYSTEM_PROMPT.format(current_workflow_json=current_workflow_json)

    async def _execute() -> str:
        prev_value = os.environ.get(env_key) if env_key else None
        try:
            if env_key:
                os.environ[env_key] = api_key

            agent = LlmAgent(
                name="workflow_modifier",
                model=model,
                instruction=instruction,
            )

            session_service = InMemorySessionService()
            runner = Runner(
                agent=agent,
                app_name="ieum-agent",
                session_service=session_service,
            )

            session = await session_service.create_session(
                app_name="ieum-agent",
                user_id="user",
            )

            message = types.Content(
                role="user",
                parts=[types.Part(text=prompt)],
            )

            output_parts = []
            async for event in runner.run_async(
                user_id="user",
                session_id=session.id,
                new_message=message,
            ):
                if event.is_final_response() and event.content:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            output_parts.append(part.text)

            return "\n".join(output_parts) if output_parts else ""

        finally:
            if env_key:
                if prev_value is None:
                    os.environ.pop(env_key, None)
                else:
                    os.environ[env_key] = prev_value

    if lock:
        async with lock:
            raw_output = await _execute()
    else:
        raw_output = await _execute()

    # JSON 파싱
    try:
        # 마크다운 코드 펜스 제거 (LLM이 실수로 감쌀 경우 대비)
        cleaned = raw_output.strip()

        if not cleaned:
            logger.error("LLM이 빈 응답을 반환했습니다. provider: %s", provider)
            raise ValueError("LLM이 빈 응답을 반환했습니다.")

        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
        if cleaned.rstrip().endswith("```"):
            cleaned = "\n".join(cleaned.rstrip().split("\n")[:-1])
        cleaned = cleaned.strip()

        data = json.loads(cleaned)

        nodes = [WorkflowNode(**n) for n in data.get("nodes", [])]
        edges = [WorkflowEdge(**e) for e in data.get("edges", [])]
        change_description = data.get("changeDescription", "")

        response = ModifyWorkflowResponse(
            nodes=nodes,
            edges=edges,
            rawPrompt=prompt,
            changeDescription=change_description,
        )

        # TODO: modify_workflow_logs 컬렉션 로깅 (Commit 4에서 추가)
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "워크플로우 수정 성공: nodes=%d, edges=%d, duration=%dms",
            len(nodes), len(edges), duration_ms,
        )

        return response

    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.error("워크플로우 수정 JSON 파싱 실패: %s\nraw_output: %s", str(e), raw_output)
        raise ValueError(f"{ErrorCode.AGENT_EXECUTION_FAILED.message} (JSON 파싱 실패: {str(e)})")

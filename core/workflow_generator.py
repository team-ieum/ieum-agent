import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from api.schemas.generate_workflow import GenerateWorkflowResponse, WorkflowNode, WorkflowEdge
from common.error_code import ErrorCode
from core.env_lock import get_env_lock
from core.provider_config import resolve_model, resolve_env_key
from db.mongodb import generate_workflow_logs

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """
You are a workflow automation assistant.
Given a user's natural language request, generate a workflow JSON that can be executed by the IEUM workflow engine.

## Output Format
Respond ONLY with a valid JSON object. No explanation, no markdown, no code fences.

{
  "nodes": [
    {
      "id": "node-1",
      "type": "TRIGGER | AI | HTTP | CONDITION | TRANSFORM",
      "label": "노드 설명",
      "config": { ... }
    }
  ],
  "edges": [
    {
      "source": "node-1",
      "target": "node-2",
      "conditionType": null
    }
  ]
}

## Node Types and Config Schema

### TRIGGER
{
  "triggerType": "SCHEDULE | MANUAL | WEBHOOK",
  "cron": "0 9 * * *"   // SCHEDULE일 때만 포함. cron 표현식은 5자리 (분 시 일 월 요일)
}

### AI
{
  "llmProvider": "CLAUDE | OPENAI | GEMINI",
  "credentialId": "",
  "prompt": "프롬프트 텍스트. 이전 노드 결과 참조: {{nodes.<node-id>.output.<field>}}",
  "systemMessage": "시스템 메시지 (optional)",
  "model": null,
  "agentType": "simple | react",   // 도구 사용이 필요하면 react, 아니면 simple
  "tools": [
    {"name": "builtin:http_fetch"},
    {"name": "builtin:notion_create_page"}
  ]
}

### HTTP
{
  "method": "GET | POST | PUT | DELETE",
  "url": "https://...",
  "headers": {},
  "body": null
}

### CONDITION
{
  "operator": "equals | notEquals | contains | notContains | greaterThan | lessThan | greaterThanOrEqual | lessThanOrEqual | isEmpty | isNotEmpty",
  "leftValue": "{{nodes.<node-id>.output.<field>}}",
  "rightValue": "비교값"
}

### TRANSFORM
{
  "mappings": {
    "newKey": "{{nodes.<node-id>.output.<field>}}"
  }
}

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
{{nodes.<node-id>.output.<field>}}

예시:
- {{nodes.node-1.output.triggeredAt}}
- {{nodes.node-2.output.output}}

## Rules
1. 노드 id는 "node-1", "node-2" 순서로 부여한다.
2. 워크플로우는 반드시 TRIGGER 노드로 시작한다.
3. edges의 source/target은 반드시 nodes에 존재하는 id를 참조한다.
4. CONDITION 노드의 true/false 분기는 conditionType: "true" | "false" 로 표현한다.
5. AI 노드에서 외부 API 호출이나 Notion 저장이 필요하면 agentType을 "react"로 설정한다.
6. credentialId는 빈 문자열("")로 설정한다. Spring Boot에서 주입한다.
7. JSON 외 어떤 텍스트도 출력하지 않는다.
8. AI 노드의 llmProvider는 반드시 사용자 요청에 사용된 provider와 동일하게 설정한다.
   예: 요청 헤더가 GEMINI면 모든 AI 노드의 llmProvider는 "GEMINI"로 설정한다.
   provider를 알 수 없는 경우에만 "CLAUDE"를 기본값으로 사용한다.
9. parent_page_id 등 사용자가 명시하지 않은 값은 빈 문자열("")로 설정한다.
   절대 플레이스홀더(YOUR_XXX_HERE, <값> 형태 등)를 사용하지 않는다.
10. prompt, systemMessage, label은 반드시 사용자 요청과 동일한 언어로 작성한다.
    한국어로 요청하면 한국어로, 영어로 요청하면 영어로 작성한다.
11. 서로 다른 외부 서비스를 호출하는 작업은 반드시 별도의 AI 노드로 분리한다.
    예: 뉴스 API 호출(http_fetch)과 Notion 저장(notion_create_page)은 각각 다른 노드로 구성한다.
    하나의 AI 노드에는 동일한 목적의 도구만 포함한다.
    나쁜 예: tools: [builtin:http_fetch, builtin:notion_create_page] → 하나의 노드에 혼합
    좋은 예: node-2(tools: [builtin:http_fetch]) → node-3(tools: [builtin:notion_create_page])
"""


async def _save_generate_workflow_log(
    prompt: str,
    provider: str,
    model: str,
    success: bool,
    duration_ms: int,
    node_count: int | None = None,
    edge_count: int | None = None,
    error_message: str | None = None,
) -> None:
    """generate_workflow 실행 결과를 MongoDB에 저장한다. 실패 시 경고 로그만 남긴다."""
    try:
        await generate_workflow_logs.insert_one({
            "prompt": prompt,
            "provider": provider,
            "model": model,
            "success": success,
            "nodeCount": node_count,
            "edgeCount": edge_count,
            "errorMessage": error_message,
            "durationMs": duration_ms,
            "createdAt": datetime.now(timezone.utc),
        })
    except Exception:
        logger.warning("Failed to save generate_workflow log", exc_info=True)


async def generate_workflow(
    prompt: str,
    provider: str,
    api_key: str,
) -> GenerateWorkflowResponse:
    start = time.monotonic()
    model = resolve_model(provider)
    env_key = resolve_env_key(provider)
    lock = get_env_lock(env_key) if env_key else None

    async def _execute() -> str:
        prev_value = os.environ.get(env_key) if env_key else None
        try:
            if env_key:
                os.environ[env_key] = api_key

            agent = LlmAgent(
                name="workflow_generator",
                model=model,
                instruction=_SYSTEM_PROMPT + f"\n\n## Current Request Context\n- provider: {provider.upper()}\n  (모든 AI 노드의 llmProvider는 반드시 \"{provider.upper()}\"로 설정한다)",
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

        response = GenerateWorkflowResponse(
            nodes=nodes,
            edges=edges,
            rawPrompt=prompt,
        )

        # 성공 로그 저장
        duration_ms = int((time.monotonic() - start) * 1000)
        await _save_generate_workflow_log(
            prompt=prompt,
            provider=provider,
            model=model,
            success=True,
            duration_ms=duration_ms,
            node_count=len(nodes),
            edge_count=len(edges),
        )

        return response

    except Exception as e:
        # 실패 로그 저장
        duration_ms = int((time.monotonic() - start) * 1000)
        await _save_generate_workflow_log(
            prompt=prompt,
            provider=provider,
            model=model,
            success=False,
            duration_ms=duration_ms,
            error_message=str(e),
        )

        logger.error("워크플로우 JSON 파싱 실패: %s\nraw_output: %s", str(e), raw_output)
        raise ValueError(f"{ErrorCode.AGENT_EXECUTION_FAILED.message} (JSON 파싱 실패: {str(e)})")

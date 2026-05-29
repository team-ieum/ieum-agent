import json
import logging
import time
from datetime import datetime, timezone

from api.schemas.generate_workflow import GenerateWorkflowResponse, WorkflowNode, WorkflowEdge
from common.error_code import ErrorCode
from core.env_lock import get_env_lock
from core.provider_config import resolve_model, resolve_env_key
from db.mongodb import generate_workflow_logs
from core.validators.workflow_validator import WorkflowValidator, WorkflowValidationError

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
    {"name": "builtin:web_search"},
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
- builtin:web_search        : 웹 검색 결과 조회 (query, maxResults 필요)
- builtin:http_fetch        : 외부 URL에 HTTP 요청 (뉴스 API, 외부 서비스 호출 등)
- builtin:notion_create_page: Notion 페이지 생성 (token, parent_page_id 필요)
- builtin:notion_read_page  : Notion 페이지 내용 읽기 (token, page_id 필요)
- builtin:notion_search     : Notion 워크스페이스 검색 (token, query 필요)
- builtin:notion_update_page: Notion 페이지 제목/내용 수정 (token, page_id 필요)
- builtin:notion_append_block: Notion 페이지에 블록 추가 (token, page_id 필요)
- builtin:json_parse        : JSON 문자열에서 특정 키 값 추출 (key_path 점 표기법 지원)
- builtin:text_extract      : 텍스트에서 정규식 패턴으로 값 추출
- builtin:date_format       : 날짜 문자열 포맷 변환 (ISO 8601 자동 파싱, 타임존 지원)
- builtin:google_sheets_read    : Google Sheets 데이터 읽기 (access_token, spreadsheet_id, range 필요)
- builtin:google_sheets_write   : Google Sheets 데이터 쓰기 (access_token, spreadsheet_id, range, values 필요)
- builtin:google_calendar_create: Google Calendar 일정 생성 (access_token, summary, start_datetime, end_datetime 필요)
- builtin:google_calendar_list  : Google Calendar 일정 조회 (access_token, time_min, time_max 필요)
- builtin:google_drive_read     : Google Drive 파일 읽기 (access_token, file_id 필요)
- builtin:google_drive_upload   : Google Drive 파일 업로드 (access_token, name, content 필요)
- builtin:workflow_context      : 이전 노드의 출력 결과 조회 (get_node_output | list_completed_nodes | get_trigger_input)
- slack                     : Slack 메시지 발송
- discord                   : Discord 웹훅 메시지 발송
- gmail                     : Gmail 발송
- mcp                       : 외부 MCP 서버 Tool 호출 (server_url, tool_name, arguments 필요)

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
12. Google 빌트인 도구(builtin:google_sheets_*, builtin:google_calendar_*, builtin:google_drive_*)의
    access_token 파라미터는 빈 문자열("")로 설정한다. Spring Boot에서 실행 시 주입한다.
13. 데이터 무결성 보존 및 가공 위임:
    - 외부 데이터를 수집하는 조회 노드(예: 깃허브 PR 조회, 노션 페이지 조회 등)는 원시 JSON 형태 데이터를 마음대로 요약/축소하지 말고 그대로 output으로 출력하도록 prompt 및 systemMessage를 설계해야 합니다. (예: "결과 데이터를 절대 요약하지 말고 JSON 원본 그대로 반환하시오")
    - 데이터 요약, 날짜 포맷팅, JSON 파싱 등 데이터 변환 작업이 필요할 때는, 조회 노드에서 직접 가공하지 말고 `transform_agent` (혹은 `json_parse` 등의 도구)를 주입한 별도의 AI 노드에 가공 업무를 명시적으로 위임합니다.
14. 생성 노드 프롬프트 경량화:
    - 각 노드를 설계할 때 노드의 systemMessage나 prompt에 불필요한 사설이나 배경 설명을 과하게 채우지 말고, 핵심 지시사항(동작, 입력 참조값, 출력 형식 등) 위주로 최대 2~3문장 이내로만 간결하게 작성합니다.
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


def _extract_json_object(raw: str) -> str:
    """LLM 출력에서 최상위 JSON 객체 문자열을 견고하게 추출한다.

    코드 펜스(```), 서론/설명문, 후행 텍스트가 섞여 있어도 첫 번째 '{' 부터
    중괄호 짝이 맞는 지점까지를 추출한다. 문자열 리터럴 내부의 중괄호와
    이스케이프(\\")는 깊이 계산에서 제외한다.
    """
    start = raw.find("{")
    if start == -1:
        raise ValueError("응답에서 JSON 객체를 찾을 수 없습니다.")

    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start:i + 1]

    raise ValueError("JSON 객체의 중괄호 짝이 맞지 않습니다.")


def _parse_and_validate(raw_output: str, original_prompt: str) -> GenerateWorkflowResponse:
    if not raw_output or not raw_output.strip():
        raise ValueError("LLM이 빈 응답을 반환했습니다.")

    cleaned = _extract_json_object(raw_output)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 문법 오류가 있습니다: {str(e)}")

    # 1. Pydantic 스키마 형태 로드 (여기서 Pydantic ValidationError 발생 가능)
    try:
        nodes = [WorkflowNode(**n) for n in data.get("nodes", [])]
        edges = [WorkflowEdge(**e) for e in data.get("edges", [])]
    except Exception as e:
        raise ValueError(f"스키마 검증(Pydantic) 실패: {str(e)}")

    # 2. 코드 레벨 의미론적 상세 검증
    raw_nodes = data.get("nodes", [])
    raw_edges = data.get("edges", [])
    WorkflowValidator.validate(raw_nodes, raw_edges)

    return GenerateWorkflowResponse(
        nodes=nodes,
        edges=edges,
        rawPrompt=original_prompt,
    )


async def generate_workflow(
    prompt: str,
    provider: str,
    api_key: str,
) -> GenerateWorkflowResponse:
    start = time.monotonic()
    model = resolve_model(provider)
    env_key = resolve_env_key(provider)
    lock = get_env_lock(env_key) if env_key else None

    def _validate(raw_output: str) -> GenerateWorkflowResponse:
        # Builder Reflexion 루프(factory)가 호출하는 검증 콜백. 실패 시 예외를 던진다.
        return _parse_and_validate(raw_output, prompt)

    async def _execute() -> str:
        from agents.generate.factory import run_generate_agent
        return await run_generate_agent(
            prompt=prompt,
            model=model,
            provider=provider,
            api_key=api_key,
            env_key=env_key,
            validate_fn=_validate,
        )

    try:
        # Plan 검증·Builder Reflexion 루프는 run_generate_agent 내부에서 수행된다.
        # 반환된 raw_output은 이미 _validate를 통과한 상태이므로 여기서 객체화만 한다.
        if lock:
            async with lock:
                raw_output = await _execute()
        else:
            raw_output = await _execute()

        response = _parse_and_validate(raw_output, prompt)
    except Exception as err:
        duration_ms = int((time.monotonic() - start) * 1000)
        await _save_generate_workflow_log(
            prompt=prompt,
            provider=provider,
            model=model,
            success=False,
            duration_ms=duration_ms,
            error_message=f"워크플로우 생성/검증 최종 실패: {str(err)}",
        )
        logger.error("워크플로우 생성/검증 최종 실패: %s", str(err), exc_info=True)
        raise ValueError(f"{ErrorCode.AGENT_EXECUTION_FAILED.message} (JSON 파싱 실패: {str(err)})")

    # 성공 로그 저장
    duration_ms = int((time.monotonic() - start) * 1000)
    await _save_generate_workflow_log(
        prompt=prompt,
        provider=provider,
        model=model,
        success=True,
        duration_ms=duration_ms,
        node_count=len(response.nodes),
        edge_count=len(response.edges),
    )
    return response

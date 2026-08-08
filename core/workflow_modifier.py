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
from core.model_factory import build_model_param, uses_env_key
from db.mongodb import modify_workflow_logs

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
  "agentType": "simple | react",   // 도구 사용이 필요하면 react, 아니면 simple
  "tools": [
    {{"name": "builtin:web_search"}},
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
  "left": "{{{{nodes.<node-id>.output.<field>}}}}",
  "right": "비교값"
}}

### TRANSFORM
{{
  "mappings": {{
    "newKey": "{{{{nodes.<node-id>.output.<field>}}}}"
  }}
}}

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
11-1. 각 노드는 config 외에 label과 description 최상위 필드를 가진다. description은 워크플로우 화면에서
    사용자에게 그대로 보여줄 쉬운 안내 1문장이다(예: "AI가 문의 내용을 읽고 알맞은 유형으로 나눠요.").
    수정하지 않는 노드의 description은 원본 그대로 유지하고, 새로 추가하는 노드에는 반드시 새로 작성하며,
    기존 노드의 description이 비어 있으면 그 노드의 label과 prompt를 보고 채운다.
    도구 키·필드명·변수 참조식 등 기술 용어는 넣지 않는다.
12. Google 빌트인 도구(builtin:google_sheets_*, builtin:google_calendar_*, builtin:google_drive_*)의
    access_token 파라미터는 빈 문자열("")로 설정한다. Spring Boot에서 실행 시 주입한다.
13. AI 노드 config의 model, serviceType은 작성하지 않는다. 노드가 어느 앱인지·어떤 모델로
    돌지는 실행 시점에 결정되며, 지어낸 값을 쓰면 잘못된 앱 배지나 존재하지 않는 모델이 저장된다.
"""


def _preserve_descriptions(new_nodes: list, current_nodes: list | None) -> None:
    """LLM 응답에서 빠진 노드 description을 기존 노드 값으로 되살린다(in-place).

    이 경로는 재검증·재시도 루프가 없어 한 번 누락되면 그대로 저장된다. 수정 대상이 아닌 노드의
    사용자용 설명이 조용히 사라지지 않도록 코드로 보존한다(같은 노드 id 기준).

    복원 순서는 **기존 값 → label**이다. label 복제는 되살릴 원본이 아예 없을 때만 쓰는 최후
    수단이다 — 원본이 있는데 label로 덮으면 사용자가 쓴 안내 문장이 영구 소실된다.

    label 변경 여부로 '용도가 교체된 노드'를 가리려던 판정은 뺐다. 개명(설명은 그대로여야 함)과
    동작 교체(설명이 바뀌어야 함)를 label 동등성으로는 구분할 수 없어, 어느 쪽으로 두든 반대편이
    틀린다. 설명을 갱신할지는 LLM이 판단할 몫이고 이 함수는 소실만 막는다."""
    prev = {
        n.get("id"): n.get("description")
        for n in (current_nodes or []) if isinstance(n, dict)
    }
    for node in new_nodes:
        if not isinstance(node, dict) or str(node.get("description") or "").strip():
            continue
        desc = str(prev.get(node.get("id")) or "").strip()
        if not desc:
            # description 도입 이전 저장분은 기존 값도 비어 있다. 그대로 두면 BE NodeDto의
            # @NotBlank에 걸려 사용자가 그 워크플로우를 저장할 수 없으므로 label로 채운다.
            label = node.get("label")
            desc = label.strip() if isinstance(label, str) else ""
        if desc:
            node["description"] = desc


# modify 경로는 model·serviceType·brand를 손대지 않는다.
#
# 이 필드들을 응답 후처리로 되채우려던 시도가 리뷰 3라운드 내내 회귀를 만들었다 — 도구 유무로
# 가드하면 tools가 항상 빈 ai.github_query의 앱 태그가 지워지고, intent로 재확인하면 태그 점수
# 경합에 져서 또 지워지며, 도구가 여럿이면 템플릿 순회 순서가 serviceType을 정한다. 노드가 어느
# 앱인지는 full-node JSON만 보고 결정론적으로 답할 수 없다.
#
# 정답은 생성 경로처럼 템플릿에서 복원하는 것(dehydrate→hydrate)이고, 그건 modify 경로 전체를
# draft 방식으로 바꾸는 별도 작업이다(IEUM-AI-58). 그때까지 이 경로는 이 PR 이전과 동일하게
# 두 필드를 비워 두고 BE·FE 폴백에 맡긴다. 잘못된 값을 결정론적으로 쓰는 것보다 낫다.


async def _save_modify_workflow_log(
    prompt: str,
    provider: str,
    model: str,
    success: bool,
    duration_ms: int,
    node_count: int | None = None,
    edge_count: int | None = None,
    error_message: str | None = None,
    key_mode: str | None = None,
) -> None:
    """modify_workflow 실행 결과를 MongoDB에 저장한다. 실패 시 경고 로그만 남긴다."""
    try:
        await modify_workflow_logs.insert_one({
            "prompt": prompt,
            "provider": provider,
            "model": model,
            "success": success,
            "nodeCount": node_count,
            "edgeCount": edge_count,
            "errorMessage": error_message,
            "keyMode": key_mode,
            "durationMs": duration_ms,
            "createdAt": datetime.now(timezone.utc),
        })
    except Exception:
        logger.warning("Failed to save modify_workflow log", exc_info=True)


async def modify_workflow(
    prompt: str,
    current_nodes: list,
    current_edges: list,
    provider: str,
    api_key: str | None,
    user_role: str | None = None,
    key_mode: str | None = None,
) -> ModifyWorkflowResponse:
    start = time.monotonic()
    model = resolve_model(provider)
    env_key = resolve_env_key(provider)
    inject_env = uses_env_key(provider, api_key, user_role)
    lock = get_env_lock(env_key) if (env_key and inject_env) else None
    model_param = build_model_param(provider, model, api_key, user_role)

    current_workflow_json = json.dumps(
        {"nodes": current_nodes, "edges": current_edges},
        ensure_ascii=False,
        indent=2,
    )
    instruction = _MODIFY_SYSTEM_PROMPT.format(current_workflow_json=current_workflow_json)

    async def _execute() -> str:
        prev_value = os.environ.get(env_key) if (env_key and inject_env) else None
        try:
            if env_key and inject_env:
                os.environ[env_key] = api_key

            agent = LlmAgent(
                name="workflow_modifier",
                model=model_param,
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
            if env_key and inject_env:
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

        # nodes 키가 아예 없으면 "바꿨습니다" 메시지와 함께 노드 0개 워크플로우가 200으로 나가고,
        # 사용자가 저장하는 순간 원본이 통째로 날아간다. 키 부재만 파싱 실패로 다룬다 — 빈 배열은
        # "노드 다 지우고 다시 짤게" 같은 정당한 요청의 정확한 응답이다.
        if "nodes" not in data:
            raise ValueError("LLM 응답에 nodes가 없습니다.")
        raw_nodes = data["nodes"]
        if not isinstance(raw_nodes, list):
            raise ValueError("LLM 응답의 nodes가 리스트가 아닙니다.")
        _preserve_descriptions(raw_nodes, current_nodes)
        nodes = [WorkflowNode(**n) for n in raw_nodes]
        edges = [WorkflowEdge(**e) for e in data.get("edges", [])]
        change_description = data.get("changeDescription", "")

        response = ModifyWorkflowResponse(
            nodes=nodes,
            edges=edges,
            rawPrompt=prompt,
            changeDescription=change_description,
        )

        # 성공 로그 저장
        duration_ms = int((time.monotonic() - start) * 1000)
        await _save_modify_workflow_log(
            prompt=prompt,
            provider=provider,
            model=model,
            success=True,
            duration_ms=duration_ms,
            node_count=len(nodes),
            edge_count=len(edges),
            key_mode=key_mode,
        )

        return response

    except Exception as e:
        # 실패 로그 저장
        duration_ms = int((time.monotonic() - start) * 1000)
        await _save_modify_workflow_log(
            prompt=prompt,
            provider=provider,
            model=model,
            success=False,
            duration_ms=duration_ms,
            error_message=str(e),
            key_mode=key_mode,
        )

        logger.error("워크플로우 수정 JSON 파싱 실패: %s\nraw_output: %s", str(e), raw_output)
        raise ValueError(f"{ErrorCode.WORKFLOW_MODIFY_PARSE_FAILED.message}")

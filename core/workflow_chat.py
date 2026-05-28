import contextlib
import functools
import inspect
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel, Field

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, SseConnectionParams
from google.genai import types

from api.schemas.chat import ChatResponse, ChatResponseType, ChatAction
from api.schemas.generate_workflow import WorkflowNode, WorkflowEdge
from common.error_code import ErrorCode
from core.config import get_current_time_info
from core.env_lock import get_env_lock
from core.provider_config import resolve_model, resolve_env_key
from core.custom_gemini import CustomGemini
from db.mongodb import chat_logs
from tools.notion import notion_search
from tools.github import github_list_orgs, github_list_repos, github_list_issues, github_list_pull_requests
from tools.google_list import google_list_calendars, google_list_sheets
from agents.base import _safe_close_mcp

logger = logging.getLogger(__name__)

_SESSION_SERVICE = None

def _get_session_service():
    global _SESSION_SERVICE
    if _SESSION_SERVICE is None:
        _SESSION_SERVICE = InMemorySessionService()
    return _SESSION_SERVICE


class ChatResponseOutputSchema(BaseModel):
    """LLM이 구조적으로 출력해야 하는 데이터 규격"""
    message: str = Field(
        description="사용자에게 전달할 대화 메시지. 사용자의 요청 언어와 동일하게 작성합니다."
    )
    type: ChatResponseType = Field(
        description="응답의 유형 (WORKFLOW_GENERATED | WORKFLOW_MODIFIED | INTEGRATION_REQUIRED | CLARIFICATION_NEEDED)"
    )
    actions: List[ChatAction] = Field(
        default=[], 
        description="OAuth 연동이 추가로 필요한 경우에만 포함하는 액션 목록"
    )
    changeDescription: Optional[str] = Field(
        default=None, 
        description="워크플로우 수정(WORKFLOW_MODIFIED) 시 변경 내용을 요약한 한 문장"
    )
    nodes: Optional[List[WorkflowNode]] = Field(
        default=None, 
        description="생성/수정된 노드 목록. 그 외에는 null"
    )
    edges: Optional[List[WorkflowEdge]] = Field(
        default=None, 
        description="생성/수정된 엣지 목록. 그 외에는 null"
    )
    workflowName: Optional[str] = Field(
        default=None,
        description="생성된 워크플로우에 잘 어울리는 간결하고 직관적인 한국어 이름 (예: 'IT 트렌드 자동 노션 요약'). 신규 생성(WORKFLOW_GENERATED) 시에만 생성하며, 수정 시에는 null로 설정합니다."
    )


_SYSTEM_PROMPT_BASE = """\
당신은 IEUM 워크플로우를 생성 및 관리하는 AI 어시스턴트입니다.
사용자 요청을 분석하여 워크플로우를 구성하거나, 연동 상태를 확인하여 필요한 리소스를 안내하세요.

<workflow_design_rules>
1. 노드 구성: 지원 노드 타입은 TRIGGER, AI, HTTP, CONDITION, TRANSFORM 뿐입니다. 
   - Notion, Gmail, Slack 등의 외부 연동은 별도의 노드 타입이 아니며, 반드시 AI 노드의 tools(예: builtin:notion_*, slack, gmail 등)를 통해 구현해야 합니다.
2. 다중 노드 설계: 서로 다른 외부 서비스를 호출하는 작업은 반드시 별도의 AI 노드로 분리하십시오.
   - 예: 뉴스 조회(http_fetch)와 Notion 저장(notion_create_page)은 서로 다른 노드여야 합니다.
3. 이전 결과 참조 및 변수 제약:
   - 이전 노드 결과는 오직 이중 중괄호(즉, 2개의 열기 중괄호 문자와 2개의 닫기 중괄호 문자)로 감싸서 'nodes.노드ID.output.필드명' 형식으로만 참조해야 합니다. (예: nodes.node-1.output.data 를 이중 중괄호로 감싸서 표현)
   - 'current_date' 이나 'today' 같이 시스템에 정의되지 않은 임의의 변수를 이중 중괄호로 감싸서 절대로 지어내어 노드 설정에 기입하지 마십시오. 치환되지 않고 에러가 발생합니다.
   - 오늘 날짜나 시간이 필요한 경우, AI 노드(LLM)가 자신의 Prompt 내에서 현재 날짜를 파악하여 쓰도록 지시하거나, 트리거 노드가 실행 시점 데이터를 전달하도록 설계하십시오.
4. 노드 간 데이터 연동(Data Link):
   - 선행 노드가 생성한 데이터를 후속 노드가 소비할 때(예: 리서치 요약 결과를 노션에 등록), 반드시 후속 노드의 `prompt` 설정에 선행 노드의 아웃풋 참조(예: nodes.node-2.output.content 를 이중 중괄호로 감싼 형태)를 포함시켜 실질적인 데이터 흐름이 이어지도록 하십시오. 빈 데이터나 하드코딩된 빈 문자열로 데이터를 넘겨두지 마십시오.
5. 올바른 내장 도구(Built-in Tools) 바인딩:
   - `builtin:web_search`: 일반적인 인터넷 검색, 뉴스/트렌드 조사 시 사용해야 합니다.
   - `builtin:http_fetch`: 특정 API를 직접 호출하거나 명확한 특정 URL(https://)의 페이지 전체 텍스트 내용을 직접 긁어올 때만 제한적으로 사용하십시오. (단순한 검색 및 트렌드 조사 목적으로 http_fetch를 매핑하는 실수를 저지르지 마십시오.)
   - Notion 페이지 생성/수정/조회에는 Notion MCP 도구들(`create_page`, `append_block`, `search`, `update_page`, `read_page` 등)을 바인딩해야 합니다.
6. AI 노드 구성: 외부 API 호출이나 Notion 연동이 포함된 AI 노드는 agentType을 "react"로 설정하십시오.
7. 커스텀 MCP 도구 구성: 사용자가 연동한 외부 커스텀 MCP 서버의 도구들(예: trendradar_*)이 주입된 경우, 사용자의 해당 기능(실시간 트렌드 수집 등) 요청에 맞춰 AI 노드 내의 tools에 이 도구명들을 바인딩하여 워크플로우를 생성하십시오.
8. 워크플로우 이름 자동 생성: 워크플로우가 신규 생성(type: WORKFLOW_GENERATED)될 때, 해당 워크플로우의 목적과 기능을 가장 잘 설명하는 한국어 이름(예: 'IT 트렌드 자동 노션 요약')을 지어 'workflowName' 필드에 담아 보내십시오. 워크플로우가 수정(type: WORKFLOW_MODIFIED)될 때는 이 필드를 null로 비워두어야 합니다.
</workflow_design_rules>

<resource_rules>
- 워크플로우를 완성하기 전, 실행에 필요한 실제 리소스 ID(Notion parent_page_id, Sheets spreadsheet_id, Calendar calendar_id 등)의 누락 여부를 반드시 확인하십시오.
- 사용자가 리소스 ID를 프롬프트에 제공하지 않았다면, 절대로 임의의 빈 값(예: "", "YOUR_PAGE_ID")을 노드 config에 채워 완성형 워크플로우를 생성해서는 안 됩니다.
- 반드시 먼저 주입된 목록 조회 도구(notion_search, google_list_calendars 등)를 실행하여 사용자의 실제 리소스 목록을 조회하십시오.
- 조회된 목록을 제시하며 어느 리소스를 사용할 것인지 사용자에게 선택을 요청하되, 이때 응답 type은 CLARIFICATION_NEEDED로 지정하고 nodes와 edges는 null로 반환해야 합니다. 사용자가 특정 리소스를 선택하면, 그제서야 해당 ID를 노드 config에 주입한 완벽한 WORKFLOW_GENERATED 워크플로우를 반환하십시오.
</resource_rules>

<integration_rules>
- 지원 서비스: GOOGLE (Gmail, Google Drive, Google Sheets, Google Calendar), NOTION, SLACK, DISCORD, GITHUB (그 외 서비스는 지원하지 않음을 명확히 안내)
- 미연동 OAuth (GOOGLE, NOTION): type을 INTEGRATION_REQUIRED로 하고 actions에 연동 액션을 추가하십시오.
- Webhook 연동 (SLACK, DISCORD): actions를 비우고 텍스트 메시지로만 가이드를 안내하세요.
</integration_rules>

<flow_selection_rules>
1. Webhook 서비스(SLACK, DISCORD) credential이 여러 개인 경우 -> type: CLARIFICATION_NEEDED, 어느 채널/서버로 보낼지 되물음
2. 미연동 서비스가 요청에 포함된 경우 -> type: INTEGRATION_REQUIRED, nodes/edges: null
3. 지원되지 않는 서비스가 요청에 포함된 경우 -> type: CLARIFICATION_NEEDED, nodes/edges: null (지원되지 않음을 명확히 안내)
4. 요청이 불명확한 경우 -> type: CLARIFICATION_NEEDED, nodes/edges: null
5. 정상 신규 생성 -> type: WORKFLOW_GENERATED
6. 정상 수정 -> type: WORKFLOW_MODIFIED
</flow_selection_rules>
"""

_NODE_META_KEYS = {"id", "type", "nodeType", "label", "config"}


def _normalize_node(node: dict, index: int, preserve_id: bool = False) -> dict:
    """LLM이 생성한 노드를 정규화한다.

    - nodeType → type 변환
    - config 없이 루트에 펼쳐진 필드들을 config 객체로 모음
    - 노드 ID 자동 순차 부여 (node-1, node-2 등)
    - 빈 문자열 기본값 강제 (credentialId, access_token, parent_page_id 등)
    """
    node = dict(node)
    
    # 1. 노드 ID 규칙 자동 정렬
    if preserve_id:
        if "id" not in node or not node["id"]:
            node["id"] = f"node-{index}"
    else:
        node["id"] = f"node-{index}"

    # 2. nodeType → type
    if "nodeType" in node and "type" not in node:
        node["type"] = node.pop("nodeType")
    elif "nodeType" in node:
        node.pop("nodeType")

    # 3. config 구조화
    if "config" not in node:
        config = {k: v for k, v in node.items() if k not in _NODE_META_KEYS}
        for k in list(config.keys()):
            del node[k]
        node["config"] = config
    else:
        node["config"] = dict(node["config"])

    # 4. 기본값 보정 (Spring Boot 주입용 및 플레이스홀더 대체)
    node["config"]["credentialId"] = ""
    if "access_token" in node["config"]:
        node["config"]["access_token"] = ""
        
    for field in ["parent_page_id", "spreadsheet_id", "calendar_id"]:
        if field in node["config"] and not node["config"][field]:
            node["config"][field] = ""

    return node


def _validate_workflow(nodes: list, edges: list) -> None:
    valid_types = {"TRIGGER", "AI", "HTTP", "CONDITION", "TRANSFORM"}
    required_fields = {"id", "type", "label", "config"}

    ids = [n.get("id") for n in nodes]
    if len(ids) != len(set(ids)):
        raise ValueError(ErrorCode.WORKFLOW_PARSE_FAILED.message)

    for n in nodes:
        if n.get("type") not in valid_types:
            logger.error("유효하지 않은 노드 타입 발견: %s", n.get('type'))
            raise ValueError(ErrorCode.WORKFLOW_PARSE_FAILED.message)

    for n in nodes:
        if not required_fields.issubset(n.keys()):
            raise ValueError(ErrorCode.WORKFLOW_PARSE_FAILED.message)

    trigger_count = sum(1 for n in nodes if n.get("type") == "TRIGGER")
    if trigger_count != 1:
        raise ValueError(ErrorCode.WORKFLOW_PARSE_FAILED.message)

    node_ids = set(ids)
    for e in edges:
        if e.get("source") not in node_ids or e.get("target") not in node_ids:
            raise ValueError(ErrorCode.WORKFLOW_PARSE_FAILED.message)


async def _save_chat_log(
    prompt: str,
    provider: str,
    model: str,
    user_id: str,
    success: bool,
    duration_ms: int,
    raw_output: str | None = None,
    data: dict | None = None,
    nodes: list | None = None,
    edges: list | None = None,
    error_message: str | None = None,
) -> None:
    try:
        await chat_logs.insert_one({
            "prompt": prompt,
            "provider": provider,
            "model": model,
            "userId": user_id,
            "type": data.get("type") if data and success else None,
            "success": success,
            "nodeCount": len(nodes) if nodes else None,
            "edgeCount": len(edges) if edges else None,
            "rawOutput": raw_output,
            "errorMessage": error_message,
            "durationMs": duration_ms,
            "createdAt": datetime.now(timezone.utc),
        })
    except Exception:
        logger.warning("Failed to save chat execution log", exc_info=True)


def _bind_token(fn, **bound_args):
    """fn의 토큰 파라미터를 바인딩한 partial을 반환한다."""
    sig = inspect.signature(fn)
    p = functools.partial(fn, **bound_args)
    p.__name__ = fn.__name__
    p.__doc__ = fn.__doc__
    p.__signature__ = sig.replace(
        parameters=[v for k, v in sig.parameters.items() if k not in bound_args]
    )
    p.__annotations__ = {
        k: v for k, v in getattr(fn, "__annotations__", {}).items() if k not in bound_args
    }
    return p


async def chat_workflow(
    prompt: str,
    provider: str,
    api_key: str,
    user_id: str,
    available_integrations: list[dict],
    unavailable_integrations: list[dict],
    current_nodes: list | None = None,
    current_edges: list | None = None,
    notion_token: str | None = None,
    github_token: str | None = None,
    google_access_token: str | None = None,
    mcp_servers: list[dict] | None = None,
    preserve_id: bool | None = None,
) -> ChatResponse:
    start = time.monotonic()
    model = resolve_model(provider)
    env_key = resolve_env_key(provider)
    is_gemini = env_key == "GOOGLE_API_KEY" or not env_key
    lock = get_env_lock(env_key) if (env_key and not is_gemini) else None

    # 동적 주입 — 연동 현황
    available_text = ""
    for integration in available_integrations:
        if integration.get("credentials"):
            names = ", ".join(c["displayName"] for c in integration["credentials"])
            available_text += f"- {integration['provider']}: {names}\n"
        else:
            available_text += f"- {integration['provider']}\n"

    unavailable_text = ", ".join(u["provider"] for u in unavailable_integrations) or "없음"

    integration_section = f"""
## 사용자 연동 현황
사용 가능:
{available_text or '없음'}
사용 불가 (미연동): {unavailable_text}
"""

    workflow_section = ""
    if current_nodes:
        workflow_section = f"""
## 현재 워크플로우 (수정 요청)
{json.dumps({"nodes": current_nodes, "edges": current_edges}, ensure_ascii=False)}

수정 규칙:
1. 기존 노드 id 체계 유지. 새 노드는 가장 큰 번호 + 1로 부여
2. 수정되지 않은 노드는 그대로 유지
3. type은 반드시 WORKFLOW_MODIFIED
"""

    instruction = (
        _SYSTEM_PROMPT_BASE
        + integration_section
        + workflow_section
        + f"\n\n## Current Request Context\n- provider: {provider.upper()}\n  (모든 AI 노드의 llmProvider는 반드시 \"{provider.upper()}\"로 설정한다)"
    )

    async def _execute() -> str:
        prev_value = None
        if env_key and not is_gemini:
            prev_value = os.environ.get(env_key)
            os.environ[env_key] = api_key

        try:
            browse_tools = []
            if notion_token:
                browse_tools.append(FunctionTool(_bind_token(notion_search, token=notion_token)))
            if github_token:
                browse_tools.append(FunctionTool(_bind_token(github_list_orgs, token=github_token)))
                browse_tools.append(FunctionTool(_bind_token(github_list_repos, token=github_token)))
                browse_tools.append(FunctionTool(_bind_token(github_list_issues, token=github_token)))
                browse_tools.append(FunctionTool(_bind_token(github_list_pull_requests, token=github_token)))
            if google_access_token:
                browse_tools.append(FunctionTool(_bind_token(google_list_calendars, access_token=google_access_token)))
                browse_tools.append(FunctionTool(_bind_token(google_list_sheets, access_token=google_access_token)))

            async with contextlib.AsyncExitStack() as stack:
                if mcp_servers:
                    for mcp_cfg in mcp_servers:
                        params = SseConnectionParams(
                            url=mcp_cfg["server_url"],
                            headers=mcp_cfg.get("headers", {}),
                        )
                        mcp = MCPToolset(connection_params=params)
                        res = mcp.get_tools()
                        mcp_tools = await res if hasattr(res, "__await__") else res
                        
                        stack.push_async_callback(lambda m=mcp: _safe_close_mcp(m))
                        browse_tools.extend(mcp_tools)

                model_param = CustomGemini(model=model, api_key=api_key) if is_gemini else model

                agent = LlmAgent(
                    name="workflow_chat",
                    model=model_param,
                    instruction=instruction + get_current_time_info(),
                    tools=browse_tools,
                    output_schema=ChatResponseOutputSchema,
                )

                session_service = _get_session_service()
                runner = Runner(
                    agent=agent,
                    app_name="ieum-agent",
                    session_service=session_service,
                )

                session = await session_service.get_session(
                    app_name="ieum-agent",
                    user_id=user_id,
                    session_id=user_id,
                )
                if not session:
                    session = await session_service.create_session(
                        app_name="ieum-agent",
                        user_id=user_id,
                        session_id=user_id,
                    )

                message = types.Content(
                    role="user",
                    parts=[types.Part(text=prompt)],
                )

                output_parts = []
                async for event in runner.run_async(
                    user_id=user_id,
                    session_id=session.id,
                    new_message=message,
                ):
                    if event.is_final_response() and event.content:
                        for part in event.content.parts:
                            if hasattr(part, "text") and part.text:
                                output_parts.append(part.text)

                return "\n".join(output_parts) if output_parts else ""

        finally:
            if env_key and not is_gemini:
                if prev_value is None:
                    os.environ.pop(env_key, None)
                else:
                    os.environ[env_key] = prev_value

    raw_output = None
    if lock:
        async with lock:
            raw_output = await _execute()
    else:
        raw_output = await _execute()

    try:
        cleaned = raw_output.strip()

        if not cleaned:
            logger.error("LLM이 빈 응답을 반환했습니다. provider: %s", provider)
            raise ValueError("LLM이 빈 응답을 반환했습니다.")

        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
        if cleaned.rstrip().endswith("```"):
            cleaned = "\n".join(cleaned.rstrip().split("\n")[:-1])
        cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            logger.info("LLM이 일반 텍스트 응답을 반환하여 CLARIFICATION_NEEDED로 처리합니다.")
            response = ChatResponse(
                message=cleaned,
                type=ChatResponseType.CLARIFICATION_NEEDED,
                actions=[],
                nodes=None,
                edges=None,
                rawPrompt=prompt,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            await _save_chat_log(
                prompt=prompt,
                provider=provider,
                model=model,
                user_id=user_id,
                success=True,
                duration_ms=duration_ms,
                raw_output=raw_output,
                data={"type": "CLARIFICATION_NEEDED", "message": cleaned},
            )
            return response

        response_type = data.get("type")
        raw_nodes = data.get("nodes")
        raw_edges = data.get("edges")

        if raw_nodes:
            pid = preserve_id if preserve_id is not None else bool(current_nodes)
            
            # Build node ID conversion mapping
            id_mapping = {}
            normalized_nodes = []
            for idx, n in enumerate(raw_nodes):
                old_id = n.get("id")
                norm_node = _normalize_node(n, idx + 1, preserve_id=pid)
                new_id = norm_node.get("id")
                if old_id and old_id != new_id:
                    id_mapping[old_id] = new_id
                normalized_nodes.append(norm_node)
            raw_nodes = normalized_nodes

            # 1. Update edges' source/target node IDs
            if raw_edges and id_mapping:
                for e in raw_edges:
                    source = e.get("source")
                    target = e.get("target")
                    if source in id_mapping:
                        e["source"] = id_mapping[source]
                    if target in id_mapping:
                        e["target"] = id_mapping[target]

            # 2. Update variable reference syntax ({{nodes.old_id.output.field}})
            if id_mapping:
                import re
                def _replace_refs(val):
                    if isinstance(val, dict):
                        return {k: _replace_refs(v) for k, v in val.items()}
                    elif isinstance(val, list):
                        return [_replace_refs(v) for v in val]
                    elif isinstance(val, str):
                        for old, new in id_mapping.items():
                            pattern = r'\{\{\s*nodes\.' + re.escape(old) + r'\.output\.'
                            replacement = '{{nodes.' + new + '.output.'
                            val = re.sub(pattern, replacement, val)
                        return val
                    return val
                raw_nodes = _replace_refs(raw_nodes)

        if response_type in ("WORKFLOW_GENERATED", "WORKFLOW_MODIFIED"):
            if not raw_nodes:
                raise ValueError("WORKFLOW_GENERATED/MODIFIED 타입에는 nodes가 필요합니다.")
            _validate_workflow(raw_nodes, raw_edges or [])

        nodes = [WorkflowNode(**n) for n in raw_nodes] if raw_nodes else None
        edges = [WorkflowEdge(**e) for e in raw_edges] if raw_edges else None
        actions = [ChatAction(**a) for a in data.get("actions", [])]

        response = ChatResponse(
            message=data.get("message", ""),
            type=response_type,
            actions=actions,
            changeDescription=data.get("changeDescription"),
            nodes=nodes,
            edges=edges,
            rawPrompt=prompt,
            workflowName=data.get("workflowName"),
        )

        duration_ms = int((time.monotonic() - start) * 1000)
        await _save_chat_log(
            prompt=prompt,
            provider=provider,
            model=model,
            user_id=user_id,
            success=True,
            duration_ms=duration_ms,
            raw_output=raw_output,
            data=data,
            nodes=raw_nodes,
            edges=raw_edges,
        )

        return response

    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        await _save_chat_log(
            prompt=prompt,
            provider=provider,
            model=model,
            user_id=user_id,
            success=False,
            duration_ms=duration_ms,
            raw_output=raw_output,
            error_message=str(e),
        )

        logger.error("채팅 워크플로우 JSON 파싱 실패: %s\nraw_output: %s", str(e), raw_output)
        raise ValueError(ErrorCode.CHAT_PARSE_FAILED.message)

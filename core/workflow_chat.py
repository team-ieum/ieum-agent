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
from tools.discord import send_discord_webhook
from tools.slack import send_slack_message
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
당신은 IEUM 워크플로우를 설계 및 관리하는 AI 어시스턴트입니다.
사용자 요청에 따라 TRIGGER, AI, HTTP, CONDITION, TRANSFORM 노드로 구성된 최적의 워크플로우를 설계하십시오.

<workflow_design_rules>
1. 외부 연동은 AI 노드 전용 도구 사용:
   - Notion, Gmail, Slack, Discord, GitHub 등 외부 연동은 **절대로 HTTP 노드로 직접 구현하지 말고**, 반드시 도구를 매핑한 AI 노드(agentType: "react")를 통해 처리하십시오. (외부 알림/웹훅 발송이나 목록 조회 등 포함)
2. 노드 간 데이터 참조 및 데이터 무결성 보존:
   - 선행 노드의 결과는 반드시 이중 중괄호로 감싼 'nodes.노드ID.output.필드명' 형식(예: nodes.node-1.output.data를 이중 중괄호로 포장)으로 참조하십시오. 임의의 정의되지 않은 변수(예: today 등)를 날조해서 지어내지 마십시오.
   - [데이터 보존] 외부 데이터를 수집하는 조회 노드(예: 깃허브 PR 조회 등)는 원시 JSON 형태(예: pulls 등)를 요약/축소하지 말고 그대로 `output`으로 출력하게 prompt를 설계하십시오.
   - [가공 에이전트 위임] 데이터 요약, 날짜 포맷팅, JSON 파싱 등 데이터 변환 작업이 필요할 때는, 직접 가공하지 말고 `transform_agent` (혹은 `json_parse` 등의 도구)를 주입한 AI 노드에 가공 업무를 명시적으로 위임하십시오.
3. 생성 노드 프롬프트 경량화 지침:
   - 각 노드를 설계할 때 노드의 `systemMessage` 나 `prompt` 에 불필요한 사설이나 배경 설명을 과하게 채우지 말고, **핵심 지시사항(동작, 입력 참조값, 출력 형식 등) 위주로 최대 2~3문장 이내로만 간결하게 작성**하십시오. (실행 시 Latency 최적화 목적)
4. 다중 서비스 노드 분리:
   - 단일 노드가 다른 성격의 외부 서비스를 중복 호출하게 설계하지 말고, 서로 다른 외부 서비스 호출(예: 뉴스 수집과 노션 등록)은 항상 별도의 AI 노드로 명확히 분리하십시오.
5. 워크플로우 자동 이름 부여:
   - 신규 생성(WORKFLOW_GENERATED) 시에만 목적을 명확히 대변하는 한국어 이름(예: 'IT 트렌드 자동 노션 요약')을 지어 'workflowName' 필드에 기입하고, 수정 시에는 null로 비워두십시오.
</workflow_design_rules>

<resource_rules>
1. 리소스 ID 확인 및 자동 매핑:
   - Notion `parent_page_id`, Sheets `spreadsheet_id` 등의 리소스 ID가 누락되었을 경우, 먼저 바인딩된 목록 조회 도구(notion_search 등)를 실행하여 실제 목록을 조회하십시오.
   - 조회 목록 중 사용자가 기입하려 하거나 워크플로우 목적에 가장 잘 부합하는 최적의 상위 리소스(예: '요약 보고서', 'IEUM' 등의 노션 페이지)가 매칭되면, 되묻지 않고 해당 리소스 ID를 노드 config에 자동으로 기입하여 완성형 워크플로우(WORKFLOW_GENERATED)를 제공하십시오.
   - 매칭이 애매하거나 없을 때만 선택지 목록을 제시하고 `CLARIFICATION_NEEDED` 유형으로 되물으십시오.
</resource_rules>

<integration_rules>
- 지원 범위: GOOGLE(Gmail, Drive, Sheets, Calendar), NOTION, SLACK, DISCORD, GITHUB (이외의 타 플랫폼 요청은 미지원 안내)
- 미연동 상태: OAuth 미연동은 INTEGRATION_REQUIRED로 변환해 actions를 추가하고, Slack/Discord 등 웹훅 연동은 actions 없이 텍스트 메시지로만 가이드를 제공하십시오.
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
            browse_tools = [
                FunctionTool(send_discord_webhook),
                FunctionTool(send_slack_message),
            ]
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

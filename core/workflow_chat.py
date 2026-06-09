import asyncio
import contextlib
import copy
import functools
import inspect
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, List, Optional
from pydantic import BaseModel, Field

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from db.session_service import MongoSessionService
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, SseConnectionParams
from google.genai import types

from api.schemas.chat import ChatResponse, ChatResponseType, ChatAction, ClarificationOption
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
from core.validators.workflow_validator import WorkflowValidator
from core.template_registry import hydrate_node, dehydrate_nodes, slot_catalog_text
from tools.registry import apply_service_brand

logger = logging.getLogger(__name__)

_SESSION_SERVICE = None


def _extract_json(text: str) -> dict | None:
    """LLM 출력 텍스트에서 JSON 객체를 추출한다.

    코드펜스 제거 → 직접 파싱 → prefix 텍스트 건너뛰어 첫 '{' 부터 파싱 순으로 시도.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[1:])
    if cleaned.rstrip().endswith("```"):
        cleaned = "\n".join(cleaned.rstrip().split("\n")[:-1])
    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        pass

    # LLM이 JSON 뒤에 설명 텍스트를 덧붙이는 경우가 있으므로, 첫 '{' 부터 중괄호 쌍이
    # 맞는 지점까지만 잘라 파싱한다(문자열 내부 중괄호/이스케이프는 무시).
    start = cleaned.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
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
                try:
                    return json.loads(cleaned[start:i + 1])
                except (json.JSONDecodeError, ValueError):
                    break

    return None

def _get_session_service():
    # MongoDB 기반 영속 세션. workflow_id로 키된 멀티턴 대화가 프로세스 재시작/리로드에도 유지된다.
    # (InMemorySessionService는 리로드 시 손실 + 다중 인스턴스 간 공유 불가)
    global _SESSION_SERVICE
    if _SESSION_SERVICE is None:
        _SESSION_SERVICE = MongoSessionService()
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
    options: List[ClarificationOption] = Field(
        default=[],
        description="CLARIFICATION_NEEDED 시 사용자가 고를 선택지(GitHub repo, 웹훅 등). 그 외에는 빈 배열"
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


class WorkflowReviewResult(BaseModel):
    """지능형 검증 레이어의 리뷰 결과 규격"""
    isValid: bool = Field(
        description="설계된 워크플로우가 설계 규칙(Data Link, HTTP 노드 지양, 리소스 ID 포함 등) 및 사용자 요구사항에 무결한지 여부"
    )
    feedback: Optional[str] = Field(
        default=None,
        description="오류나 개선이 필요한 사항에 대한 상세한 피드백 설명 (isValid가 false일 때 필수 기입, 통과 시 null)"
    )


_REVIEWER_SYSTEM_PROMPT = """\
당신은 IEUM 워크플로우 설계를 평가하고 검증하는 전문 AI 리뷰어(검증 레이어)입니다.
사용자 요청(User Request)과 에이전트가 설계한 워크플로우 초안(Draft Workflow)을 대조하여, 결함이 있는지 엄격하게 평가하십시오.

<verification_checklist>
1. 외부 연동은 AI 노드 전용 도구 사용 여부:
   - Notion, Gmail, Slack, Discord, GitHub 등 외부 서비스 연동에 **절대로 HTTP 노드가 사용되어서는 안 됩니다.** 반드시 도구를 바인딩한 AI 노드로 작성되었는지 확인하십시오. (예: Discord 전송은 HTTP API가 아닌 send_discord_webhook 도구가 주입된 AI 노드여야 함)
2. 노드 간 데이터 참조 정합성:
   - 이전 노드 참조(예: nodes.node-1.output.data를 이중 중괄호로 감싼 형태)가 올바른 선행 노드 ID를 가리키고 있는지 대조하십시오.
   - 정의되지 않은 임의의 시스템 변수(예: today, current_date 등)를 날조해서 사용하고 있지 않은지 감시하십시오.
3. 데이터 훼손 방지 및 흐름 적절성:
   - 깃허브나 노션 조회 노드의 prompt가 데이터 값을 자연어로 마음대로 훼손/왜곡하지 않으면서도, 후속 노드가 실제 사용하는 필드만 추출하도록 가이드되었는지 확인하십시오. 전체 원시 JSON을 통째로 덤프하게 설계되어 있다면 결함입니다(거대 출력은 실행 타임아웃 유발). 가능하면 조회 단계 server-side 필터(state/날짜 범위/개수 제한)와 후속 필터의 선반영이 포함되어 있는지 확인하십시오.
   - 원시 데이터를 정제하거나 마크다운 형식으로 작성할 때, 별도 TRANSFORM 노드 또는 별도 AI 노드(prompt 위임)를 거치도록 설계되었는지 확인하십시오.
   - [tools 작성 규칙] AI 노드의 `tools`에는 빌트인 도구 키(`slack`, `discord`, `gmail`, `builtin:...`)만 허용됩니다. GitHub 조회·데이터 가공 등은 실행 시 서브 에이전트가 자동 처리하므로 해당 노드의 `tools`는 비어 있는([]) 것이 **정상**입니다. `github_list_pull_requests`·`builtin:github_*`·`transform_agent` 같은 이름이 `tools`에 들어 있다면 오히려 결함이며, GitHub/가공 노드에 도구가 없다고 해서 "도구 누락"으로 지적하지 마십시오.
4. 리소스 ID 주입:
   - Notion parent_page_id 등 워크플로우 작동에 필요한 리소스 ID들이 빈 값("")이 아닌 유효한 조회 ID로 매핑되어 있는지 확인하십시오.
5. SCHEDULE 트리거의 cron 필드 검증:
   - 만약 TRIGGER 노드의 `config.triggerType`이 "SCHEDULE"인 경우, `config.cron` 필드가 반드시 존재하고 비어있지 않아야 하며, 5필드 표준 크론 표현식(예: "0 17 * * 5") 형식인지 확인하십시오. 누락되었거나 형식이 잘못되었다면 isValid를 false로 하고 피드백을 반환하십시오.
</verification_checklist>

설계 초안에 결함이나 규칙 위반이 존재한다면 isValid를 false로 하고, 피드백(feedback) 필드에 구체적으로 어떤 부분을 어떻게 수정해야 하는지 피드백 메시지를 상세히 작성하여 반환하십시오.
모든 체크리스트가 완벽히 통과되고 설계상 오류가 전혀 없다면 isValid를 true, feedback을 null로 반환하십시오.
"""


_SYSTEM_PROMPT_BASE = """\
당신은 IEUM 워크플로우를 설계 및 관리하는 AI 어시스턴트입니다.
사용자 요청에 따라 TRIGGER, AI, HTTP, CONDITION, TRANSFORM 노드로 구성된 최적의 워크플로우를 설계하십시오.

<workflow_design_rules>
당신은 노드 구조를 직접 설계하지 않습니다. 아래 '노드 템플릿 카탈로그'에서 각 노드의 templateId를 고르고,
그 템플릿이 정의한 슬롯(slots)만 채웁니다. 노드의 타입·도구·고정 설정은 템플릿이 결정합니다.
provider 슬롯(llmProvider 등 '자동주입' 표기)은 시스템이 채우므로 작성하지 않습니다.

1. 외부 연동(Notion/Gmail/Slack/Discord/GitHub 등)은 해당 서비스의 ai.* 템플릿을 선택합니다. http 템플릿으로 직접 호출하지 않습니다.
2. 노드 간 데이터 참조 및 무결성:
   - prompt 등 슬롯 값에서 선행 노드 결과는 이중 중괄호 'nodes.노드ID.output.필드명' 형식만 사용합니다. 정의되지 않은 변수를 날조하지 마십시오.
   - [필드명 규약] 노드 타입별 실제 출력 필드만 참조합니다(임의 필드명 results/content/data 금지):
     · AI 노드 결과 → `output.output` (예: 이중 중괄호로 nodes.node-2.output.output)
     · HTTP → `output.body`, `output.statusCode`   · TRIGGER(SCHEDULE) → `output.triggeredAt`
     · TRANSFORM → 그 노드 매핑에서 정의한 키
   - [참조 전용] 이중 중괄호 안에는 'nodes.노드ID.output.필드명'만 허용됩니다. `{{#each}}`, `{{formatDate now}}`, `{{this.필드}}` 같은 헬퍼·함수·반복문은 **금지**입니다(엔진에 함수 없음). 날짜 삽입·반복·포맷팅이 필요하면 prompt에 자연어로 지시합니다.
   - [데이터 보존·출력 최소화] 조회 노드 prompt는 후속 노드가 실제 쓰는 필드만 추출하도록 지시합니다. 전체 raw JSON 덤프 금지(타임아웃 유발), 임의 요약/왜곡 금지. 목록 조회(깃허브 PR/이슈, 노션 검색 등)는 반드시 단일 페이지·개수 상한을 명시합니다("최신순 1페이지(per_page=30, page=1)만 조회"). 날짜 필터는 그 1페이지 결과에 적용합니다.
   - [데이터 가공 위임] 요약·날짜 포맷·JSON 파싱 등 변환은 transform 템플릿 또는 별도 AI 노드 prompt에 위임합니다(실행 시 서브 에이전트 자동 처리).
3. prompt 슬롯은 핵심 지시(동작·입력 참조·출력 형식) 위주 2~3문장 이내로 간결히 작성합니다.
4. 서로 다른 외부 서비스 작업은 항상 별도 노드(별도 templateId)로 분리합니다.
4-1. [요약·가공과 발송·저장 분리] 발송/저장 노드(ai.slack_send / ai.discord_send / ai.gmail_send /
   ai.notion_create_page 등)에서 콘텐츠를 직접 요약·분석·포맷하지 마십시오. 요약/판단/정리가 필요하면
   선행에 ai.reasoning(순수 추론) 또는 transform 노드를 두고, 발송/저장 노드는 그 결과를
   {{nodes.<id>.output.output}}로 참조해 전달만 하도록 노드를 나눕니다.
   (예: ai.web_search → ai.reasoning(요약) → ai.discord_send(발송만))
   - [발송 prompt 명령형] 발송/저장 노드의 prompt는 **참조식만 단독으로 두지 마십시오.** 반드시 발송·저장
     지시문과 보낼 내용 참조를 함께 씁니다. (예: "다음 내용을 디스코드로 보내줘: {{nodes.node-3.output.output}}")
5. 신규 생성(WORKFLOW_GENERATED) 시에만 목적을 대변하는 한국어 이름을 'workflowName'에 기입하고, 수정 시에는 null로 둡니다.
6. 모든 워크플로우는 1개의 TRIGGER 템플릿(trigger.manual / trigger.schedule / trigger.webhook)으로 시작합니다.
   trigger.schedule을 고르면 cron 슬롯에 5필드 표준 크론 표현식을 채웁니다(예: "매일 오전 9시" -> "0 9 * * *").
</workflow_design_rules>

<resource_rules>
1. 리소스 식별자 절대 가정 금지 (최우선):
   - GitHub `owner/repo`, Notion 페이지/DB, Google Sheets ID, 채널 등 사용자가 명시하지 않은
     리소스 식별자를 **임의로 추측하거나 가정해서 채우지 마십시오.** (예: 'IEUM/ieum-backend'처럼
     존재 여부를 모르는 값을 가정하는 것은 금지)
2. 리소스 ID 확인 및 자동 매핑:
   - Notion `parent_page_id`, Sheets `spreadsheet_id`, GitHub `owner/repo` 등이 누락되었을 경우,
     먼저 바인딩된 목록 조회 도구(notion_search, github_list_repos 등)를 실행하여 실제 목록을 조회하십시오.
   - 조회 목록 중 워크플로우 목적에 가장 잘 부합하는 항목이 명확히 매칭되면, 되묻지 않고 해당
     리소스 ID/이름을 노드의 prompt 슬롯에 자연어로 기입하여 완성형 워크플로우(WORKFLOW_GENERATED)를 제공하십시오.
   - 미연동이라 조회 도구가 없는 경우(예: GitHub 토큰 없음)에는 가정하지 말고 `INTEGRATION_REQUIRED`
     유형으로 해당 서비스 연동을 요청하십시오. (actions에 {type: OAUTH, provider: <서비스>} 추가)
   - 연동은 되어 있으나 후보가 여러 개이거나 매칭이 애매하면, **가정하지 말고** `CLARIFICATION_NEEDED`
     유형으로 되묻되, 조회한 후보를 `options` 배열에 채워 사용자가 고르게 하십시오.
     (예: GitHub repo 선택 → options: [{"value":"owner/repo-a","label":"repo-a"}, ...])
     message에는 무엇을 선택해야 하는지 안내하고, 구체 후보는 options로 제공합니다.
   - **options는 최대 7개까지만** 담습니다. 후보가 많으면(예: 저장소 수십 개) 전부 나열하지 말고,
     요청 맥락·최근 활동(github_list_repos는 최신 업데이트순) 기준으로 가장 관련성 높은 상위 후보만
     추리십시오. 그리고 message에 "원하는 저장소가 없으면 'owner/repo' 형식으로 직접 입력해 주세요"처럼
     직접 입력 안내를 덧붙입니다.
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
7. 직전 턴에서 CLARIFICATION_NEEDED로 되물었고(예: GitHub repo 선택) 사용자가 그 선택값(예: 'owner/repo')만
   답한 경우 -> **다시 되묻지 말고**, 대화 기록의 원래 요청을 그 선택값으로 보완하여 워크플로우를 끝까지
   완성하십시오(type: WORKFLOW_GENERATED). 원래 요청의 나머지 단계(예: Notion 저장, Discord 알림)를 빠뜨리지 마십시오.
</flow_selection_rules>
"""

# Designer는 output_schema 대신 instruction으로 출력 형식을 강제한다(도구 사용 + Gemini 호환 위함).
# ChatResponseOutputSchema와 동일한 JSON 봉투를 자연어 스펙으로 명시한다.
_OUTPUT_FORMAT_SPEC = """\

<output_format>
반드시 아래 JSON 객체 **하나만** 출력하십시오. 코드펜스(```)나 JSON 밖의 설명 문장을 절대 포함하지 마십시오.
{
  "message": "사용자에게 전달할 대화 메시지(사용자 요청 언어와 동일)",
  "type": "WORKFLOW_GENERATED | WORKFLOW_MODIFIED | INTEGRATION_REQUIRED | CLARIFICATION_NEEDED",
  "actions": [],
  "options": [],
  "changeDescription": null,
  "nodes": [ /* 생성/수정된 노드 draft 목록. 각 노드는 {"id","templateId","slots"} 형식. 그 외에는 null */ ],
  "edges": [ /* 생성/수정된 엣지 목록(source/target, 분기는 conditionType). 그 외에는 null */ ],
  "workflowName": null
}
- nodes 각 항목은 draft 형식이다: {"id": "node-1", "templateId": "<카탈로그의 templateId>", "slots": { ... }}.
  슬롯은 해당 templateId가 정의한 것만 채우고(없는 슬롯 키 금지), provider 슬롯('자동주입')은 작성하지 않는다.
- actions: OAuth 연동이 추가로 필요할 때(INTEGRATION_REQUIRED)만 채우고, 그 외에는 빈 배열([]).
- options: CLARIFICATION_NEEDED로 사용자에게 선택을 요청할 때만 채운다(예: GitHub repo 후보, 웹훅 후보).
  각 항목은 {"value": "선택 시 사용할 값", "label": "사용자에게 보일 이름", "description": null} 형식이다.
  그 외(생성/수정/연동요청)에는 빈 배열([]).
- changeDescription: 워크플로우 수정(WORKFLOW_MODIFIED) 시 변경 요약 한 문장, 그 외 null.
- nodes/edges: 생성/수정 시에만 채우고, INTEGRATION_REQUIRED/CLARIFICATION_NEEDED일 때는 null.
- workflowName: 신규 생성(WORKFLOW_GENERATED) 시에만 간결한 한국어 이름, 그 외 null.
- 리소스 ID(parent_page_id, owner/repo 등)가 필요하면 바인딩된 조회 도구(notion_search 등)로 실제 ID를 찾아 prompt 슬롯에 자연어로 기입한다. 못 찾으면 비운다.
</output_format>
"""


# 설계(Designer) 단계에서 모델이 호출하면 안 되는 발송/변경성 도구. 조회 도구만 허용한다.
_DESIGNER_EXCLUDED_TOOLS = {"send_slack_message", "send_discord_webhook"}

# CLARIFICATION_NEEDED options 최대 개수(UI 과부하 방지 하드 캡). 초과분은 잘리며 사용자는 직접 입력 가능.
_MAX_CLARIFICATION_OPTIONS = 8

# 정적 검증(WorkflowValidator) 실패 시 Designer에 오류를 피드백해 재생성하는 최대 횟수.
_MAX_VALIDATION_RETRIES = 2


_WEBHOOK_TOOL_NAMES = {"slack", "discord"}


def _strip_invalid_webhook_credentials(nodes: list, allowed_webhook_credential_ids: set | None = None) -> None:
    """하이드레이션된 slack/discord 노드의 webhookCredentialId가 보유 자격증명에 없으면 제거한다(in-place).

    webhookCredentialId는 슬롯으로 config.tools[0].config.webhookCredentialId에 주입된다. Designer가
    환각으로 채운 id를 검증 전에 떼어내, 정상 워크플로우가 하드 실패하지 않게 한다(도구 자체는 유지 —
    webhook_url은 실행 시 backend가 주입). mcp catalogId는 WorkflowValidator가 별도 검증한다."""
    allowed = allowed_webhook_credential_ids or set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        cfg = node.get("config")
        tools = cfg.get("tools") if isinstance(cfg, dict) else None
        if not isinstance(tools, list):
            continue
        for tool in tools:
            if not (isinstance(tool, dict) and tool.get("name") in _WEBHOOK_TOOL_NAMES):
                continue
            tcfg = tool.get("config")
            if isinstance(tcfg, dict) and tcfg.get("webhookCredentialId") not in allowed:
                tcfg.pop("webhookCredentialId", None)


# 공통 WorkflowValidator 사용으로 대체됨


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


def _emit_stage(on_stage: Callable[[str], None] | None, stage: str) -> None:
    """진행 단계 콜백을 안전하게 호출한다.

    스트리밍(/v1/chat/stream) 경로에서만 on_stage가 전달되며, 블로킹(/v1/chat) 경로는
    None이라 아무 동작도 하지 않는다. 콜백 실패가 설계 로직을 막지 않도록 예외는 무시한다."""
    if on_stage is None:
        return
    try:
        on_stage(stage)
    except Exception:
        logger.warning("[chat-stream] on_stage 콜백 실패 — stage: %s", stage, exc_info=True)


async def chat_workflow(
    prompt: str,
    provider: str,
    api_key: str,
    user_id: str,
    available_integrations: list[dict],
    unavailable_integrations: list[dict],
    workflow_id: str | None = None,
    current_nodes: list | None = None,
    current_edges: list | None = None,
    notion_token: str | None = None,
    github_token: str | None = None,
    google_access_token: str | None = None,
    mcp_servers: list[dict] | None = None,
    available_mcp_servers: list | None = None,
    available_webhooks: list | None = None,
    preserve_id: bool | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> ChatResponse:
    start = time.monotonic()

    # 생성/수정 단계에서 허용되는 MCP 카탈로그 ID 집합. 카탈로그가 없으면 MCP는 전면 차단된다.
    allowed_mcp_catalog_ids = {
        (m.get("catalogId") if isinstance(m, dict) else getattr(m, "catalogId", None))
        for m in (available_mcp_servers or [])
    }
    allowed_mcp_catalog_ids.discard(None)

    # 생성/수정 단계에서 허용되는 webhook 자격증명 ID 집합. 없으면 webhookCredentialId 미배정.
    allowed_webhook_credential_ids = {
        (w.get("webhookCredentialId") if isinstance(w, dict) else getattr(w, "webhookCredentialId", None))
        for w in (available_webhooks or [])
    }
    allowed_webhook_credential_ids.discard(None)
    model = resolve_model(provider)
    env_key = resolve_env_key(provider)
    is_gemini = (env_key == "GOOGLE_API_KEY") or (not env_key and "gemini" in model.lower())
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
        # 저장된 full-node를 draft(templateId+slots)로 역변환해 주입한다. Designer는 draft로 편집한다.
        current_drafts = dehydrate_nodes(current_nodes)
        workflow_section = f"""
## 현재 워크플로우 (수정 요청)
{json.dumps({"nodes": current_drafts, "edges": current_edges}, ensure_ascii=False)}

수정 규칙:
1. 기존 노드 id 체계 유지. 새 노드는 가장 큰 번호 + 1로 부여
2. 수정되지 않은 노드는 그대로 유지(같은 templateId·slots)
3. type은 반드시 WORKFLOW_MODIFIED
"""

    from core.skill_loader import format_mcp_catalog, format_webhook_catalog
    catalog = slot_catalog_text()
    mcp_catalog_section = format_mcp_catalog(available_mcp_servers)
    webhook_catalog_section = format_webhook_catalog(available_webhooks)

    instruction = (
        _SYSTEM_PROMPT_BASE
        + f"\n\n## 노드 템플릿 카탈로그 (templateId + 채울 슬롯)\n{catalog}"
        + (f"\n\n{mcp_catalog_section}" if mcp_catalog_section else "")
        + (f"\n\n{webhook_catalog_section}" if webhook_catalog_section else "")
        + integration_section
        + workflow_section
        + f"\n\n## Current Request Context\n- provider: {provider.upper()}\n  (모든 AI 노드의 llmProvider는 반드시 \"{provider.upper()}\"로 설정한다)"
    )

    def _prepare_nodes(data: dict):
        """LLM 출력 draft(nodes)를 템플릿으로 하이드레이션하고 노드 ID 재부여 + 참조식/엣지 리맵을 적용한다.
        외부 응답 빌드와 정적 검증 사전점검이 동일 로직을 공유하도록 추출했다.
        하이드레이션 실패(SlotFillError 등)는 호출부(try/except)에서 처리된다."""
        raw_drafts = data.get("nodes")
        raw_edges = data.get("edges")
        if not raw_drafts:
            return raw_drafts, raw_edges

        pid = preserve_id if preserve_id is not None else bool(current_nodes)
        id_mapping = {}
        raw_nodes = []
        for idx, draft in enumerate(raw_drafts):
            old_id = draft.get("id") if isinstance(draft, dict) else None
            node = hydrate_node(draft, provider=provider)
            new_id = old_id if (pid and old_id) else f"node-{idx + 1}"
            node["id"] = new_id
            if old_id and old_id != new_id:
                id_mapping[old_id] = new_id
            raw_nodes.append(node)

        if raw_edges and id_mapping:
            for e in raw_edges:
                if e.get("source") in id_mapping:
                    e["source"] = id_mapping[e["source"]]
                if e.get("target") in id_mapping:
                    e["target"] = id_mapping[e["target"]]

        if id_mapping:
            def _replace_refs(val):
                if isinstance(val, dict):
                    return {k: _replace_refs(v) for k, v in val.items()}
                elif isinstance(val, list):
                    return [_replace_refs(v) for v in val]
                elif isinstance(val, str):
                    for old, new in id_mapping.items():
                        pattern = r'\{\{\s*nodes\.' + re.escape(old) + r'\.output\.'
                        val = re.sub(pattern, '{{nodes.' + new + '.output.', val)
                    return val
                return val
            raw_nodes = _replace_refs(raw_nodes)

        return raw_nodes, raw_edges

    def _static_validation_error(output_str: str) -> str | None:
        """후보 출력이 정적 검증(WorkflowValidator)을 통과하는지 확인한다.
        통과하면 None, 실패하면 사람이 읽을 수 있는 오류 메시지를 반환한다(부작용 없음 — deepcopy 사용).
        GENERATED/MODIFIED가 아니거나 JSON이 아니면 검증 대상이 아니므로 None."""
        data = _extract_json(output_str)
        if not data or data.get("type") not in ("WORKFLOW_GENERATED", "WORKFLOW_MODIFIED"):
            return None
        data = copy.deepcopy(data)
        try:
            raw_nodes, raw_edges = _prepare_nodes(data)
            if not raw_nodes:
                return "WORKFLOW_GENERATED/MODIFIED 타입에는 nodes가 필요합니다."
            apply_service_brand(raw_nodes)
            _strip_invalid_webhook_credentials(raw_nodes, allowed_webhook_credential_ids)
            WorkflowValidator.validate(raw_nodes, raw_edges or [], allowed_mcp_catalog_ids)
        except Exception as e:
            return str(e)
        return None

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

                session_service = _get_session_service()

                # 1. Designer Agent (Generator) 선언
                # output_schema는 사용하지 않는다. 이유:
                #  (1) ADK 제약상 output_schema가 설정되면 도구를 전혀 호출할 수 없어
                #      resource_rules의 notion_search 기반 리소스 ID 해소가 불가능해진다.
                #  (2) Gemini API는 response_schema에서 additionalProperties(자유형 config dict)를
                #      지원하지 않아 ChatResponseOutputSchema를 그대로 쓰면 요청 빌드가 실패한다.
                # 따라서 도구를 활성화하고 출력은 instruction의 <output_format>으로 강제한 뒤,
                # 아래에서 JSON을 직접 파싱한다(generate 경로와 동일 방식).
                # 단, 설계 단계에서는 조회 도구만 허용한다. send_slack/discord 같은 발송 도구를
                # 주면 모델이 워크플로우를 짜는 도중 실제 메시지를 보낼 수 있으므로 제외한다.
                designer_tools = [
                    t for t in browse_tools
                    if getattr(t, "name", "") not in _DESIGNER_EXCLUDED_TOOLS
                ]
                designer_instruction = instruction + _OUTPUT_FORMAT_SPEC + get_current_time_info()
                designer_agent = LlmAgent(
                    name="workflow_designer",
                    model=model_param,
                    instruction=designer_instruction,
                    tools=designer_tools,
                )
                
                # 2. Reviewer Agent 선언
                reviewer_agent = LlmAgent(
                    name="workflow_reviewer",
                    model=model_param,
                    instruction=_REVIEWER_SYSTEM_PROMPT,
                    output_schema=WorkflowReviewResult,
                )

                # designer 실행을 위한 runner
                designer_runner = Runner(
                    agent=designer_agent,
                    app_name="ieum-agent",
                    session_service=session_service,
                )

                # reviewer 실행을 위한 runner
                reviewer_runner = Runner(
                    agent=reviewer_agent,
                    app_name="ieum-agent",
                    session_service=session_service,
                )

                # 세션 격리: workflow_id가 있으면 워크플로우별 멀티턴 세션을 이어가고,
                # 없으면(신규 생성) 매 요청 고유 세션을 만들어 종료 시 폐기한다.
                # (과거 user_id 단일 세션은 서로 다른 워크플로우/요청 간 대화가 섞이는 원인이었다.)
                ephemeral_session = workflow_id is None
                if ephemeral_session:
                    session_id = f"{user_id}-{uuid.uuid4().hex}"
                    session = await session_service.create_session(
                        app_name="ieum-agent",
                        user_id=user_id,
                        session_id=session_id,
                    )
                else:
                    session_id = f"{user_id}-{workflow_id}"
                    session = await session_service.get_session(
                        app_name="ieum-agent",
                        user_id=user_id,
                        session_id=session_id,
                    )
                    if not session:
                        session = await session_service.create_session(
                            app_name="ieum-agent",
                            user_id=user_id,
                            session_id=session_id,
                        )
                if ephemeral_session:
                    stack.push_async_callback(
                        lambda sid=session.id: session_service.delete_session(
                            app_name="ieum-agent", user_id=user_id, session_id=sid
                        )
                    )

                # Step 1: 워크플로우 설계 초안 생성 (Designer)
                async def _run_designer_once() -> str:
                    """Designer를 1회 실행해 최종 텍스트를 반환한다. parts가 None인 이벤트는 건너뛴다."""
                    message = types.Content(role="user", parts=[types.Part(text=prompt)])
                    parts_out = []
                    kinds = []
                    async for event in designer_runner.run_async(
                        user_id=user_id, session_id=session.id, new_message=message,
                    ):
                        if event.is_final_response() and event.content and event.content.parts:
                            for part in event.content.parts:
                                if hasattr(part, "text") and part.text:
                                    parts_out.append(part.text)
                                elif getattr(part, "function_call", None) is not None:
                                    kinds.append(f"function_call:{getattr(part.function_call, 'name', '?')}")
                                else:
                                    kinds.append("non_text")
                    text = "\n".join(parts_out) if parts_out else ""
                    logger.warning(
                        "[chat-debug] designer draft(len=%d) non_text_parts=%s preview=%r",
                        len(text), kinds, text[:800],
                    )
                    return text

                async def _run_designer(message_text: str) -> str:
                    """임의의 지시 텍스트로 Designer를 1회 실행해 최종 텍스트를 반환한다(자가교정 재생성용)."""
                    message = types.Content(role="user", parts=[types.Part(text=message_text)])
                    parts_out = []
                    async for event in designer_runner.run_async(
                        user_id=user_id, session_id=session.id, new_message=message,
                    ):
                        if event.is_final_response() and event.content and event.content.parts:
                            for part in event.content.parts:
                                if hasattr(part, "text") and part.text:
                                    parts_out.append(part.text)
                    return "\n".join(parts_out) if parts_out else ""

                # Gemini가 함수 호출 후 빈 텍스트를 반환하는 경우가 있어, 빈 응답이면 1회 재시도한다.
                _emit_stage(on_stage, "designing")
                draft_output = await _run_designer_once()
                if not draft_output.strip():
                    logger.warning("[chat-debug] designer 빈 응답 — 1회 재시도합니다.")
                    draft_output = await _run_designer_once()

                # 재시도 후에도 비어 있으면 502 대신 CLARIFICATION_NEEDED로 우아하게 안내한다.
                if not draft_output.strip():
                    logger.warning("[chat-debug] designer 재시도 후에도 빈 응답 — CLARIFICATION 폴백.")
                    draft_output = json.dumps({
                        "message": "요청을 처리하지 못했습니다. 조금 더 구체적으로 다시 말씀해 주시겠어요?",
                        "type": "CLARIFICATION_NEEDED",
                        "actions": [], "options": [],
                        "changeDescription": None, "nodes": None, "edges": None,
                        "workflowName": None,
                    }, ensure_ascii=False)

                # 초안이 JSON 인지 체크 및 응답 타입 파악
                draft_data = _extract_json(draft_output)
                response_type = draft_data.get("type") if draft_data else None
                cleaned_draft = json.dumps(draft_data, ensure_ascii=False) if draft_data else draft_output

                candidate = draft_output

                # 신규 생성 또는 수정인 경우에만 지능형 검증(Reviewer) 가동
                if response_type in ("WORKFLOW_GENERATED", "WORKFLOW_MODIFIED"):
                    # Step 2: 설계 초안 검증 (Reviewer)
                    _emit_stage(on_stage, "reviewing")
                    review_prompt = (
                        f"Original User Request: {prompt}\n\n"
                        f"Drafted Workflow Configs:\n{cleaned_draft}"
                    )
                    review_message = types.Content(
                        role="user",
                        parts=[types.Part(text=review_prompt)],
                    )

                    review_parts = []
                    async for event in reviewer_runner.run_async(
                        user_id=user_id,
                        session_id=session.id,
                        new_message=review_message,
                    ):
                        if event.is_final_response() and event.content and event.content.parts:
                            for part in event.content.parts:
                                if hasattr(part, "text") and part.text:
                                    review_parts.append(part.text)

                    review_output = "\n".join(review_parts) if review_parts else ""

                    review_data = _extract_json(review_output)
                    if review_data:
                        is_valid = review_data.get("isValid", True)
                        feedback = review_data.get("feedback")
                    else:
                        logger.warning("검증 레이어 응답 파싱 실패, 기본값으로 통과 처리합니다.")
                        is_valid = True
                        feedback = None

                    # Step 3: 결함 발견 시 피드백 기반 1회 자가 교정 (Reviewer Self-Correction Loop)
                    if not is_valid and feedback:
                        logger.info("검증 레이어 결함 발견! 자가 교정을 시도합니다. 피드백: %s", feedback)
                        correction_prompt = (
                            f"당신이 이전에 작성한 워크플로우 설계 초안에 결함이 발견되었습니다.\n"
                            f"아래 피드백 내용을 엄격하게 수용하여, 오류를 수정하고 완성된 새로운 워크플로우를 재생성하십시오.\n\n"
                            f"## 검증 피드백:\n{feedback}\n\n"
                            f"## 이전 설계 초안:\n{cleaned_draft}"
                        )
                        corrected = await _run_designer(correction_prompt)
                        if corrected.strip():
                            candidate = corrected

                # Step 4: 정적 검증(WorkflowValidator) 기반 자가 교정 루프.
                # Reviewer(LLM)가 못 잡는 결정론적 오류(잘못된 변수 참조 문법, config 필드 환각,
                # enum 위반 등)를 validator가 잡으면, 그 오류 메시지를 Designer에 피드백해 재생성한다.
                for attempt in range(_MAX_VALIDATION_RETRIES):
                    err = _static_validation_error(candidate)
                    if err is None:
                        break
                    logger.info(
                        "[chat] 정적 검증 실패(시도 %d/%d) — 자가 교정 재생성. error: %s",
                        attempt + 1, _MAX_VALIDATION_RETRIES, err,
                    )
                    _emit_stage(on_stage, "designing")
                    fix_prompt = (
                        "당신이 작성한 워크플로우가 정적 검증(WorkflowValidator)에서 거부되었습니다.\n"
                        "아래 오류를 반드시 해소하여 완전한 워크플로우 JSON을 재생성하십시오.\n"
                        "특히 이중 중괄호는 '{{nodes.노드ID.output.필드}}' 참조 전용입니다. "
                        "{{#each}}, {{formatDate ...}}, {{this.x}} 같은 템플릿 헬퍼/함수/루프 문법은 절대 사용하지 마십시오. "
                        "날짜·목록·포맷팅이 필요하면 노드의 prompt에 자연어로 지시하십시오.\n\n"
                        f"## 검증 오류:\n{err}\n\n## 이전 출력:\n{candidate}"
                    )
                    fixed = await _run_designer(fix_prompt)
                    if fixed.strip():
                        candidate = fixed

                # 재시도 후에도 검증 실패면 하드 실패(CHAT_PARSE_FAILED) 대신 CLARIFICATION으로 우아하게 폴백
                if _static_validation_error(candidate) is not None:
                    logger.warning(
                        "[chat] 정적 검증 자가 교정 %d회 실패 — CLARIFICATION 폴백.", _MAX_VALIDATION_RETRIES,
                    )
                    return json.dumps({
                        "message": "워크플로우를 자동 생성했지만 일부 노드 설정에 오류가 있어 완성하지 못했습니다. "
                                   "조금 더 단순하게 다시 설명해 주시겠어요? "
                                   "(예: 날짜 형식이나 목록 정리는 각 노드 설명에 맡겨 주세요)",
                        "type": "CLARIFICATION_NEEDED",
                        "actions": [], "options": [],
                        "changeDescription": None, "nodes": None, "edges": None,
                        "workflowName": None,
                    }, ensure_ascii=False)

                return candidate

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

        data = _extract_json(cleaned)
        if data is None:
            logger.info("LLM이 일반 텍스트 응답을 반환하여 CLARIFICATION_NEEDED로 처리합니다.")
            # code fence 제거 후 사용자에게 보여줄 텍스트 정제
            cleaned = cleaned.strip()
            if cleaned.startswith("```"):
                cleaned = "\n".join(cleaned.split("\n")[1:])
            if cleaned.rstrip().endswith("```"):
                cleaned = "\n".join(cleaned.rstrip().split("\n")[:-1])
            cleaned = cleaned.strip()
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
        # draft 하이드레이션 + ID 재부여 + 참조식/엣지 리맵 (정적 검증 사전점검과 동일 로직 공유)
        raw_nodes, raw_edges = _prepare_nodes(data)

        if response_type in ("WORKFLOW_GENERATED", "WORKFLOW_MODIFIED"):
            if not raw_nodes:
                raise ValueError("WORKFLOW_GENERATED/MODIFIED 타입에는 nodes가 필요합니다.")
            apply_service_brand(raw_nodes)
            _strip_invalid_webhook_credentials(raw_nodes, allowed_webhook_credential_ids)
            WorkflowValidator.validate(raw_nodes, raw_edges or [], allowed_mcp_catalog_ids)

        nodes = [WorkflowNode(**n) for n in raw_nodes] if raw_nodes else None
        edges = [WorkflowEdge(**e) for e in raw_edges] if raw_edges else None
        actions = [ChatAction(**a) for a in data.get("actions", [])]
        # 모델이 후보를 과도하게 많이 담아도(저장소 수십 개 등) UI 과부하를 막기 위해 하드 캡.
        # 목록에 없으면 사용자가 직접 입력할 수 있다(message 안내 + 일반 입력 경로).
        # 개별 옵션 파싱 실패가 전체 생성 실패로 번지지 않도록 방어적으로 필터링한다.
        options = []
        for o in (data.get("options") or []):
            if isinstance(o, dict) and "value" in o and "label" in o:
                try:
                    options.append(ClarificationOption(**o))
                except Exception:
                    pass
        options = options[:_MAX_CLARIFICATION_OPTIONS]

        response = ChatResponse(
            message=data.get("message", ""),
            type=response_type,
            actions=actions,
            options=options,
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


async def chat_workflow_stream(**kwargs):
    """chat_workflow를 실행하며 진행 단계를 SSE용 이벤트로 yield한다.

    yield 형식은 ``(event, data)`` 튜플이다.
      - ``("stage", {"stage": "designing" | "reviewing"})`` — 설계/검토 진행 알림
      - ``("done", ChatResponse)`` — 완성된 최종 응답
      - ``("error", {"message": str})`` — 실행 중 예외

    chat_workflow를 백그라운드 task로 돌리고, on_stage 콜백이 큐에 넣은 단계 이벤트를
    실시간으로 흘린 뒤 완료 시 최종 결과(done) 또는 예외(error)를 마지막으로 방출한다.
    on_stage는 내부에서 주입하므로 kwargs로 전달하면 안 된다.
    """
    queue: asyncio.Queue = asyncio.Queue()

    def on_stage(stage: str) -> None:
        queue.put_nowait(("stage", {"stage": stage}))

    async def _run() -> None:
        try:
            response = await chat_workflow(on_stage=on_stage, **kwargs)
            await queue.put(("done", response))
        except ValueError as e:
            await queue.put(("error", {"message": str(e)}))
        except Exception as e:
            logger.error("[chat-stream] 스트리밍 실행 실패: %s", e, exc_info=True)
            await queue.put(("error", {"message": ErrorCode.CHAT_EXECUTION_FAILED.message}))
        finally:
            await queue.put(None)  # 종료 sentinel

    task = asyncio.create_task(_run())
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
    finally:
        # 구독자가 중간에 연결을 끊으면(클라이언트 disconnect) 백그라운드 task를 정리한다.
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

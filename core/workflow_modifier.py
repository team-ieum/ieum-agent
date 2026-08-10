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
from core.node_hydration import prepare_hydrated_nodes
from core.template_registry import (
    dehydrate_nodes, slot_catalog_text, reattach_stripped_fields,
    PASSTHROUGH_TEMPLATE_ID,
)
from db.mongodb import modify_workflow_logs
from tools.registry import apply_service_brand

logger = logging.getLogger(__name__)

# 노드 구조(type/fixed.config)는 templateId가 결정론적으로 정하고, LLM은 draft(templateId+slots)만
# 채운다(core.workflow_chat과 동일 계약, IEUM-AI-58). full-node config 스키마를 직접 뱉게 하면
# model·serviceType 같은 표시 필드를 LLM이 지어내거나 비워야 했고, 그걸 응답 후처리로 되채우려던
# 시도가 IEUM-AI-55에서 리뷰 3라운드 연속 회귀를 냈다(도구 유무·intent 추론 모두 오분류 사례가 있었다).
_MODIFY_SYSTEM_PROMPT = f"""\
당신은 IEUM 워크플로우를 수정하는 AI 어시스턴트입니다.
사용자의 수정 요청에 따라 아래 현재 워크플로우 draft를 갱신하십시오.

당신은 노드 구조를 직접 설계하지 않습니다. 아래 '노드 템플릿 카탈로그'에서 각 노드의 templateId를 고르고,
그 템플릿이 정의한 슬롯(slots)만 채웁니다. 노드의 타입·도구·고정 설정(model, serviceType 포함)은 템플릿이
결정합니다. '자동주입(작성금지)' 표기 슬롯(llmProvider, model)은 시스템이 채우므로 작성하지 않습니다.

## Variable Reference Syntax
이전 노드의 결과를 참조할 때는 반드시 아래 형식을 사용한다.
{{{{nodes.<node-id>.output.<field>}}}}

## 수정 규칙
1. 기존 노드 id 체계 유지. 새 노드는 가장 큰 번호 + 1로 부여한다.
2. 수정 요청과 무관한 노드는 현재 워크플로우 draft의 templateId·slots를 그대로 유지한다(값을 임의로 바꾸지 않는다).
3. templateId가 "{PASSTHROUGH_TEMPLATE_ID}"인 노드는 편집을 지원하지 않는다. templateId와 id만 그대로 두고
   넘긴다(서버가 원본으로 복원한다). 사용자가 이 노드 자체의 변경을 요청하면 그 노드는 손대지 말고,
   changeDescription에 그 노드는 편집할 수 없어 변경하지 못했다고 명시한다(변경한 척하지 않는다).
   설명(description)이 비어 있을 때만 'node' 필드에 description을 채워 보낼 수 있다.
4. edges의 source/target은 반드시 nodes에 존재하는 id를 참조한다.
5. CONDITION 노드의 true/false 분기는 conditionType: "true" | "false" 로 표현한다.
6. 외부 연동(Notion/Gmail/Slack/Discord/GitHub 등)이 필요하면 해당 서비스의 ai.* 템플릿을 선택한다.
7. 리소스 식별자(parent_page_id, owner/repo, spreadsheet_id 등)는 사용자가 명시한 값만 prompt 슬롯에
   자연어로 기입한다. 명시하지 않은 값을 임의로 추측·가정해 채우지 않는다.
8. changeDescription은 반드시 사용자 요청과 동일한 언어로 작성한다.
   한국어로 요청하면 한국어로, 영어로 요청하면 영어로 작성한다.
9. 서로 다른 외부 서비스를 호출하는 작업은 반드시 별도의 AI 노드(별도 templateId)로 분리한다.
10. [description 슬롯] 모든 템플릿의 description 슬롯은 필수이며, 워크플로우 화면에서 사용자에게 그대로
    보여줄 쉬운 안내 1문장이다. 수정하지 않는 노드의 description은 원본 그대로 유지하고, 새로 추가하는
    노드에는 반드시 새로 작성한다. 도구 키·templateId·필드명·변수 참조식 등 기술 용어는 넣지 않는다.
11. JSON 외 어떤 텍스트도 출력하지 않는다.

## Output Format
Respond ONLY with a valid JSON object. No explanation, no markdown, no code fences.

{{
  "nodes": [ /* 수정된 노드 draft 목록. 각 항목 {{"id","templateId","slots"}} */ ],
  "edges": [ /* source/target(nodes에 존재하는 id), 분기는 conditionType */ ],
  "changeDescription": "변경 내용을 한 문장으로 요약"
}}
"""


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

    # 저장된 full-node를 draft(templateId+slots)로 역변환해 주입한다. LLM은 draft로 편집한다.
    current_drafts = dehydrate_nodes(current_nodes)
    # description 슬롯 도입(IEUM-AI-55) 이전에 저장된 노드는 값이 비어 draft에서 아예 빠진다.
    # 이 id들만 "그대로 복사 금지"의 예외로 프롬프트에 못박고, LLM이 놓쳐도 하이드레이션 직전에 보정한다.
    # pass-through draft(templateId=PASSTHROUGH_TEMPLATE_ID)는 slots 자체가 없으므로 제외한다
    # (제외하지 않으면 slots 없는 draft가 전부 "description 없는 레거시"로 오분류된다).
    legacy_desc_ids = {
        d["id"] for d in current_drafts
        if d.get("id") and d.get("templateId") != PASSTHROUGH_TEMPLATE_ID
        and not str((d.get("slots") or {}).get("description") or "").strip()
    }
    legacy_rule = ""
    if legacy_desc_ids:
        legacy_rule = (
            "\n12. 단, 아래 현재 워크플로우 draft에서 description 슬롯이 없는 노드({ids})는 2번의 예외다."
            "\n    복사만 하면 안 되고, label과 prompt를 보고 사용자에게 보여줄 쉬운 설명 1문장을"
            "\n    description 슬롯에 새로 채워야 한다(description은 모든 노드의 필수 슬롯이라 빠진"
            "\n    채로 두면 수정이 실패한다). 이 노드들의 나머지 슬롯 값은 2번대로 그대로 둔다."
        ).format(ids=", ".join(sorted(legacy_desc_ids)))

    catalog = slot_catalog_text()
    current_workflow_json = json.dumps(
        {"nodes": current_drafts, "edges": current_edges},
        ensure_ascii=False,
        indent=2,
    )
    instruction = (
        _MODIFY_SYSTEM_PROMPT
        + legacy_rule
        + f"\n\n## 노드 템플릿 카탈로그 (templateId + 채울 슬롯)\n{catalog}"
        + f"\n\n## 현재 워크플로우 (draft)\n{current_workflow_json}"
    )

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
        raw_drafts = data["nodes"]
        if not isinstance(raw_drafts, list):
            raise ValueError("LLM 응답의 nodes가 리스트가 아닙니다.")

        # draft 하이드레이션(pass-through 노드는 hydrate_node가 current_nodes 원본에서만 복원한다 —
        # 날조 차단 게이트는 호출부가 아니라 hydrate_node 자체의 기본 거부 정책) + 레거시 description
        # 보정. core.workflow_chat과 동일 로직을 공유한다(단일 구현).
        raw_nodes, raw_edges = prepare_hydrated_nodes(
            raw_drafts, data.get("edges"),
            provider=provider, current_nodes=current_nodes,
            preserve_id=True, legacy_desc_ids=legacy_desc_ids,
        )
        # 슬롯 밖이라 하이드레이션이 만들지 않는 상위 필드(position 등)만 원본에서 복원한다.
        # config(credentialId·tools 등)는 되살리지 않는다 — reattach_stripped_fields 참고.
        reattach_stripped_fields(raw_nodes, current_nodes)
        # brand는 하이드레이션 대상이 아니라 별도 후처리로 도출된다(생성 경로와 동일).
        apply_service_brand(raw_nodes)

        nodes = [WorkflowNode(**n) for n in raw_nodes]
        edges = [WorkflowEdge(**e) for e in (raw_edges or [])]
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

import json
import logging
import time
from datetime import datetime, timezone

from api.schemas.generate_workflow import GenerateWorkflowResponse, WorkflowNode, WorkflowEdge
from common.error_code import ErrorCode
from core.env_lock import get_env_lock
from core.provider_config import resolve_model, resolve_env_key
from core.model_factory import uses_env_key
from db.mongodb import generate_workflow_logs
from core.validators.workflow_validator import WorkflowValidator, WorkflowValidationError

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """
You are a workflow automation assistant.
당신은 노드의 구조를 직접 설계하지 않는다. 노드의 타입·도구·고정 설정은 '노드 템플릿'이 결정한다.
당신의 역할은 계획(plan)에 정해진 templateId를 그대로 쓰고, 그 템플릿의 가변값(slots)만 채우는 것이다.

## Output Format
Respond ONLY with a valid JSON object. No explanation, no markdown, no code fences.

{
  "nodes": [
    {"id": "node-1", "templateId": "trigger.schedule",
     "slots": {"label": "매일 9시", "description": "정해둔 시각이 되면 자동으로 시작돼요.", "cron": "0 9 * * *"}},
    {"id": "node-2", "templateId": "ai.notion_search",
     "slots": {"label": "검색", "description": "노션에서 찾고 있는 페이지를 검색해요.", "prompt": "..."}}
  ],
  "edges": [
    {"source": "node-1", "target": "node-2", "conditionType": null}
  ]
}

## Rules
1. 각 노드는 계획(plan)에 명시된 templateId를 그대로 사용한다. templateId를 임의로 바꾸거나 새로 만들지 않는다.
2. slots에는 해당 templateId가 정의한 슬롯만 채운다(아래 '노드 템플릿 카탈로그' 참조).
   카탈로그에 없는 슬롯 키를 만들지 않는다.
3. 카탈로그에 '자동주입(작성금지)'로 표기된 슬롯(llmProvider, model)은 시스템이 채우므로 작성하지 않는다.
4. '필수' 슬롯은 모두 채운다. '선택' 슬롯은 실제로 필요할 때만 채운다.
5. credentialId / access_token / tools / agentType / triggerType 등 구조·자격 값은
   템플릿과 런타임이 처리한다. slots에 넣지 않는다.
6. 변수 참조: {{nodes.<node-id>.output.<field>}}. 노드 타입별 실제 출력 필드만 사용한다(임의 필드명 금지):
   - AI 노드 결과 → `output.output` (예: {{nodes.node-2.output.output}}). results/content/data 등 날조 금지.
   - HTTP → `output.body`, `output.statusCode`
   - TRIGGER(SCHEDULE) → `output.triggeredAt`, `output.cron`
   - TRANSFORM → 그 노드 매핑에서 정의한 키
7. 발송/저장 노드(slack/discord/gmail/notion)의 prompt는 **참조식 단독으로 두지 말고** 발송·저장 지시문과
   보낼 내용 참조를 함께 쓴다. (예: "다음 내용을 디스코드로 보내줘: {{nodes.node-3.output.output}}")
8. CONDITION 노드의 true/false 분기는 edges의 conditionType: "true" | "false" 로 표현한다.
9. edges의 source/target은 반드시 nodes에 존재하는 id를 참조한다.
10. label/prompt/description은 반드시 사용자 요청과 동일한 언어로 작성한다.
10-1. description 슬롯(모든 템플릿 필수)은 워크플로우 화면에서 사용자에게 그대로 보여줄 안내 문장이다.
    - 그 노드가 무슨 일을 하는지 1문장으로 쉽게 쓴다. (예: "AI가 문의 내용을 읽고 알맞은 유형으로 나눠요.")
    - 도구 키·templateId·필드명·변수 참조식({{nodes...}})·JSON 등 기술 용어를 넣지 않는다.
    - 계획(plan)의 description을 그대로 복사하지 말고, 사용자가 읽을 문장으로 다시 쓴다.
11. 사용자가 명시하지 않은 식별값(page_id 등)에 플레이스홀더(YOUR_XXX_HERE, <값>)를 쓰지 않는다.
12. JSON 외 어떤 텍스트도 출력하지 않는다.
13. 조회 노드 prompt 경량화 (필수):
    - 후속 노드가 실제로 쓰는 필드만 추출하도록 지시한다. 전체 raw JSON 덤프 금지(타임아웃 유발), 임의 요약/왜곡 금지.
    - 목록 조회(깃허브 PR/이슈, 노션 검색 등)는 반드시 단일 페이지·개수 상한을 못박는다
      (예: "최신순 1페이지(per_page=30, page=1)만 조회"). 무한 페이징 차단.
    - prompt는 핵심 지시(동작·입력 참조·출력 형식) 위주 2~3문장 이내로 간결하게.
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
    key_mode: str | None = None,
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
            "keyMode": key_mode,
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


def _parse_and_validate(raw_output: str, original_prompt: str, provider: str | None = None,
                        allowed_mcp_catalog_ids: set | None = None) -> GenerateWorkflowResponse:
    if not raw_output or not raw_output.strip():
        raise ValueError("LLM이 빈 응답을 반환했습니다.")

    cleaned = _extract_json_object(raw_output)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 문법 오류가 있습니다: {str(e)}")

    # 0. draft({templateId, slots})를 템플릿으로 하이드레이션한다(구조는 템플릿 결정론, 환각 차단).
    #    provider 슬롯은 요청 provider로 자동 주입된다. tools 패키지는 google.adk를 import하므로
    #    template_registry/tools는 lazy import한다.
    from core.template_registry import hydrate_nodes, SlotFillError
    try:
        node_dicts = hydrate_nodes(data.get("nodes", []), provider=provider)
    except SlotFillError as e:
        raise ValueError(f"노드 하이드레이션 실패: {str(e)}")

    # 0-1. 노드 config["brand"] 주입(프론트 UI 서비스 라벨/아이콘).
    from tools.registry import apply_service_brand
    apply_service_brand(node_dicts)

    # 1. Pydantic 스키마 형태 로드 (여기서 Pydantic ValidationError 발생 가능)
    try:
        nodes = [WorkflowNode(**n) for n in node_dicts]
        edges = [WorkflowEdge(**e) for e in data.get("edges", [])]
    except Exception as e:
        raise ValueError(f"스키마 검증(Pydantic) 실패: {str(e)}")

    # 2. 코드 레벨 의미론적 상세 검증(하이드레이션된 노드 기준)
    raw_edges = data.get("edges", [])
    WorkflowValidator.validate(node_dicts, raw_edges, allowed_mcp_catalog_ids)

    return GenerateWorkflowResponse(
        nodes=nodes,
        edges=edges,
        rawPrompt=original_prompt,
    )


async def generate_workflow(
    prompt: str,
    provider: str,
    api_key: str | None,
    available_mcp_servers: list | None = None,
    user_role: str | None = None,
    key_mode: str | None = None,
) -> GenerateWorkflowResponse:
    start = time.monotonic()
    model = resolve_model(provider)
    env_key = resolve_env_key(provider)
    lock = get_env_lock(env_key) if (env_key and uses_env_key(provider, api_key, user_role)) else None

    # 생성 단계에서 허용되는 MCP 카탈로그 ID 집합. 카탈로그가 없으면 MCP는 전면 차단된다.
    allowed_mcp_catalog_ids = {
        (m.get("catalogId") if isinstance(m, dict) else getattr(m, "catalogId", None))
        for m in (available_mcp_servers or [])
    }
    allowed_mcp_catalog_ids.discard(None)

    def _validate(raw_output: str) -> GenerateWorkflowResponse:
        # Builder Reflexion 루프(factory)가 호출하는 검증 콜백. 실패 시 예외를 던진다.
        return _parse_and_validate(raw_output, prompt, provider, allowed_mcp_catalog_ids)

    async def _execute() -> str:
        from agents.generate.factory import run_generate_agent
        return await run_generate_agent(
            prompt=prompt,
            model=model,
            provider=provider,
            api_key=api_key,
            env_key=env_key,
            user_role=user_role,
            validate_fn=_validate,
            available_mcp_servers=available_mcp_servers,
            allowed_mcp_catalog_ids=allowed_mcp_catalog_ids,
        )

    try:
        # Plan 검증·Builder Reflexion 루프는 run_generate_agent 내부에서 수행된다.
        # 반환된 raw_output은 이미 _validate를 통과한 상태이므로 여기서 객체화만 한다.
        if lock:
            async with lock:
                raw_output = await _execute()
        else:
            raw_output = await _execute()

        response = _parse_and_validate(raw_output, prompt, provider, allowed_mcp_catalog_ids)
    except Exception as err:
        duration_ms = int((time.monotonic() - start) * 1000)
        await _save_generate_workflow_log(
            prompt=prompt,
            provider=provider,
            model=model,
            success=False,
            duration_ms=duration_ms,
            error_message=f"워크플로우 생성/검증 최종 실패: {str(err)}",
            key_mode=key_mode,
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
        key_mode=key_mode,
    )
    return response

"""LLM draft 출력 → 완성 노드 하이드레이션 공통 로직.

core.workflow_chat(채팅 생성/수정)이 쓴다. 날조 차단 자체는 hydrate_node가 하고(서버 측 원본
dict를 넘긴 호출부만 pass-through가 열린다), 이 모듈은 그 원본 dict를 구성해 넘기고 편집 불가
노드가 출력에서 빠지지 않았는지 확인하는 역할이다."""
import copy
import logging
import re

from core.output_fields import InvalidOutputFieldsError, validate_output_fields
from core.validators.workflow_validator import WorkflowValidationError, WorkflowValidator
from core.template_registry import (
    hydrate_node, dehydrate_node, get_template, SlotFillError, PASSTHROUGH_TEMPLATE_ID,
)

logger = logging.getLogger(__name__)


def preserved_config(current_nodes: list | None, passthrough_ids: set | None = None) -> dict:
    """채팅 재조립이 버리는 슬롯 밖 config(outputFields·systemMessage)를 저장분에서 모은다.

    반환: {노드 id: (templateId, {키: 값})}. templateId는 서버가 그 노드를 dehydrate한 결과다 —
    재조립 때 LLM draft의 templateId가 이것과 같을 때만 되붙인다(systemMessage는 모든 AI 템플릿의 공통
    칸이라 다른 템플릿으로 바뀐 노드에 옛 지시문이 붙으면 안 된다). 그 템플릿이 허용하는 키만 담는다.
    outputFields는 규칙(validate_output_fields)을 통과할 때만 담는다. 위반 값을 되살린 뒤 검증기가
    거부하면 LLM은 그 값을 보지도 고치지도 못해(슬롯 아님) 채팅 수정이 영구히 막힌다 — 버리고
    실행 시 INVALID_OUTPUT_FIELDS가 막게 둔다. pass-through 노드는 원본 전체를 복사하므로 제외한다."""
    passthrough_ids = passthrough_ids or set()
    out = {}
    for n in current_nodes or []:
        if not isinstance(n, dict) or n.get("id") in passthrough_ids or not isinstance(n.get("config"), dict):
            continue
        draft = dehydrate_node(n)
        if not draft or draft["templateId"] == PASSTHROUGH_TEMPLATE_ID:
            continue
        allowed = set(get_template(draft["templateId"])["allowed_config_fields"])
        cfg = n["config"]
        kept = {}
        if "systemMessage" in allowed and isinstance(cfg.get("systemMessage"), str) and cfg["systemMessage"]:
            kept["systemMessage"] = cfg["systemMessage"]
        if "outputFields" in allowed and cfg.get("outputFields") not in (None, []):
            try:
                if validate_output_fields(cfg["outputFields"], cfg.get("tools"), cfg.get("agentType")):
                    kept["outputFields"] = copy.deepcopy(cfg["outputFields"])
            except InvalidOutputFieldsError as e:
                logger.warning("채팅 수정: 규칙 위반 outputFields는 되살리지 않는다 (node_id=%s): %s", n.get("id"), e)
        if kept:
            out[n.get("id")] = (draft["templateId"], kept)
    return out


def _drop_unresolvable_refs(node_id: str, kept: dict, draft_ids: set, edges: list) -> dict:
    """되살린 문자열(systemMessage, outputFields[].description)이 검증기의 참조 검사를 통과하는 키만 남긴다.

    검증기는 config의 모든 문자열을 훑는다(systemMessage는 형식·대상, description은 대상만). 저장분이
    이번 수정에서 지워지는 노드를 참조하거나 systemMessage 형식이 틀리면, 그대로 붙인 순간 수정이 영구히
    거부된다(LLM은 슬롯 밖 값을 고칠 수 없다). 걸린 키만 뺀다
    (spec §3.3 조건 4). node_id·draft_ids·edges는 id 재부여 전 기준 — 참조 리맵은 그 뒤에 일관되게 돈다."""
    out = {}
    for key, value in kept.items():
        texts = [value] if key == "systemMessage" else [f.get("description") or "" for f in value]
        try:
            for text in texts:
                WorkflowValidator.check_restored_references(text, node_id, draft_ids, edges,
                                                            check_format=(key == "systemMessage"))
        except WorkflowValidationError as e:
            logger.warning("채팅 수정: 되살린 %s의 참조가 이번 수정과 맞지 않아 붙이지 않는다 (node_id=%s): %s",
                           key, node_id, e)
            continue
        out[key] = value
    return out


def backfill_legacy_description(draft: dict) -> None:
    """description 슬롯이 빈 draft를 label로 채운다(in-place).

    description은 모든 템플릿의 required 슬롯이라 hydrate_node가 누락 시 하드 실패한다.
    모델이 어느 노드 하나라도 빠뜨리면 자가 교정 재시도 후에도 정당한 수정 요청 자체가 거부된다.
    라벨 복제는 좋은 설명이 아니지만 수정 실패보다 낫다.

    호출부는 레거시 id(=저장분에 description이 없던 노드)에만 적용한다. 설명이 이미 있는
    노드까지 대상으로 넓히면, 되살릴 원본이 draft에 있는데도 label 복제로 덮어써 사용자가 쓴
    문장이 소실된다. 신규 노드에도 적용하지 않으므로 '새 노드는 반드시 description을 쓴다'는
    강제가 유지된다."""
    slots = draft.get("slots")
    if not isinstance(slots, dict) or str(slots.get("description") or "").strip():
        return
    label = slots.get("label")
    if isinstance(label, str) and label.strip():
        slots["description"] = label.strip()


def prepare_hydrated_nodes(
    raw_drafts: list | None,
    raw_edges: list | None,
    *,
    provider: str | None,
    current_nodes: list | None = None,
    preserve_id: bool | None = None,
    legacy_desc_ids: set | None = None,
    passthrough_ids: set | None = None,
) -> tuple[list | None, list | None]:
    """LLM 출력 draft(nodes)를 템플릿으로 하이드레이션하고 노드 ID 재부여 + 참조식/엣지 리맵을 적용한다.

    **어느 노드가 pass-through인지는 서버가 정한다.** 호출부가 자기 dehydrate 결과에서 뽑은
    passthrough_ids가 유일한 근거이고, LLM 출력은 그 판정을 바꾸지 못한다. 규칙은 한 문장이다 —
    **passthrough_ids의 노드는 출력에 있으면 반드시 센티넬(PASSTHROUGH_TEMPLATE_ID)이어야 하고,
    없으면 삭제로 인정한다.** 양방향으로 막는다:
    - 그 밖의 노드에 센티넬을 붙이면(위장) SlotFillError. 허용하면 편집 가능한 노드를 되돌려놓고
      '수정했습니다'로 응답할 수 있다
    - passthrough_ids의 노드를 진짜 templateId로 되돌리면(센티넬 제거) SlotFillError. 허용하면
      LLM이 쓴 config가 id 기준 검증 면제(웹훅 스트립·MCP 인가·config 화이트리스트)를 그대로
      타고 나간다 — 면제는 '서버가 원본을 복원했다'는 전제 위에서만 성립한다
    이 규칙 덕에 '검증 면제 대상 id'와 '실제로 원본이 복원된 노드'가 정의상 일치한다.
    복원은 서버 측 원본에서만 한다 — LLM이 보낸 "node"는 hydrate_node가 무시한다.
    출력에서 빠진 pass-through 노드는 삭제 요청으로 본다. 서버가 생존을 강제하면 사용자가 그
    노드를 지워달라고 해도 영원히 실패한다(모델이 빼면 거부, 넣으면 삭제가 안 됨).
    legacy_desc_ids에 담긴 id의 draft는 description 슬롯이 비어 있으면 label로 보정한다.
    raw_drafts가 비어 있으면(빈 배열/None) 그대로 반환한다(하이드레이션 대상 없음).
    하이드레이션 실패(SlotFillError 등)는 호출부에서 처리한다."""
    if not raw_drafts:
        return raw_drafts, raw_edges

    legacy_desc_ids = legacy_desc_ids or set()
    passthrough_ids = passthrough_ids or set()
    current_nodes_by_id = {
        n.get("id"): n for n in (current_nodes or [])
        if isinstance(n, dict) and n.get("id") in passthrough_ids
    }
    # 사용자가 고른 model(BYOK)은 LLM이 쓰지 않는 시스템 주입 슬롯이라 재하이드레이션마다 기본값으로
    # 리셋된다. 서버가 쥔 저장분에서만 되살린다(LLM draft의 slots["model"]은 계속 무시 — 날조 차단).
    # provider가 바뀐 요청이면 옛 provider의 모델이라 되살리지 않는다. pass-through 노드는 원본
    # 전체를 복사하므로 여기서 손대지 않는다.
    stored_models = {
        n.get("id"): n["config"]["model"]
        for n in (current_nodes or [])
        if isinstance(n, dict) and n.get("id") not in passthrough_ids
        and isinstance(n.get("config"), dict) and isinstance(n["config"].get("model"), str)
        and n["config"]["model"]
        and str(n["config"].get("llmProvider") or "").upper() == str(provider or "").upper()
    }
    preserved = preserved_config(current_nodes, passthrough_ids)
    # 되살린 문자열의 참조 검사 기준 = 이번 출력 draft(재부여 전 id)와 그 엣지
    draft_ids = {d.get("id") for d in raw_drafts if isinstance(d, dict) and d.get("id")}
    draft_edges = [e for e in (raw_edges or []) if isinstance(e, dict)]
    pid = preserve_id if preserve_id is not None else bool(current_nodes)
    id_mapping = {}
    raw_nodes = []
    for idx, draft in enumerate(raw_drafts):
        old_id = draft.get("id") if isinstance(draft, dict) else None
        if old_id in passthrough_ids and (
                not isinstance(draft, dict) or draft.get("templateId") != PASSTHROUGH_TEMPLATE_ID):
            raise SlotFillError(
                f"노드 '{old_id}'는 편집을 지원하지 않습니다. templateId를 "
                f"\"{PASSTHROUGH_TEMPLATE_ID}\"로 둔 채 반환하거나, 삭제하거나 다른 노드로 교체할 거라면 아예 빼십시오(교체할 노드는 새 id로 만든다)."
            )
        if old_id in legacy_desc_ids:
            backfill_legacy_description(draft)
        node = hydrate_node(
            draft, provider=provider, passthrough_originals=current_nodes_by_id,
            model_override=stored_models.get(old_id),
        )
        kept = preserved.get(old_id)
        if kept and draft.get("templateId") == kept[0]:
            node["config"].update(copy.deepcopy(_drop_unresolvable_refs(old_id, kept[1], draft_ids, draft_edges)))
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
        # 한 번의 스캔으로 치환한다. 매핑을 순차 적용하면 이미 새 id로 바뀐 참조를 뒤 항목이
        # 다시 치환해 엉뚱한 노드를 가리킨다(예: {node-2→node-1, node-1→node-2}면 왕복해 원위치).
        _ref_pattern = re.compile(
            r'\{\{\s*nodes\.(' + '|'.join(re.escape(k) for k in id_mapping) + r')\.output\.'
        )

        def _replace_refs(val):
            if isinstance(val, dict):
                return {k: _replace_refs(v) for k, v in val.items()}
            elif isinstance(val, list):
                return [_replace_refs(v) for v in val]
            elif isinstance(val, str):
                return _ref_pattern.sub(
                    lambda m: '{{nodes.' + id_mapping[m.group(1)] + '.output.', val)
            return val
        raw_nodes = _replace_refs(raw_nodes)

    return raw_nodes, raw_edges

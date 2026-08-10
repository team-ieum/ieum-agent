"""LLM draft 출력 → 완성 노드 하이드레이션 공통 로직.

core.workflow_chat(채팅 생성/수정)이 쓴다. 날조 차단 자체는 hydrate_node가 하고(서버 측 원본
dict를 넘긴 호출부만 pass-through가 열린다), 이 모듈은 그 원본 dict를 구성해 넘기고 편집 불가
노드가 출력에서 빠지지 않았는지 확인하는 역할이다."""
import re

from core.template_registry import hydrate_node, SlotFillError


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
    passthrough_ids만 원본 복원이 열리고, 그 밖의 노드에 LLM이 PASSTHROUGH_TEMPLATE_ID를 붙이면
    SlotFillError다. LLM 판단을 믿으면 편집 가능한 노드를 pass-through로 위장해 사용자의 수정을
    조용히 되돌릴 수 있다(응답은 '수정했습니다'인데 노드는 그대로).
    복원은 서버 측 원본에서만 한다 — LLM이 보낸 "node"는 hydrate_node가 무시한다.
    passthrough_ids 중 출력에서 통째로 빠진 노드가 있으면 SlotFillError(편집 불가 노드가 조용히
    삭제되는 것 차단 — 프롬프트 지시만으로는 못 막는다).
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
    pid = preserve_id if preserve_id is not None else bool(current_nodes)
    id_mapping = {}
    raw_nodes = []
    seen_passthrough = set()
    for idx, draft in enumerate(raw_drafts):
        old_id = draft.get("id") if isinstance(draft, dict) else None
        if old_id in legacy_desc_ids:
            backfill_legacy_description(draft)
        node = hydrate_node(
            draft, provider=provider, passthrough_originals=current_nodes_by_id
        )
        if old_id in passthrough_ids:
            seen_passthrough.add(old_id)
        new_id = old_id if (pid and old_id) else f"node-{idx + 1}"
        node["id"] = new_id
        if old_id and old_id != new_id:
            id_mapping[old_id] = new_id
        raw_nodes.append(node)

    dropped = passthrough_ids - seen_passthrough
    if dropped:
        raise SlotFillError(
            f"편집을 지원하지 않는 노드({', '.join(sorted(dropped))})가 출력에서 빠졌습니다. "
            "이 노드들은 templateId와 id를 그대로 두고 반드시 함께 반환해야 합니다."
        )

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

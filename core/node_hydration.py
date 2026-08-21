"""LLM draft 출력 → 완성 노드 하이드레이션 공통 로직.

core.workflow_chat(채팅 생성/수정)이 쓴다. 날조 차단 자체는 hydrate_node가 하고(서버 측 원본
dict를 넘긴 호출부만 pass-through가 열린다), 이 모듈은 그 원본 dict를 구성해 넘기고 편집 불가
노드가 출력에서 빠지지 않았는지 확인하는 역할이다."""
import re

from core.template_registry import hydrate_node, SlotFillError, PASSTHROUGH_TEMPLATE_ID


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
    pid = preserve_id if preserve_id is not None else bool(current_nodes)
    id_mapping = {}
    raw_nodes = []
    for idx, draft in enumerate(raw_drafts):
        old_id = draft.get("id") if isinstance(draft, dict) else None
        if old_id in passthrough_ids and (
                not isinstance(draft, dict) or draft.get("templateId") != PASSTHROUGH_TEMPLATE_ID):
            raise SlotFillError(
                f"노드 '{old_id}'는 편집을 지원하지 않습니다. templateId를 "
                f"\"{PASSTHROUGH_TEMPLATE_ID}\"로 둔 채 반환하거나, 삭제할 거라면 아예 빼십시오."
            )
        if old_id in legacy_desc_ids:
            backfill_legacy_description(draft)
        node = hydrate_node(
            draft, provider=provider, passthrough_originals=current_nodes_by_id
        )
        if old_id in stored_models and isinstance(node.get("config"), dict) and "model" in node["config"]:
            from core.provider_config import resolve_model  # settings 의존이라 lazy(template_registry 관례)
            node["config"]["model"] = resolve_model(provider, stored_models[old_id])
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

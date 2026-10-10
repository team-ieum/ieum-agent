"""노드 카탈로그 API 응답(GET /v1/nodes/catalog → BE GET /api/v1/nodes/catalog).

빌더(FE)가 노드 추가 목록과 노드 설정 폼을 그리는 데 쓴다. builder 항목만 match 평가 순서로
내보내고, 생성 전용 메타(llm)는 빼며, 기본값과 같은 속성은 생략한다(spec §6.1)."""
import copy

from core.template_registry import builder_entries

_DEFAULT_ATTRS = {"required": False, "ref": True, "list": False, "dynamic": False}
_ABSENT = object()


def _public_field(field: dict) -> dict:
    out = {k: v for k, v in field.items()
           if k != "llm" and _DEFAULT_ATTRS.get(k, _ABSENT) != v}
    if "children" in out:
        out["children"] = [_public_field(c) for c in out["children"]]
    return out


def _fixed_with_brand(t: dict) -> dict:
    """FE가 노드를 만들 때 쓰는 fixed에 brand를 얹는다(FE 목록 아이콘·BE 연동 목록은 config.brand만 본다).
    레지스트리 항목의 fixed는 생성 경로와 공유하므로 복사본에만 넣는다."""
    fixed = copy.deepcopy(t["fixed"])
    fixed["config"]["brand"] = t["brand"]
    return fixed


def node_catalog() -> dict:
    entries = []
    for t in builder_entries():
        entry = {
            "id": t["id"],
            "nodeType": t["node_type"],
            "app": t.get("app"),
            "title": t["title"],
            "description": t["description"],
            "match": t["match"],
            "fixed": _fixed_with_brand(t),
            "inputFields": [_public_field(f) for f in t["inputFields"]],
            "outputFields": [_public_field(f) for f in t["outputFields"]],
        }
        if t.get("outputDynamic"):
            entry["outputDynamic"] = True
        if t.get("outputsFrom"):
            entry["outputsFrom"] = t["outputsFrom"]
        entries.append(entry)
    return {"entries": entries}

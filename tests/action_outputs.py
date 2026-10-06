"""액션 항목 outputFields ↔ 도구 실제 반환 일치 단언(spec §8). 각 도구의 성공 테스트가 부른다.
도구가 반환 키를 바꾸면 그 성공 테스트가 실패한다 — 카탈로그 선언이 낡지 않게."""
import json

from core import template_registry as tr


def _keys(fields: list) -> set:
    return {f["key"] for f in fields}


def assert_matches_outputs(action_id: str, result) -> None:
    data = json.loads(result) if isinstance(result, str) else dict(result)
    assert "error" not in data, data
    data.pop("success", None)
    fields = tr.get_template(action_id)["outputFields"]
    assert set(data) == _keys(fields), (action_id, sorted(data), sorted(_keys(fields)))
    for f in fields:
        if f.get("dynamic") or not f.get("children"):
            continue
        value = data[f["key"]]
        for item in (value if f.get("list") else [value])[:1]:
            assert set(item) == _keys(f["children"]), (action_id, f["key"], sorted(item))

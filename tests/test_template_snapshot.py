"""AI-64 회귀 가드 — 템플릿 원본 형식을 바꿔도 생성 경로가 보는 것은 의도된 차이 말고 그대로다.

fixture는 AI-64 착수 전 dev(fb97575)의 레지스트리에서 떴다(아래 __main__). 의도된 차이(spec §7.3)는
INTENDED_* 상수로 명시하고, 그 밖의 차이는 실패한다. slot description은 어떤 LLM 프롬프트에도
들어가지 않아(slot_catalog_text는 이름·kind·필수만 출력) 비교에서 뺀다."""
import copy
import json
import os

from core import template_registry as tr

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "template_snapshot_before_ai64.json")

# spec §7.3 ② — 표시 이름 슬롯 이름 규칙 `<key>_name`
INTENDED_SLOT_RENAMES: dict[str, str] = {}
# spec §7.3 ③ — 허용 config 키는 늘기만 한다
INTENDED_ALLOWED_ADDED: dict[str, set[str]] = {}
# spec §7.3 ① — Calendar·Drive·Notion 프리셋의 리소스 ID 슬롯
INTENDED_NEW_SLOTS: dict[str, list[str]] = {}


def _roundtrip(snippet: dict) -> dict:
    draft = tr.dehydrate_node(copy.deepcopy(snippet))
    node = tr.hydrate_node(draft, provider="GEMINI")
    node.get("config", {}).pop("model", None)  # .env마다 기본 모델이 달라 비교에서 뺀다
    return node


def capture() -> dict:
    out = {"menu_index": tr.menu_index(), "templates": {}}
    for tpl in tr.all_templates():
        snip = tpl.get("golden_snippet")
        out["templates"][tpl["id"]] = {
            "node_type": tpl["node_type"],
            "tool_key": tpl["tool_key"],
            "tags": tpl["tags"],
            "menu": tpl["menu"],
            "fixed": tpl["fixed"],
            "slots": [[s["name"], s["path"], s["required"], s["kind"]] for s in tpl["slots"]],
            "allowed_config_fields": sorted(tpl["allowed_config_fields"]),
            "golden_snippet": snip,
            "golden_roundtrip": _roundtrip(snip) if snip else None,
        }
    return out


def _load_fixture() -> dict:
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def test_generation_view_matches_snapshot_except_intended_diffs():
    before = _load_fixture()
    after = json.loads(json.dumps(capture(), ensure_ascii=False))  # 튜플·키 순서 정규화
    assert after["menu_index"] == before["menu_index"]
    assert after["templates"].keys() == before["templates"].keys()
    for tid, b in before["templates"].items():
        expected = copy.deepcopy(b)
        expected["slots"] = [[INTENDED_SLOT_RENAMES.get(s[0], s[0]), *s[1:]] for s in expected["slots"]]
        expected["allowed_config_fields"] = sorted(
            set(expected["allowed_config_fields"]) | INTENDED_ALLOWED_ADDED.get(tid, set()))
        actual = copy.deepcopy(after["templates"][tid])
        new = set(INTENDED_NEW_SLOTS.get(tid, ()))
        actual["slots"] = [s for s in actual["slots"] if s[0] not in new]
        assert actual == expected, tid


def test_intended_new_slots_exist_exactly():
    after = capture()["templates"]
    for tid, names in INTENDED_NEW_SLOTS.items():
        got = [s[0] for s in after[tid]["slots"] if s[0] in set(names)]
        assert got == names, tid


if __name__ == "__main__":  # 리팩터 전 dev에서 1회만 실행해 fixture를 만든다
    os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
    with open(FIXTURE, "w", encoding="utf-8") as f:
        json.dump(capture(), f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")

"""CONDITION 칸 — 새 모양(갈래)과 레거시 3칸(IEUM-AI-97).

FE 명세·BE executor가 보는 이름은 left/right인데 agent는 leftValue/rightValue를 만들어
FE 기술정보 칸이 비었다. agent는 이제 left/right만 만든다.
"""
import core.template_registry as tr

OPS = ["equals", "notEquals", "contains", "notContains", "startsWith", "endsWith", "in", "notIn",
       "greaterThan", "lessThan", "greaterThanOrEqual", "lessThanOrEqual", "numberEquals",
       "before", "after", "sameDay", "isTrue", "isFalse", "isEmpty", "isNotEmpty"]


def _fields():
    return {f["path"]: f for f in tr.get_template("branch.if")["inputFields"]}


def test_분기_칸은_레거시_3칸과_새_칸_모두_선택():
    by_path = _fields()
    assert list(by_path) == ["config.operator", "config.left", "config.right", "config.branches",
                             "config.pick", "config.otherwise", "config.caseSensitive"]
    assert not any(f.get("required") for f in by_path.values())
    assert [c["id"] for c in by_path["config.operator"]["choices"]] == OPS
    assert (by_path["config.pick"]["default"], by_path["config.otherwise"]["default"],
            by_path["config.caseSensitive"]["default"]) == ("first", "branch", False)


def test_갈래_칸은_2단_children():
    br = _fields()["config.branches"]
    assert br["type"] == "object" and br["list"] is True
    assert [c["key"] for c in br["children"]] == ["key", "name", "match", "rules"]
    assert br["children"][2]["default"] == "all"
    rules = br["children"][3]
    assert rules["list"] is True and rules["required"] is True
    assert [c["key"] for c in rules["children"]] == ["left", "operator", "right"]
    assert [c["id"] for c in rules["children"][1]["choices"]] == OPS

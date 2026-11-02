"""CONDITION config 키 표기 통일(IEUM-AI-55 Task 3).

FE 명세·BE executor가 보는 이름은 left/right인데 agent는 leftValue/rightValue를 만들어
FE 기술정보 칸이 비었다. agent는 이제 left/right만 만든다.
"""
import core.template_registry as tr


def test_condition_template_uses_canonical_keys():
    tpl = tr.get_template("branch.if")
    by_path = {f["path"]: f for f in tpl["inputFields"]}
    assert set(by_path) == {"config.operator", "config.left", "config.right"}
    assert all(f.get("required") for f in by_path.values())
    assert set(tpl["golden_snippet"]["config"]) == {"operator", "left", "right"}

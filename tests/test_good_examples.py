import json
import os
import re

import pytest

from api.schemas.generate_workflow import WorkflowNode, WorkflowEdge
from core.skill_loader import SKILL_DIR, load_design_rules
from core.validators.workflow_validator import WorkflowValidator

_GOOD_EXAMPLES_PATH = os.path.join(SKILL_DIR, "references", "good-examples.md")


def _extract_json_blocks(md_text: str) -> list[str]:
    """마크다운에서 ```json ... ``` 코드 블록 본문을 추출한다."""
    return re.findall(r"```json\s*\n(.*?)\n```", md_text, re.DOTALL)


def _load_examples() -> list[dict]:
    with open(_GOOD_EXAMPLES_PATH, "r", encoding="utf-8") as f:
        blocks = _extract_json_blocks(f.read())
    return [json.loads(b) for b in blocks]


def test_good_examples_file_exists_with_blocks():
    examples = _load_examples()
    assert len(examples) >= 3, "골든 예시는 최소 3개여야 한다."


@pytest.mark.parametrize("idx", range(len(_load_examples())))
def test_each_good_example_passes_validation(idx):
    """모든 골든 예시는 스키마 + WorkflowValidator 검증을 통과해야 한다.
    (틀린 예시를 모델에 모방시키면 역효과이므로 회귀 가드로 강제한다.)"""
    example = _load_examples()[idx]
    nodes = example["nodes"]
    edges = example["edges"]

    # 1. Pydantic 스키마 (노드 타입별 config 필수값 포함)
    for n in nodes:
        WorkflowNode(**n)
    for e in edges:
        WorkflowEdge(**e)

    # 2. 의미론적 검증 (TRIGGER/엣지/고아/도구이름/변수참조/HTTP 도메인)
    WorkflowValidator.validate(nodes, edges)


def test_golden_snippets_injected_into_design_rules():
    """노드 템플릿 골든 스니펫이 생성 레퍼런스에 주입된다(통짜 good-examples.md 대체).
    메뉴 인덱스·구조 노드 예시는 항상층으로 주입된다."""
    rules = load_design_rules("아무 요청")
    assert "사용 가능한 노드/도구 메뉴" in rules
    assert "구조 노드 예시" in rules
    assert "```json" in rules  # 골든 스니펫(JSON) 주입 확인

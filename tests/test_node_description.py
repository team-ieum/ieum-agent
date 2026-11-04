"""골든 스니펫 description(사용자용 자연어 설명) 가드 (IEUM-AI-55 Task 1)."""
from core import template_registry as tr

# description에 새어 나오면 안 되는 개발자 시점 토큰(사용자에게 그대로 보인다).
_TECHNICAL_TOKENS = ["{{", "templateId", "builtin:", "config.", "node_type",
                     "JSON", "slot", "output.output"]


def test_every_golden_snippet_has_user_facing_description():
    """골든 스니펫의 description은 비어 있지 않고 기술 용어가 없는 사용자용 문장이다.
    (LLM이 스니펫 톤을 모방하므로 개발자 시점 문장이 섞이면 그대로 사용자에게 노출된다.)"""
    for tpl in tr._generation_templates().values():
        snip = tpl.get("golden_snippet")
        if not snip:
            continue
        desc = snip.get("description")
        assert desc and desc.strip(), f"{tpl['id']}: 골든 스니펫 description 누락"
        for token in _TECHNICAL_TOKENS:
            assert token not in desc, f"{tpl['id']}: description에 기술 용어 '{token}' 포함 — {desc}"

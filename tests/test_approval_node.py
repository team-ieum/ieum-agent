"""APPROVAL(사람 승인 게이트) 노드 계약 (IEUM-BE-45)

BE 런타임이 이 노드에서 실행을 멈추고, 소유자가 승인하면 이어진 실행이 게이트 다음부터 돈다.
agent는 이 노드를 만들고(채팅/생성) 수정 왕복에서 보존만 한다 — 실행하지 않는다.
config는 승인자에게 보여 줄 message 하나(선택)다.
"""

from core import template_registry as tr


def test_approval_template_registered():
    tr.validate_registry()
    tpl = tr.get_template("approval.request")
    assert tpl is not None and tpl["node_type"] == "APPROVAL"

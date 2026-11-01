"""대화형 생성 요청·응답 모델 — 노드별 type 검증 없음, 모르는 키 보존 (spec 2026-10-28 §2.1, IEUM-AI-84)."""
import pytest
from pydantic import ValidationError

from api.schemas.chat import BuildChatRequest, BuildChatResponse

INCOMPLETE = [
    {"id": "n1", "event": "schedule.every", "type": "TRIGGER", "label": "매일", "config": {},
     "position": {"x": 10.5, "y": 20}},
    {"id": "n2", "event": "http.request", "type": "HTTP", "label": "요청", "config": {}},
    {"id": "n3", "event": "branch.if", "label": "분기", "config": {}},
    {"id": "n4", "event": "transform.map", "label": "변환", "config": {}},
    {"id": "n5", "event": "ai.reasoning", "label": "AI", "config": {"prompt": "x"}},
    {"id": "n6", "event": "nobody.knows", "label": "모름", "config": {}},
]


def test_미완성_노드가_그대로_왕복한다():
    req = BuildChatRequest(prompt="x", currentNodes=INCOMPLETE,
                           currentEdges=[{"id": "e1", "source": "n1", "target": "n2", "conditionType": None}])
    nodes = [n.model_dump() for n in req.currentNodes]
    assert nodes == INCOMPLETE
    assert req.currentEdges == [{"id": "e1", "source": "n1", "target": "n2", "conditionType": None}]


@pytest.mark.parametrize("node", [
    {"id": "", "event": "manual.run"},
    {"id": "  ", "event": "manual.run"},
    {"id": "n1", "event": ""},
    {"id": "n1", "event": "manual run"},
    {"id": "n1"},
    {"event": "manual.run"},
])
def test_id_event가_비면_거절(node):
    with pytest.raises(ValidationError):
        BuildChatRequest(prompt="x", currentNodes=[node])


def test_응답은_buildState를_늘_싣고_기본은_BUILDING():
    body = BuildChatResponse(message="m", type="CLARIFICATION_NEEDED", rawPrompt="p").model_dump(mode="json")
    assert body["buildState"] == "BUILDING"
    assert BuildChatResponse(message="m", type="WORKFLOW_MODIFIED", rawPrompt="p", nodes=INCOMPLETE,
                             buildState="DONE").model_dump(mode="json")["nodes"] == INCOMPLETE
    with pytest.raises(ValidationError):
        BuildChatResponse(message="m", type="WORKFLOW_MODIFIED", rawPrompt="p", buildState="building")

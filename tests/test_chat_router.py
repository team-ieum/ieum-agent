from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from main import app
from api.middleware.credential import get_llm_credentials
from common.error_code import ErrorCode


def override_credentials():
    return {
        "provider": "CLAUDE",
        "api_key": "test-key",
        "user_id": "test-user",
        "google_access_token": None,
        "notion_token": None,
    }


client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_and_cleanup_dependency_overrides():
    """각 테스트 전 credential override를 설정하고 테스트 후 초기화한다."""
    app.dependency_overrides[get_llm_credentials] = override_credentials
    yield
    app.dependency_overrides.clear()


CHAT_PAYLOAD = {
    "prompt": "워크플로우 만들어줘",
    "currentNodes": None,
    "currentEdges": None,
    "availableIntegrations": [],
    "unavailableIntegrations": [],
}


def test_chat_endpoint_파싱실패_502_반환():
    with patch("api.routes.chat.chat_workflow", side_effect=ValueError("파싱 실패")):
        response = client.post("/v1/chat", json=CHAT_PAYLOAD)
    assert response.status_code == 502
    assert ErrorCode.CHAT_PARSE_FAILED.message in response.json()["detail"]


def test_chat_endpoint_서버오류_500_반환():
    with patch("api.routes.chat.chat_workflow", side_effect=Exception("예상치 못한 오류")):
        response = client.post("/v1/chat", json=CHAT_PAYLOAD)
    assert response.status_code == 500
    assert ErrorCode.CHAT_EXECUTION_FAILED.message in response.json()["detail"]


def test_chat_endpoint_레거시_condition_워크플로우_수정_요청_수용():
    """구표기(leftValue/rightValue)로 저장된 워크플로우의 수정 요청이 422로 막히지 않고,
    chat_workflow에는 표준 표기(left/right)로만 전달된다(IEUM-AI-55)."""
    from api.schemas.chat import ChatResponse, ChatResponseType

    captured = {}

    async def _fake_chat(**kwargs):
        captured.update(kwargs)
        return ChatResponse(message="ok", type=ChatResponseType.WORKFLOW_MODIFIED, rawPrompt="수정")

    payload = dict(CHAT_PAYLOAD, prompt="조건 바꿔줘", currentNodes=[
        {"id": "node-1", "type": "TRIGGER", "label": "시작", "config": {"triggerType": "MANUAL"}},
        {"id": "node-2", "type": "CONDITION", "label": "분기", "config": {
            "operator": "equals", "leftValue": "{{nodes.node-1.output.output}}", "rightValue": "urgent"}},
    ], currentEdges=[{"source": "node-1", "target": "node-2", "conditionType": None}])

    with patch("api.routes.chat.chat_workflow", side_effect=_fake_chat):
        response = client.post("/v1/chat", json=payload)

    assert response.status_code == 200
    assert captured["current_nodes"][1]["config"] == {
        "operator": "equals", "left": "{{nodes.node-1.output.output}}", "right": "urgent"}


class _RateLimitError(Exception):
    def __init__(self):
        self.code = 429


def test_chat_endpoint_레이트리밋_429_반환():
    with patch("api.routes.chat.chat_workflow", side_effect=_RateLimitError()):
        response = client.post("/v1/chat", json=CHAT_PAYLOAD)
    assert response.status_code == 429
    assert ErrorCode.RATE_LIMITED.message in response.json()["detail"]

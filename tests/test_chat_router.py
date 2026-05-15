from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from main import app
from api.middleware.credential import get_llm_credentials
from common.error_code import ErrorCode


def override_credentials():
    return {"provider": "CLAUDE", "api_key": "test-key", "user_id": "test-user"}


app.dependency_overrides[get_llm_credentials] = override_credentials
client = TestClient(app)

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

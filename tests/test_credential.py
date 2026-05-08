from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from api.schemas.response import AgentExecutionResult
from common.error_code import ErrorCode
from main import app

client = TestClient(app)

PAYLOAD = {
    "nodeId": "node-1",
    "renderedPrompt": "Hello",
}

_mock_result = AgentExecutionResult(success=True, output="ok")


def test_both_headers_present_returns_200():
    headers = {"X-LLM-Provider": "CLAUDE", "X-LLM-Api-Key": "test-key"}
    with patch("api.routes.execute.run_agent", new=AsyncMock(return_value=_mock_result)):
        response = client.post("/v1/execute", json=PAYLOAD, headers=headers)
    assert response.status_code == 200


def test_missing_provider_header_returns_422():
    headers = {"X-LLM-Api-Key": "test-key"}
    response = client.post("/v1/execute", json=PAYLOAD, headers=headers)
    assert response.status_code == 422


def test_missing_api_key_header_returns_422():
    headers = {"X-LLM-Provider": "CLAUDE"}
    response = client.post("/v1/execute", json=PAYLOAD, headers=headers)
    assert response.status_code == 422


def test_both_headers_missing_returns_422():
    response = client.post("/v1/execute", json=PAYLOAD, headers={})
    assert response.status_code == 422


def test_empty_provider_returns_400_with_error_message():
    headers = {"X-LLM-Provider": "", "X-LLM-Api-Key": "test-key"}
    response = client.post("/v1/execute", json=PAYLOAD, headers=headers)
    assert response.status_code == 400
    assert response.json()["detail"] == ErrorCode.MISSING_CREDENTIAL.message


def test_empty_api_key_returns_400_with_error_message():
    headers = {"X-LLM-Provider": "CLAUDE", "X-LLM-Api-Key": ""}
    response = client.post("/v1/execute", json=PAYLOAD, headers=headers)
    assert response.status_code == 400
    assert response.json()["detail"] == ErrorCode.MISSING_CREDENTIAL.message

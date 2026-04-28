from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from main import app
from api.schemas.response import AgentExecutionResult

client = TestClient(app)

HEADERS = {
    "X-LLM-Provider": "CLAUDE",
    "X-LLM-Api-Key": "test-key",
}

PAYLOAD = {
    "nodeId": "node-1",
    "renderedPrompt": "Hello",
}


def test_execute_success():
    mock_result = AgentExecutionResult(success=True, output="test result")
    with patch("api.routes.execute.run_agent", new=AsyncMock(return_value=mock_result)):
        response = client.post("/v1/execute", json=PAYLOAD, headers=HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["output"] == "test result"


def test_execute_failure():
    mock_result = AgentExecutionResult(success=False, errorMessage="some error")
    with patch("api.routes.execute.run_agent", new=AsyncMock(return_value=mock_result)):
        response = client.post("/v1/execute", json=PAYLOAD, headers=HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert data["errorMessage"] == "some error"

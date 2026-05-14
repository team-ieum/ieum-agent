from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from main import app
from api.schemas.response import AgentExecutionResult

client = TestClient(app)

HEADERS = {
    "X-LLM-Provider": "CLAUDE",
    "X-LLM-Api-Key": "test-key",
    "X-User-Id": "test-user",
}

PAYLOAD = {
    "nodeId": "node-1",
    "workflowExecutionId": "exec-uuid-123",
    "renderedPrompt": "Hello",
    "agentType": "simple",
    "systemMessage": "You are a helpful assistant.",
}


def test_execute_success():
    mock_result = AgentExecutionResult(success=True, output="test result")
    with patch("api.routes.execute.run_agent", new=AsyncMock(return_value=mock_result)):
        response = client.post("/v1/execute", json=PAYLOAD, headers=HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["output"] == "test result"


def test_execute_success_with_status():
    """성공 응답에 status 필드가 포함된다."""
    mock_result = AgentExecutionResult(success=True, status="COMPLETED", output="test result")
    with patch("api.routes.execute.run_agent", new=AsyncMock(return_value=mock_result)):
        response = client.post("/v1/execute", json=PAYLOAD, headers=HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "COMPLETED"


def test_execute_request_schema_agentType_default():
    """agentType 기본값이 'simple'이다."""
    from api.schemas.request import AgentNodeRequest
    req = AgentNodeRequest(nodeId="node-1", renderedPrompt="test")
    assert req.agentType == "simple"


def test_execute_request_schema_workflowExecutionId_optional():
    """workflowExecutionId는 optional이며 없어도 생성된다."""
    from api.schemas.request import AgentNodeRequest
    req = AgentNodeRequest(nodeId="node-1", renderedPrompt="test")
    assert req.workflowExecutionId is None


def test_execute_failure():
    mock_result = AgentExecutionResult(success=False, errorMessage="some error")
    with patch("api.routes.execute.run_agent", new=AsyncMock(return_value=mock_result)):
        response = client.post("/v1/execute", json=PAYLOAD, headers=HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert data["errorMessage"] == "some error"

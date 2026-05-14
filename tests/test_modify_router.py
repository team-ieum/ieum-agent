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

MODIFY_PAYLOAD = {
    "prompt": "Slack 알림 추가",
    "currentNodes": [
        {"id": "node-1", "type": "TRIGGER", "label": "트리거", "config": {}}
    ],
    "currentEdges": [],
}


def test_modify_workflow_파싱_실패_시_502_반환():
    with patch("api.routes.modify.modify_workflow", side_effect=ValueError("파싱 실패")):
        response = client.post(
            "/v1/modify-workflow",
            json=MODIFY_PAYLOAD,
        )
    assert response.status_code == 502
    assert ErrorCode.WORKFLOW_MODIFY_PARSE_FAILED.message in response.json()["detail"]


def test_modify_workflow_서버_오류_시_500_반환():
    with patch("api.routes.modify.modify_workflow", side_effect=Exception("예상치 못한 오류")):
        response = client.post(
            "/v1/modify-workflow",
            json=MODIFY_PAYLOAD,
        )
    assert response.status_code == 500
    assert ErrorCode.WORKFLOW_MODIFY_FAILED.message in response.json()["detail"]

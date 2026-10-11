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


_APP_TRIGGER_CONFIGS = [
    {"triggerType": "GMAIL_NEW_EMAIL", "serviceType": "GOOGLE", "query": "from:boss@x.com", "brand": "gmail"},
    {"triggerType": "GMAIL_NEW_EMAIL", "serviceType": "GOOGLE", "query": ""},
    {"triggerType": "GMAIL_NEW_EMAIL"},
    {"triggerType": "GITHUB_NEW_ISSUE", "serviceType": "GITHUB", "repoId": "123456",
     "_names": {"repoId": "octo/hello"}, "brand": "github"},
    {"triggerType": "GITHUB_NEW_ISSUE", "serviceType": "GITHUB", "repo": "octo/hello", "repoId": 123456},
    {"triggerType": "GITHUB_NEW_ISSUE"},   # 빌더에서 저장소를 아직 안 고른 드래프트
]


@pytest.mark.parametrize("config", _APP_TRIGGER_CONFIGS)
def test_chat_endpoint_앱_트리거_currentNodes_수용(config):
    """BE가 앱 트리거를 저장한 뒤 채팅 수정 요청이 422로 막히지 않고, config가 그대로 전달된다."""
    from api.schemas.chat import ChatResponse, ChatResponseType

    captured = {}

    async def _fake_chat(**kwargs):
        captured.update(kwargs)
        return ChatResponse(message="ok", type=ChatResponseType.WORKFLOW_MODIFIED, rawPrompt="수정")

    payload = dict(CHAT_PAYLOAD, prompt="요약 노드 추가해줘", currentNodes=[
        {"id": "node-1", "type": "TRIGGER", "label": "새 메일", "config": config},
    ], currentEdges=[])

    with patch("api.routes.chat.chat_workflow", side_effect=_fake_chat):
        response = client.post("/v1/chat", json=payload)

    assert response.status_code == 200, response.text
    assert captured["current_nodes"][0]["config"] == config


@pytest.mark.parametrize("trigger_type", ["SLACK_NEW_MESSAGE", "gmail_new_email", ""])
def test_chat_endpoint_모르는_triggerType은_422(trigger_type):
    payload = dict(CHAT_PAYLOAD, currentNodes=[
        {"id": "node-1", "type": "TRIGGER", "label": "시작", "config": {"triggerType": trigger_type}},
    ], currentEdges=[])
    with patch("api.routes.chat.chat_workflow") as chat:
        response = client.post("/v1/chat", json=payload)
    assert response.status_code == 422
    chat.assert_not_called()


# BE가 저장하는 ACTION 노드 모양(ieum-backend WorkflowNodeRequestValidationTest·WorkflowNodeResponseTest)과
# 빌더가 도구 인자를 아직 안 채운 드래프트(카탈로그 fixed 그대로).
_ACTION_CONFIGS = [
    {"tools": [{"name": "builtin:github_create_issue",
                "config": {"owner": "ieum", "repo": "demo",
                           "title": "{{nodes.node-list.output.issues.0.title}}"}}],
     "brand": "github", "serviceType": "GITHUB"},
    {"tools": [{"name": "builtin:github_list_issues", "config": {"owner": "ieum"}}]},
    {"tools": [{"name": "slack", "config": {"webhookCredentialId": "wh-1", "message": "안녕",
                                            "_names": {"webhookCredentialId": "내 채널"}}}],
     "serviceType": "SLACK", "brand": "slack"},
    {"tools": [{"name": "builtin:notion_create_page"}], "serviceType": "NOTION"},
]


@pytest.mark.parametrize("config", _ACTION_CONFIGS)
def test_chat_endpoint_ACTION_currentNodes_수용(config):
    """빌더로 만든 ACTION 노드가 든 워크플로우의 채팅 수정 요청이 422로 막히지 않고, config가 그대로 전달된다."""
    from api.schemas.chat import ChatResponse, ChatResponseType

    captured = {}

    async def _fake_chat(**kwargs):
        captured.update(kwargs)
        return ChatResponse(message="ok", type=ChatResponseType.WORKFLOW_MODIFIED, rawPrompt="수정")

    payload = dict(CHAT_PAYLOAD, prompt="요약 노드 추가해줘", currentNodes=[
        {"id": "node-1", "type": "TRIGGER", "label": "시작", "config": {"triggerType": "MANUAL"}},
        {"id": "node-2", "type": "ACTION", "label": "액션", "config": config},
    ], currentEdges=[{"source": "node-1", "target": "node-2"}])

    with patch("api.routes.chat.chat_workflow", side_effect=_fake_chat):
        response = client.post("/v1/chat", json=payload)

    assert response.status_code == 200, response.text
    assert captured["current_nodes"][1]["config"] == config


def test_chat_endpoint_카탈로그_ACTION_fixed는_전부_수용():
    """카탈로그의 모든 action.* fixed(빌더가 노드를 놓는 순간의 모양)가 채팅 스키마를 통과한다.
    액션을 새로 추가할 때 스키마와 어긋나면 여기서 잡힌다."""
    from core import template_registry as tr

    actions = [t for t in tr.all_entries() if t["node_type"] == "ACTION" and t["builder"]]
    assert actions
    for t in actions:
        payload = dict(CHAT_PAYLOAD, currentNodes=[
            {"id": "node-1", "type": "TRIGGER", "label": "시작", "config": {"triggerType": "MANUAL"}},
            {"id": "node-2", "type": t["fixed"]["type"], "label": t["title"],
             "config": {**t["fixed"]["config"], "brand": t["brand"]}},
        ], currentEdges=[])
        with patch("api.routes.chat.chat_workflow", side_effect=ValueError("파싱 실패")):
            response = client.post("/v1/chat", json=payload)
        # 라우트는 chat_workflow의 ValueError를 CHAT_PARSE_FAILED(502)로 매핑한다 — 422(스키마 거부)가 아니면 통과
        assert response.status_code == ErrorCode.CHAT_PARSE_FAILED.status_code, (t["id"], response.text)


def test_chat_endpoint_ACTION_tools_비리스트는_422():
    payload = dict(CHAT_PAYLOAD, currentNodes=[
        {"id": "node-1", "type": "TRIGGER", "label": "시작", "config": {"triggerType": "MANUAL"}},
        {"id": "node-2", "type": "ACTION", "label": "액션", "config": {"tools": 5}},
    ], currentEdges=[])
    with patch("api.routes.chat.chat_workflow") as chat:
        response = client.post("/v1/chat", json=payload)
    assert response.status_code == 422
    chat.assert_not_called()


@pytest.mark.parametrize("node_type", ["ACTIONS", "UNKNOWN"])
def test_chat_endpoint_모르는_노드_type은_422(node_type):
    payload = dict(CHAT_PAYLOAD, currentNodes=[
        {"id": "node-1", "type": "TRIGGER", "label": "시작", "config": {"triggerType": "MANUAL"}},
        {"id": "node-2", "type": node_type, "label": "x", "config": {"tools": [{"name": "slack"}]}},
    ], currentEdges=[])
    with patch("api.routes.chat.chat_workflow") as chat:
        response = client.post("/v1/chat", json=payload)
    assert response.status_code == 422
    chat.assert_not_called()

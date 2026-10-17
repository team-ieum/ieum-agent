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
        {"id": "node-1", "event": "manual.run", "label": "시작", "config": {}},
        {"id": "node-2", "event": "branch.if", "label": "분기", "config": {
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


_APP_TRIGGER_NODES = [
    ("gmail.new_email", {"query": "from:boss@x.com", "brand": "gmail"}),
    ("gmail.new_email", {"query": ""}),
    ("gmail.new_email", {}),
    ("github.new_issue", {"repoId": "123456", "_names": {"repoId": "octo/hello"}, "brand": "github"}),
    ("github.new_issue", {"repo": "octo/hello", "repoId": 123456}),
    ("github.new_issue", {}),   # 빌더에서 저장소를 아직 안 고른 드래프트
]


@pytest.mark.parametrize("event,config", _APP_TRIGGER_NODES)
def test_chat_endpoint_앱_트리거_currentNodes_수용(event, config):
    """BE가 앱 트리거를 저장한 뒤 채팅 수정 요청이 422로 막히지 않고, config가 그대로 전달된다."""
    from api.schemas.chat import ChatResponse, ChatResponseType

    captured = {}

    async def _fake_chat(**kwargs):
        captured.update(kwargs)
        return ChatResponse(message="ok", type=ChatResponseType.WORKFLOW_MODIFIED, rawPrompt="수정")

    payload = dict(CHAT_PAYLOAD, prompt="요약 노드 추가해줘", currentNodes=[
        {"id": "node-1", "event": event, "label": "새 메일", "config": config},
    ], currentEdges=[])

    with patch("api.routes.chat.chat_workflow", side_effect=_fake_chat):
        response = client.post("/v1/chat", json=payload)

    assert response.status_code == 200, response.text
    assert captured["current_nodes"][0]["config"] == config
    assert captured["current_nodes"][0]["type"] == "TRIGGER"


@pytest.mark.parametrize("node", [
    {"id": "node-1", "type": "TRIGGER", "label": "시작", "config": {"triggerType": "MANUAL"}},   # event 없음
    {"id": "node-1", "type": "TRIGGER", "label": "시작", "event": None, "config": {}},
    {"id": "node-1", "label": "시작", "event": "stripe.new_payment", "config": {}},             # 모르는 event + type 없음
])
def test_chat_endpoint_event_없는_노드는_422(node):
    payload = dict(CHAT_PAYLOAD, currentNodes=[node], currentEdges=[])
    with patch("api.routes.chat.chat_workflow") as chat:
        response = client.post("/v1/chat", json=payload)
    assert response.status_code == 422
    chat.assert_not_called()


# BE가 저장하는 ACTION 노드 모양(spec §4: 칸 값은 config.<칸>)과 빌더가 칸을 아직 안 채운 드래프트.
_ACTION_NODES = [
    ("github.create_issue", {"owner": "ieum", "repo": "demo",
                             "title": "{{nodes.node-list.output.issues.0.title}}", "brand": "github"}),
    ("github.list_issues", {"owner": "ieum"}),
    ("slack.send_message", {"webhookCredentialId": "wh-1", "message": "안녕",
                            "_names": {"webhookCredentialId": "내 채널"}, "brand": "slack"}),
    ("notion.create_page", {}),
]


@pytest.mark.parametrize("event,config", _ACTION_NODES)
def test_chat_endpoint_ACTION_currentNodes_수용(event, config):
    """빌더로 만든 ACTION 노드가 든 워크플로우의 채팅 수정 요청이 422로 막히지 않고, config가 그대로 전달된다."""
    from api.schemas.chat import ChatResponse, ChatResponseType

    captured = {}

    async def _fake_chat(**kwargs):
        captured.update(kwargs)
        return ChatResponse(message="ok", type=ChatResponseType.WORKFLOW_MODIFIED, rawPrompt="수정")

    payload = dict(CHAT_PAYLOAD, prompt="요약 노드 추가해줘", currentNodes=[
        {"id": "node-1", "event": "manual.run", "label": "시작", "config": {}},
        {"id": "node-2", "event": event, "label": "액션", "config": config},
    ], currentEdges=[{"source": "node-1", "target": "node-2"}])

    with patch("api.routes.chat.chat_workflow", side_effect=_fake_chat):
        response = client.post("/v1/chat", json=payload)

    assert response.status_code == 200, response.text
    assert captured["current_nodes"][1]["config"] == config
    assert captured["current_nodes"][1]["type"] == "ACTION"


def test_chat_endpoint_카탈로그_앱_항목_fixed는_전부_수용():
    """카탈로그의 모든 builder TRIGGER·ACTION 항목 fixed(빌더가 노드를 놓는 순간의 모양)에 event를 실은 노드가
    채팅 스키마를 통과하고 type이 카탈로그 node_type이 된다. 앱 이벤트를 추가할 때 스키마와 어긋나면 여기서 잡힌다."""
    from api.schemas.chat import ChatResponse, ChatResponseType
    from core import template_registry as tr

    entries = [t for t in tr.builder_entries() if t["node_type"] in ("TRIGGER", "ACTION")]
    assert entries
    for t in entries:
        captured = {}

        async def _fake_chat(**kwargs):
            captured.update(kwargs)
            return ChatResponse(message="ok", type=ChatResponseType.WORKFLOW_MODIFIED, rawPrompt="수정")

        cron = {"cron": "0 9 * * *"} if t.get("trigger_type") == "SCHEDULE" else {}
        payload = dict(CHAT_PAYLOAD, currentNodes=[
            {"id": "node-1", "event": t["id"], "label": t["title"],
             "config": {**t["fixed"]["config"], **cron, "brand": t["brand"]}},
        ], currentEdges=[])
        with patch("api.routes.chat.chat_workflow", side_effect=_fake_chat):
            response = client.post("/v1/chat", json=payload)
        assert response.status_code == 200, (t["id"], response.text)
        assert captured["current_nodes"][0]["type"] == t["node_type"], t["id"]


def test_chat_endpoint_요청_type은_event로_덮인다():
    from api.schemas.chat import ChatResponse, ChatResponseType

    captured = {}

    async def _fake_chat(**kwargs):
        captured.update(kwargs)
        return ChatResponse(message="ok", type=ChatResponseType.WORKFLOW_MODIFIED, rawPrompt="수정")

    payload = dict(CHAT_PAYLOAD, currentNodes=[
        {"id": "node-1", "event": "slack.send_message", "type": "TRIGGER", "label": "알림", "config": {"tools": 5}},
    ], currentEdges=[])
    with patch("api.routes.chat.chat_workflow", side_effect=_fake_chat):
        response = client.post("/v1/chat", json=payload)
    assert response.status_code == 200, response.text
    assert captured["current_nodes"][0]["type"] == "ACTION"


@pytest.mark.parametrize("node_type", ["ACTIONS", "UNKNOWN"])
def test_chat_endpoint_모르는_노드_type은_422(node_type):
    payload = dict(CHAT_PAYLOAD, currentNodes=[
        {"id": "node-1", "event": "manual.run", "label": "시작", "config": {}},
        {"id": "node-2", "event": "stripe.new_payment", "type": node_type, "label": "x", "config": {}},
    ], currentEdges=[])
    with patch("api.routes.chat.chat_workflow") as chat:
        response = client.post("/v1/chat", json=payload)
    assert response.status_code == 422
    chat.assert_not_called()


def test_chat_endpoint_currentNodes_event_보존():
    """event가 chat_workflow(current_nodes=...)까지 가야 pass-through 원본이 event를 잃지 않는다.
    agent가 모르는 event(BE가 먼저 올린 새 이벤트)도 422 없이 받은 type 그대로 간다."""
    from api.schemas.chat import ChatResponse, ChatResponseType

    captured = {}

    async def _fake_chat(**kwargs):
        captured.update(kwargs)
        return ChatResponse(message="ok", type=ChatResponseType.WORKFLOW_MODIFIED, rawPrompt="수정")

    payload = dict(CHAT_PAYLOAD, prompt="요약 노드 추가해줘", currentNodes=[
        {"id": "node-1", "type": "TRIGGER", "label": "새 메일", "event": "gmail.new_email", "config": {}},
        {"id": "node-2", "type": "TRIGGER", "label": "모르는 이벤트", "event": "stripe.new_payment", "config": {}},
    ], currentEdges=[])

    with patch("api.routes.chat.chat_workflow", side_effect=_fake_chat):
        response = client.post("/v1/chat", json=payload)

    assert response.status_code == 200, response.text
    assert [(n["event"], n["type"]) for n in captured["current_nodes"]] == [
        ("gmail.new_email", "TRIGGER"), ("stripe.new_payment", "TRIGGER")]

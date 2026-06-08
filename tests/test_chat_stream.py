from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from main import app
from api.middleware.credential import get_llm_credentials
from api.schemas.chat import ChatResponse, ChatResponseType
from core.workflow_chat import chat_workflow_stream


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
def _override_credentials():
    app.dependency_overrides[get_llm_credentials] = override_credentials
    yield
    app.dependency_overrides.clear()


CHAT_PAYLOAD = {
    "prompt": "워크플로우 만들어줘",
    "availableIntegrations": [],
    "unavailableIntegrations": [],
}


def _make_response():
    return ChatResponse(
        message="완성됐어요",
        type=ChatResponseType.WORKFLOW_GENERATED,
        rawPrompt="워크플로우 만들어줘",
    )


# ─────────────────── chat_workflow_stream 단위 ───────────────────

@pytest.mark.asyncio
async def test_stream_단계_이벤트_후_done_순서로_방출():
    async def fake(on_stage=None, **kwargs):
        on_stage("designing")
        on_stage("reviewing")
        return _make_response()

    with patch("core.workflow_chat.chat_workflow", fake):
        events = [ev async for ev in chat_workflow_stream(prompt="x")]

    assert events[0] == ("stage", {"stage": "designing"})
    assert events[1] == ("stage", {"stage": "reviewing"})
    assert events[2][0] == "done"
    assert isinstance(events[2][1], ChatResponse)
    assert events[2][1].type == ChatResponseType.WORKFLOW_GENERATED


@pytest.mark.asyncio
async def test_stream_예외_시_error_이벤트_방출():
    async def fake(on_stage=None, **kwargs):
        on_stage("designing")
        raise ValueError("boom")

    with patch("core.workflow_chat.chat_workflow", fake):
        events = [ev async for ev in chat_workflow_stream(prompt="x")]

    assert events[0] == ("stage", {"stage": "designing"})
    assert events[-1][0] == "error"
    assert "boom" in events[-1][1]["message"]


# ─────────────────── /v1/chat/stream 엔드포인트 ───────────────────

def test_chat_stream_엔드포인트_SSE_응답():
    async def fake(on_stage=None, **kwargs):
        on_stage("designing")
        on_stage("reviewing")
        return _make_response()

    with patch("core.workflow_chat.chat_workflow", fake):
        response = client.post("/v1/chat/stream", json=CHAT_PAYLOAD)

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]

    body = response.text
    assert "event: stage" in body
    assert "designing" in body
    assert "reviewing" in body
    assert "event: done" in body
    assert "WORKFLOW_GENERATED" in body

import inspect
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from main import app
from api.middleware.credential import get_llm_credentials
from api.schemas.chat import BuildChatResponse, ChatResponseType
from core.builder.engine import build_turn


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
    "workflowId": "wf-1",
    "availableIntegrations": [],
    "unavailableIntegrations": [],
}


# ─────────────────── /v1/chat/stream 엔드포인트 = 대화형 엔진 (IEUM-AI-84) ───────────────────

def _fake_stream(captured: dict, response=None):
    async def fake(**kwargs):
        captured.update(kwargs)
        yield "stage", {"stage": "designing"}
        yield "done", response or BuildChatResponse(message="안녕", type=ChatResponseType.CLARIFICATION_NEEDED,
                                                    rawPrompt=kwargs["prompt"])
    return fake


def test_chat_stream은_새_엔진에_위임_토큰과_dict_노드를_넘긴다():
    captured = {}
    nodes = [{"id": "n1", "event": "schedule.every", "type": "TRIGGER", "label": "매일", "config": {},
              "position": {"x": 1, "y": 2}},
             {"id": "n2", "event": "http.request", "label": "요청", "config": {}}]
    payload = {**CHAT_PAYLOAD, "currentNodes": nodes, "currentEdges": [{"id": "e", "source": "n1", "target": "n2"}]}
    with patch("api.routes.chat.build_turn_stream", _fake_stream(captured)):
        response = client.post("/v1/chat/stream", json=payload, headers={"X-Delegation-Token": "dt"})
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert captured["delegation_token"] == "dt" and captured["workflow_id"] == "wf-1"
    assert captured["current_nodes"] == nodes
    assert captured["current_edges"] == [{"id": "e", "source": "n1", "target": "n2"}]
    assert captured["provider"] == "CLAUDE" and captured["user_id"] == "test-user"
    # 진짜 build_turn 시그니처에 묶여야 한다 — 모르는 kwarg(key_mode 등)를 넘기면 매 턴 TypeError
    inspect.signature(build_turn).bind(**captured)
    body = response.text
    assert "event: stage" in body and "designing" in body and "event: done" in body
    # 기본값 buildState도 싣는다(exclude_unset/exclude_defaults 금지)
    assert '"buildState":"BUILDING"' in body


def test_chat_stream_done은_buildState_DONE과_노드를_싣는다():
    done = BuildChatResponse(message="완료", type=ChatResponseType.WORKFLOW_MODIFIED, rawPrompt="네",
                             nodes=[{"id": "n1", "event": "manual.run", "label": "시작", "config": {}}],
                             edges=[{"id": "e-keep", "source": "n1", "target": "n1"}], buildState="DONE")
    with patch("api.routes.chat.build_turn_stream", _fake_stream({}, done)):
        body = client.post("/v1/chat/stream", json=CHAT_PAYLOAD).text
    assert '"buildState":"DONE"' in body and '"type":"WORKFLOW_MODIFIED"' in body and '"event":"manual.run"' in body
    assert '"id":"e-keep"' in body  # 응답 엣지 id 보존


def test_chat_stream_위임_토큰이_없으면_None():
    captured = {}
    with patch("api.routes.chat.build_turn_stream", _fake_stream(captured)):
        client.post("/v1/chat/stream", json=CHAT_PAYLOAD)
    assert captured["delegation_token"] is None


def _post_model(model=..., credentials=None) -> dict:
    """model 필드를 실어(... 이면 생략) 보내고 엔진에 넘어간 kwargs를 돌려준다."""
    if credentials:
        app.dependency_overrides[get_llm_credentials] = lambda: credentials
    payload = CHAT_PAYLOAD if model is ... else {**CHAT_PAYLOAD, "model": model}
    captured = {}
    with patch("api.routes.chat.build_turn_stream", _fake_stream(captured)):
        assert client.post("/v1/chat/stream", json=payload).status_code == 200
    return captured


def test_chat_stream은_model을_엔진에_넘긴다():
    captured = _post_model("claude-sonnet-5-5")
    assert captured["model"] == "claude-sonnet-5-5"
    inspect.signature(build_turn).bind(**captured)


def test_chat_stream_model_앞뒤_공백은_벗긴다():
    assert _post_model(" claude-sonnet-5-5 ")["model"] == "claude-sonnet-5-5"


def test_chat_stream_공백_model은_None():
    assert _post_model("   ")["model"] is None


def test_chat_stream_model_없으면_None():
    assert _post_model()["model"] is None


def test_chat_stream_명시_null_model은_None():
    assert _post_model(None)["model"] is None


def test_chat_stream_100자_model은_통과():
    assert _post_model("a" * 100)["model"] == "a" * 100


@pytest.mark.parametrize("model", [
    "a" * 101,
    "models/gemini-2.5-flash",
    "anthropic/claude-sonnet-5-5",
    "projects/p/locations/us-central1/endpoints/e",
    "claude sonnet",
    5,
    ["claude-sonnet-5-5"],
])
def test_chat_stream_bare_id가_아닌_model은_422(model):
    with patch("api.routes.chat.build_turn_stream") as fake:
        assert client.post("/v1/chat/stream", json={**CHAT_PAYLOAD, "model": model}).status_code == 422
    fake.assert_not_called()


def test_chat_stream_platform_키면_model을_버린다():
    creds = {**override_credentials(), "key_mode": "platform"}
    assert _post_model("claude-sonnet-5-5", credentials=creds)["model"] is None


def test_chat_stream_id_없는_노드는_422():
    payload = {**CHAT_PAYLOAD, "currentNodes": [{"id": " ", "event": "manual.run"}]}
    with patch("api.routes.chat.build_turn_stream") as fake:
        assert client.post("/v1/chat/stream", json=payload).status_code == 422
    fake.assert_not_called()


def test_REST_채팅은_410():
    """새 엔진은 WebSocket 스트림 전용 — 블로킹 /v1/chat은 옛 엔진과 함께 지웠다."""
    response = client.post("/v1/chat", json=CHAT_PAYLOAD)
    assert response.status_code == 410
    assert response.json() == {"detail": "채팅은 워크플로우 화면의 실시간 채팅에서만 쓸 수 있어요."}

"""대화형 생성 BE 클라이언트 — 위임 토큰 헤더·경로 인코딩·결과 모양 (spec 2026-10-28 §7.3, IEUM-AI-84).
BE를 실제로 부르지 않는다(pytest가 배포 게이트) — 공유 클라이언트를 patch한다."""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.builder.backend import TEST_TIMEOUT_SECONDS, Backend
from core.config import settings


def _response(status: int, body) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.is_success = 200 <= status < 300
    if isinstance(body, Exception):
        r.json.side_effect = body
    else:
        r.json.return_value = body
    return r


@pytest.fixture
def be(monkeypatch):
    monkeypatch.setattr(settings, "BACKEND_BASE_URL", "http://be.internal:8080/")
    client = MagicMock()
    client.request = AsyncMock(return_value=_response(200, {"success": True, "data": {"items": []}}))
    with patch("core.builder.backend.get_http_client", return_value=client):
        yield client


@pytest.mark.asyncio
async def test_옵션_조회는_경로를_인코딩하고_쿼리는_params로(be):
    result = await Backend("tok").options("google.worksheets", {"spreadsheet_id": "a/b?c", "q": "회의"})
    assert result == {"data": {"items": []}}
    method, url = be.request.call_args.args
    kwargs = be.request.call_args.kwargs
    assert (method, url) == ("GET", "http://be.internal:8080/api/v1/integrations/google/options/worksheets")
    assert kwargs["params"] == {"spreadsheet_id": "a/b?c", "q": "회의"}
    assert kwargs["headers"] == {"X-Delegation-Token": "tok"}
    assert kwargs["timeout"] is httpx.USE_CLIENT_DEFAULT


@pytest.mark.asyncio
async def test_노드_테스트는_POST_본문_node와_190초(be):
    node = {"id": "../x?y", "event": "manual.run", "label": "t", "config": {}}
    await Backend("tok").test_node("wf/1", node)
    method, url = be.request.call_args.args
    kwargs = be.request.call_args.kwargs
    assert method == "POST"
    assert url == "http://be.internal:8080/api/v1/workflows/wf%2F1/nodes/..%2Fx%3Fy/test"
    assert kwargs["json"] == {"node": node}
    assert kwargs["timeout"] == TEST_TIMEOUT_SECONDS == 190.0


@pytest.mark.asyncio
async def test_연결_목록과_칸_목록_경로(be):
    await Backend("tok").connections()
    assert be.request.call_args.args == ("GET", "http://be.internal:8080/api/v1/oauth/connections")
    await Backend("tok").fields("notion.properties", {"data_source_id": "d1"})
    assert be.request.call_args.args == ("GET", "http://be.internal:8080/api/v1/integrations/notion/fields/properties")


@pytest.mark.asyncio
async def test_오류_본문은_code_message_data로(be):
    be.request.return_value = _response(400, {"success": False, "code": "TEST_SAMPLE_MISSING",
                                              "message": "앞 노드를 먼저", "data": {"missingNodeIds": ["a"]}})
    result = await Backend("tok").test_node("wf", {"id": "n"})
    assert result == {"error": {"code": "TEST_SAMPLE_MISSING", "message": "앞 노드를 먼저",
                                "data": {"missingNodeIds": ["a"]}}}


@pytest.mark.asyncio
async def test_JSON이_아닌_오류_본문(be):
    be.request.return_value = _response(502, ValueError("not json"))
    result = await Backend("tok").connections()
    assert result["error"]["code"] == "HTTP_502"


@pytest.mark.asyncio
async def test_네트워크_오류는_BACKEND_UNAVAILABLE(be):
    be.request.side_effect = httpx.ConnectError("down")
    assert (await Backend("tok").connections())["error"]["code"] == "BACKEND_UNAVAILABLE"


@pytest.mark.asyncio
async def test_토큰이나_주소가_없으면_부르지_않는다(be, monkeypatch):
    assert (await Backend(None).connections())["error"]["code"] == "BACKEND_UNAVAILABLE"
    monkeypatch.setattr(settings, "BACKEND_BASE_URL", "")
    assert (await Backend("tok").connections())["error"]["code"] == "BACKEND_UNAVAILABLE"
    be.request.assert_not_called()

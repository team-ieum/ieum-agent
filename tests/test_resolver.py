import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.workflow_chat import _resolve_resources, _RESOLVER_EXCLUDED_TOOLS


def _runner_mock(text_output: str):
    event = MagicMock()
    event.is_final_response.return_value = True
    event.content.parts = [type("Part", (), {"text": text_output})()]

    async def run_async(**kwargs):
        yield event

    runner = MagicMock()
    runner.run_async = run_async
    return runner


def _session_service_mock():
    svc = MagicMock()
    svc.create_session = AsyncMock(return_value=MagicMock(id="s"))
    svc.delete_session = AsyncMock(return_value=None)
    return svc


def _tool(name: str):
    t = MagicMock()
    t.name = name
    return t


@pytest.mark.asyncio
async def test_no_lookup_tools_returns_empty():
    # 발송 전용 도구만 있으면 조회 대상이 없으므로 빈 문자열
    tools = [_tool("send_slack_message"), _tool("send_discord_webhook")]
    out = await _resolve_resources(tools, "노션에 저장", None, _session_service_mock(), "u1")
    assert out == ""


@pytest.mark.asyncio
async def test_excluded_tools_contract():
    assert "send_slack_message" in _RESOLVER_EXCLUDED_TOOLS
    assert "send_discord_webhook" in _RESOLVER_EXCLUDED_TOOLS


@pytest.mark.asyncio
async def test_none_output_returns_empty():
    tools = [_tool("notion_search")]
    svc = _session_service_mock()
    with patch("core.workflow_chat.Runner", return_value=_runner_mock("NONE")), \
         patch("core.workflow_chat.get_current_time_info", return_value=""), \
         patch("core.workflow_chat.LlmAgent", return_value=MagicMock()):
        out = await _resolve_resources(tools, "안녕", None, svc, "u1")
    assert out == ""


@pytest.mark.asyncio
async def test_resolved_text_returned():
    tools = [_tool("notion_search")]
    svc = _session_service_mock()
    resolved = '- Notion 페이지: "IT 트렌드" → abc123'
    with patch("core.workflow_chat.Runner", return_value=_runner_mock(resolved)), \
         patch("core.workflow_chat.get_current_time_info", return_value=""), \
         patch("core.workflow_chat.LlmAgent", return_value=MagicMock()):
        out = await _resolve_resources(tools, "IT 트렌드 노션에 저장", None, svc, "u1")
    assert "abc123" in out
    # 조회 세션은 생성 후 정리되어야 한다
    svc.delete_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_failure_returns_empty_not_raises():
    tools = [_tool("notion_search")]
    svc = _session_service_mock()
    with patch("core.workflow_chat.Runner", side_effect=RuntimeError("boom")), \
         patch("core.workflow_chat.get_current_time_info", return_value=""), \
         patch("core.workflow_chat.LlmAgent", return_value=MagicMock()):
        out = await _resolve_resources(tools, "x", None, svc, "u1")
    assert out == ""

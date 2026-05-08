import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.error_code import ErrorCode
from tools.mcp import call_mcp_tool


# ---------------------------------------------------------------------------
# Content 헬퍼 클래스 (hasattr 분기 검증용)
# ---------------------------------------------------------------------------

class _TextContent:
    def __init__(self, text: str):
        self.text = text


class _DataContent:
    def __init__(self, data):
        self.data = data


# ---------------------------------------------------------------------------
# Mock 생성 헬퍼
# ---------------------------------------------------------------------------

def _make_sse_and_session(
    call_tool_result=None,
    call_tool_side_effect=None,
    sse_aenter_side_effect=None,
):
    """sse_client + ClientSession async context manager mock 쌍을 반환한다."""
    mock_read, mock_write = MagicMock(), MagicMock()

    mock_session = AsyncMock()
    if call_tool_side_effect:
        mock_session.call_tool.side_effect = call_tool_side_effect
    else:
        mock_session.call_tool.return_value = call_tool_result

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_sse_ctx = AsyncMock()
    if sse_aenter_side_effect:
        mock_sse_ctx.__aenter__ = AsyncMock(side_effect=sse_aenter_side_effect)
    else:
        mock_sse_ctx.__aenter__ = AsyncMock(return_value=(mock_read, mock_write))
    mock_sse_ctx.__aexit__ = AsyncMock(return_value=None)

    return mock_sse_ctx, mock_session_ctx


# ---------------------------------------------------------------------------
# 성공 케이스
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_text_content_returns_text():
    result_obj = MagicMock(isError=False, content=[_TextContent("hello world")])
    sse_ctx, session_ctx = _make_sse_and_session(call_tool_result=result_obj)

    with patch("tools.mcp.sse_client", return_value=sse_ctx), \
         patch("tools.mcp.ClientSession", return_value=session_ctx):
        result = await call_mcp_tool("http://mcp-server/sse", "my_tool", {})

    assert result == "hello world"


@pytest.mark.asyncio
async def test_data_content_returns_json_string():
    data = {"key": "value", "num": 42}
    result_obj = MagicMock(isError=False, content=[_DataContent(data)])
    sse_ctx, session_ctx = _make_sse_and_session(call_tool_result=result_obj)

    with patch("tools.mcp.sse_client", return_value=sse_ctx), \
         patch("tools.mcp.ClientSession", return_value=session_ctx):
        result = await call_mcp_tool("http://mcp-server/sse", "my_tool", {})

    assert result == json.dumps(data, ensure_ascii=False)


@pytest.mark.asyncio
async def test_empty_content_returns_default_message():
    result_obj = MagicMock(isError=False, content=[])
    sse_ctx, session_ctx = _make_sse_and_session(call_tool_result=result_obj)

    with patch("tools.mcp.sse_client", return_value=sse_ctx), \
         patch("tools.mcp.ClientSession", return_value=session_ctx):
        result = await call_mcp_tool("http://mcp-server/sse", "my_tool", {})

    assert result == "MCP Tool 실행 완료 (결과 없음)"


@pytest.mark.asyncio
async def test_multi_content_joined_with_newline():
    result_obj = MagicMock(isError=False, content=[
        _TextContent("line1"),
        _TextContent("line2"),
        _TextContent("line3"),
    ])
    sse_ctx, session_ctx = _make_sse_and_session(call_tool_result=result_obj)

    with patch("tools.mcp.sse_client", return_value=sse_ctx), \
         patch("tools.mcp.ClientSession", return_value=session_ctx):
        result = await call_mcp_tool("http://mcp-server/sse", "my_tool", {})

    assert result == "line1\nline2\nline3"


# ---------------------------------------------------------------------------
# 에러 케이스
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_is_error_true_returns_tool_execution_failed_message():
    result_obj = MagicMock(isError=True, content=[_TextContent("something went wrong")])
    sse_ctx, session_ctx = _make_sse_and_session(call_tool_result=result_obj)

    with patch("tools.mcp.sse_client", return_value=sse_ctx), \
         patch("tools.mcp.ClientSession", return_value=session_ctx):
        result = await call_mcp_tool("http://mcp-server/sse", "my_tool", {})

    assert ErrorCode.TOOL_EXECUTION_FAILED.message in result


@pytest.mark.asyncio
async def test_sse_client_connection_failure_returns_error_message():
    sse_ctx, session_ctx = _make_sse_and_session(
        sse_aenter_side_effect=Exception("connection refused"),
    )

    with patch("tools.mcp.sse_client", return_value=sse_ctx), \
         patch("tools.mcp.ClientSession", return_value=session_ctx):
        result = await call_mcp_tool("http://mcp-server/sse", "my_tool", {})

    assert ErrorCode.TOOL_EXECUTION_FAILED.message in result


@pytest.mark.asyncio
async def test_call_tool_failure_returns_error_message():
    sse_ctx, session_ctx = _make_sse_and_session(
        call_tool_side_effect=Exception("tool not found"),
    )

    with patch("tools.mcp.sse_client", return_value=sse_ctx), \
         patch("tools.mcp.ClientSession", return_value=session_ctx):
        result = await call_mcp_tool("http://mcp-server/sse", "my_tool", {})

    assert ErrorCode.TOOL_EXECUTION_FAILED.message in result

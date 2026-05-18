import json
import smtplib
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from common.error_code import ToolErrorCode
from tools.discord import send_discord_webhook
from tools.gmail import send_gmail
from tools.mcp import call_mcp_tool
from tools.slack import send_slack_message
from tools import get_tools_for_request


def _make_async_http_client(post_mock):
    """httpx.AsyncClient 컨텍스트 매니저 mock 생성 헬퍼"""
    mock_client = AsyncMock()
    mock_client.post = post_mock
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_slack_message_success():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client = _make_async_http_client(AsyncMock(return_value=mock_response))

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await send_slack_message(
            webhook_url="https://hooks.slack.com/test",
            message="hello",
        )

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["message"] == "Slack 메시지 발송 성공"


@pytest.mark.asyncio
async def test_send_slack_message_failure():
    mock_request = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 400
    error = httpx.HTTPStatusError("bad request", request=mock_request, response=mock_response)
    mock_client = _make_async_http_client(AsyncMock(side_effect=error))

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await send_slack_message(
            webhook_url="https://hooks.slack.com/test",
            message="hello",
        )

    parsed = json.loads(result)
    assert "error" in parsed
    assert ToolErrorCode.EXECUTION_FAILED.message in parsed["error"]


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_discord_webhook_success():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client = _make_async_http_client(AsyncMock(return_value=mock_response))

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await send_discord_webhook(
            webhook_url="https://discord.com/api/webhooks/test",
            content="hello",
        )

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["message"] == "Discord 메시지 발송 성공"


@pytest.mark.asyncio
async def test_send_discord_webhook_failure():
    mock_request = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 403
    error = httpx.HTTPStatusError("forbidden", request=mock_request, response=mock_response)
    mock_client = _make_async_http_client(AsyncMock(side_effect=error))

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await send_discord_webhook(
            webhook_url="https://discord.com/api/webhooks/test",
            content="hello",
        )

    parsed = json.loads(result)
    assert "error" in parsed
    assert ToolErrorCode.EXECUTION_FAILED.message in parsed["error"]


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_gmail_success():
    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = await send_gmail(
            to="recipient@example.com",
            subject="Test",
            body="Hello",
            sender_email="sender@gmail.com",
            sender_password="app-password",
        )

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert "recipient@example.com" in parsed["message"]
    assert "성공" in parsed["message"]


@pytest.mark.asyncio
async def test_send_gmail_auth_failure():
    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"auth failed")
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = await send_gmail(
            to="recipient@example.com",
            subject="Test",
            body="Hello",
            sender_email="sender@gmail.com",
            sender_password="wrong-password",
        )

    parsed = json.loads(result)
    assert "error" in parsed
    assert ToolErrorCode.EXECUTION_FAILED.message in parsed["error"]


# ---------------------------------------------------------------------------
# get_tools_for_request
# ---------------------------------------------------------------------------

def test_get_tools_for_request():
    result = get_tools_for_request([{"name": "slack"}, {"name": "discord"}])
    assert len(result) == 2


def test_get_tools_for_request_unknown():
    result = get_tools_for_request([{"name": "unknown_tool"}])
    assert result == []


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------

def _make_mcp_text_content(text: str):
    """text 속성만 가진 MCP content mock을 생성한다."""
    mock_content = MagicMock(spec=["text"])
    mock_content.text = text
    return mock_content


def _make_mcp_session_context(call_tool_result):
    """sse_client + ClientSession 2단계 컨텍스트 매니저 mock을 생성한다.

    Returns:
        (mock_sse_cm, mock_session) 튜플
        — patch("tools.mcp.sse_client") return_value로 mock_sse_cm 사용
        — patch("tools.mcp.ClientSession") return_value로 mock_session 사용
    """
    mock_session = AsyncMock()
    mock_session.initialize = AsyncMock()
    mock_session.call_tool = AsyncMock(return_value=call_tool_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    mock_sse_cm = MagicMock()
    mock_streams = (AsyncMock(), AsyncMock())
    mock_sse_cm.__aenter__ = AsyncMock(return_value=mock_streams)
    mock_sse_cm.__aexit__ = AsyncMock(return_value=None)

    return mock_sse_cm, mock_session


@pytest.mark.asyncio
async def test_call_mcp_tool_success_returns_json():
    """MCP 도구 성공 시 success=True와 output 필드를 포함한 JSON을 반환한다."""
    mock_result = MagicMock()
    mock_result.isError = False
    mock_result.content = [_make_mcp_text_content("some result text")]

    mock_sse_cm, mock_session = _make_mcp_session_context(mock_result)

    with patch("tools.mcp.sse_client", return_value=mock_sse_cm), \
         patch("tools.mcp.ClientSession", return_value=mock_session):
        result = await call_mcp_tool(
            server_url="http://mcp-server/sse",
            tool_name="some_tool",
            arguments={"key": "value"},
        )

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert "output" in parsed
    assert parsed["output"] == "some result text"


@pytest.mark.asyncio
async def test_call_mcp_tool_success_json_output_not_double_serialized():
    """MCP 결과가 JSON 문자열인 경우 이중 직렬화 없이 객체로 포함된다."""
    mock_result = MagicMock()
    mock_result.isError = False
    mock_result.content = [_make_mcp_text_content('{"status": "ok", "count": 3}')]

    mock_sse_cm, mock_session = _make_mcp_session_context(mock_result)

    with patch("tools.mcp.sse_client", return_value=mock_sse_cm), \
         patch("tools.mcp.ClientSession", return_value=mock_session):
        result = await call_mcp_tool(
            server_url="http://mcp-server/sse",
            tool_name="some_tool",
            arguments={},
        )

    parsed = json.loads(result)
    # output이 문자열이 아닌 파싱된 객체여야 한다 (이중 직렬화 방지)
    assert isinstance(parsed["output"], dict)
    assert parsed["output"]["status"] == "ok"
    assert parsed["output"]["count"] == 3


@pytest.mark.asyncio
async def test_call_mcp_tool_error_returns_json_error():
    """MCP 도구 isError=True 시 error 키를 포함한 JSON을 반환한다."""
    mock_result = MagicMock()
    mock_result.isError = True
    mock_result.content = [_make_mcp_text_content("tool execution failed")]

    mock_sse_cm, mock_session = _make_mcp_session_context(mock_result)

    with patch("tools.mcp.sse_client", return_value=mock_sse_cm), \
         patch("tools.mcp.ClientSession", return_value=mock_session):
        result = await call_mcp_tool(
            server_url="http://mcp-server/sse",
            tool_name="some_tool",
            arguments={},
        )

    parsed = json.loads(result)
    assert "error" in parsed
    assert ToolErrorCode.EXECUTION_FAILED.message in parsed["error"]


@pytest.mark.asyncio
async def test_call_mcp_tool_exception_returns_json_error():
    """MCP 연결 실패 등 예외 발생 시 error 키를 포함한 JSON을 반환한다."""
    mock_sse_cm = MagicMock()
    mock_sse_cm.__aenter__ = AsyncMock(side_effect=Exception("connection refused"))
    mock_sse_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("tools.mcp.sse_client", return_value=mock_sse_cm):
        result = await call_mcp_tool(
            server_url="http://mcp-server/sse",
            tool_name="some_tool",
            arguments={},
        )

    parsed = json.loads(result)
    assert "error" in parsed
    assert ToolErrorCode.EXECUTION_FAILED.message in parsed["error"]


@pytest.mark.asyncio
async def test_call_mcp_tool_success_data_attribute_content():
    """content에 data 속성이 있는 경우 json.dumps로 직렬화하여 output에 포함한다."""
    mock_result = MagicMock()
    mock_result.isError = False

    # data 속성만 가진 content (text 없음)
    mock_content = MagicMock(spec=["data"])
    mock_content.data = {"key": "value", "count": 42}
    mock_result.content = [mock_content]

    mock_sse_cm, mock_session = _make_mcp_session_context(mock_result)

    with patch("tools.mcp.sse_client", return_value=mock_sse_cm), \
         patch("tools.mcp.ClientSession", return_value=mock_session):
        result = await call_mcp_tool(
            server_url="http://mcp-server/sse",
            tool_name="some_tool",
            arguments={},
        )

    parsed = json.loads(result)
    assert parsed["success"] is True
    # data 속성 content는 dict 형태로 output에 포함된다
    assert parsed["output"] == {"key": "value", "count": 42}


@pytest.mark.asyncio
async def test_call_mcp_tool_success_no_attribute_content():
    """content에 text도 data도 없는 경우 str()로 변환하여 output에 포함한다."""
    mock_result = MagicMock()
    mock_result.isError = False

    # text도 data도 없는 content → str(content) fallback
    mock_content = MagicMock(spec=[])
    mock_result.content = [mock_content]

    mock_sse_cm, mock_session = _make_mcp_session_context(mock_result)

    with patch("tools.mcp.sse_client", return_value=mock_sse_cm), \
         patch("tools.mcp.ClientSession", return_value=mock_session):
        result = await call_mcp_tool(
            server_url="http://mcp-server/sse",
            tool_name="some_tool",
            arguments={},
        )

    parsed = json.loads(result)
    assert parsed["success"] is True
    # str(MagicMock(spec=[])) 결과가 output으로 포함된다
    assert isinstance(parsed["output"], str)


@pytest.mark.asyncio
async def test_call_mcp_tool_success_empty_content():
    """result.content가 빈 목록일 때 output=None, 메시지에 '결과 없음'이 포함된다."""
    mock_result = MagicMock()
    mock_result.isError = False
    mock_result.content = []  # 빈 목록

    mock_sse_cm, mock_session = _make_mcp_session_context(mock_result)

    with patch("tools.mcp.sse_client", return_value=mock_sse_cm), \
         patch("tools.mcp.ClientSession", return_value=mock_session):
        result = await call_mcp_tool(
            server_url="http://mcp-server/sse",
            tool_name="some_tool",
            arguments={},
        )

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["output"] is None
    assert "결과 없음" in parsed["message"]

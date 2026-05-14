import json
import smtplib
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from common.error_code import ToolErrorCode
from tools.discord import send_discord_webhook
from tools.gmail import send_gmail
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

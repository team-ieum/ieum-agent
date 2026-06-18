import inspect
import json
import smtplib
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from common.error_code import ToolErrorCode
from tools.discord import send_discord_webhook
from tools.gmail import send_gmail
from tools.http_fetch import http_fetch
from tools.slack import send_slack_message
from tools.web_search import web_search
from tools import get_tools_for_request


def _make_async_http_client(post_mock):
    """httpx.AsyncClient 컨텍스트 매니저 mock 생성 헬퍼"""
    mock_client = AsyncMock()
    mock_client.post = post_mock
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


def _make_async_request_client(request_mock):
    """request 메서드를 가진 httpx.AsyncClient 컨텍스트 매니저 mock 생성 헬퍼"""
    mock_client = AsyncMock()
    mock_client.request = request_mock
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


def _make_async_get_client(get_mock):
    """get 메서드를 가진 httpx.AsyncClient 컨텍스트 매니저 mock 생성 헬퍼"""
    mock_client = AsyncMock()
    mock_client.get = get_mock
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


# ---------------------------------------------------------------------------
# HTTP Fetch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_http_fetch_headers_json_success():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/json"}
    mock_response.content = b'{"ok":true}'
    request_mock = AsyncMock(return_value=mock_response)
    mock_client = _make_async_request_client(request_mock)

    with patch("tools.http_fetch._is_private_host", return_value=False), \
         patch("tools.http_fetch.get_http_client", return_value=mock_client):
        result = await http_fetch(
            url="https://example.com",
            method="GET",
            headers_json='{"Accept":"application/json"}',
        )

    parsed = json.loads(result)
    assert parsed["statusCode"] == 200
    assert parsed["body"] == '{"ok":true}'
    request_mock.assert_awaited_once()
    assert request_mock.call_args.kwargs["headers"] == {"Accept": "application/json"}


@pytest.mark.asyncio
async def test_http_fetch_invalid_headers_json_returns_error():
    with patch("tools.http_fetch._is_private_host", return_value=False):
        result = await http_fetch(
            url="https://example.com",
            method="GET",
            headers_json="not-json",
        )

    parsed = json.loads(result)
    assert "error" in parsed
    assert "headers_json" in parsed["error"]


# ---------------------------------------------------------------------------
# Web Search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_web_search_success_returns_structured_results():
    tavily_response = {
        "query": "world economy news",
        "results": [
            {
                "title": "Example News",
                "url": "https://example.com/news",
                "content": "Global economy update",
            },
            {
                "title": "Example Report",
                "url": "https://example.org/report",
                "content": "Markets and finance report",
            },
        ]
    }
    mock_response = MagicMock()
    mock_response.json = MagicMock(return_value=tavily_response)
    mock_response.raise_for_status = MagicMock()
    post_mock = AsyncMock(return_value=mock_response)
    mock_client = _make_async_http_client(post_mock)

    with patch.dict("os.environ", {"TAVILY_API_KEY": "test_tavily_key"}), \
         patch("tools.web_search.get_http_client", return_value=mock_client):
        result = await web_search("world economy news", max_results=2)

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["query"] == "world economy news"
    assert parsed["results"] == [
        {
            "title": "Example News",
            "url": "https://example.com/news",
            "snippet": "Global economy update",
        },
        {
            "title": "Example Report",
            "url": "https://example.org/report",
            "snippet": "Markets and finance report",
        },
    ]


@pytest.mark.asyncio
async def test_web_search_missing_api_key_returns_error():
    with patch.dict("os.environ", {}, clear=True):
        result = await web_search("world economy news")

    parsed = json.loads(result)
    assert "error" in parsed
    assert "TAVILY_API_KEY" in parsed["error"]


@pytest.mark.asyncio
async def test_web_search_empty_query_returns_error():
    result = await web_search(" ")

    parsed = json.loads(result)
    assert "error" in parsed
    assert "검색어" in parsed["error"]



# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_slack_message_success():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client = _make_async_http_client(AsyncMock(return_value=mock_response))

    with patch("tools.slack.get_http_client", return_value=mock_client):
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

    with patch("tools.slack.get_http_client", return_value=mock_client):
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

    with patch("tools.discord.get_http_client", return_value=mock_client):
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

    with patch("tools.discord.get_http_client", return_value=mock_client):
        result = await send_discord_webhook(
            webhook_url="https://discord.com/api/webhooks/test",
            content="hello",
        )

    parsed = json.loads(result)
    assert "error" in parsed
    assert ToolErrorCode.EXECUTION_FAILED.message in parsed["error"]


def test_bind_config_strips_webhook_url_from_signature_and_docstring():
    """_bind_config로 webhook_url을 바인딩하면 시그니처와 docstring 어디에도
    webhook_url이 노출되지 않는다.
    (docstring에 남으면 LLM이 'URL을 모른다'며 도구 호출을 포기 → 발송 실패)

    ADK 내부 declaration 표현(버전마다 다름)에 의존하지 않도록 __signature__/__doc__를
    직접 검증한다. ADK는 이 둘로부터 LLM 노출 스키마를 생성한다."""
    from tools import _bind_config

    cfg = {
        "webhook_url": "https://discord.com/api/webhooks/x",
        "webhookCredentialId": "cred-id",
    }
    bound = _bind_config(send_discord_webhook, cfg)

    # 시그니처에서 제거
    params = inspect.signature(bound).parameters
    assert "webhook_url" not in params
    assert "content" in params

    # docstring(Args)에서도 제거 — 이번 버그의 핵심
    assert "webhook_url" not in (bound.__doc__ or "")
    assert "content" in (bound.__doc__ or "")


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
    result = get_tools_for_request([{"name": "slack"}, {"name": "discord"}, {"name": "builtin:web_search"}])
    assert len(result) == 3


def test_get_tools_for_request_unknown():
    result = get_tools_for_request([{"name": "unknown_tool"}])
    assert result == []


def test_get_tools_for_request_binds_tool_config():
    result = get_tools_for_request([
        {
            "name": "builtin:notion_create_page",
            "config": {
                "parent_page_id": "page-id",
                "title": "테스트 제목",
                "unknown": "ignored",
            },
        }
    ])

    fn = getattr(result[0], "func", None) or getattr(result[0], "_func", None)
    params = list(inspect.signature(fn).parameters.keys())

    assert "parent_page_id" not in params
    assert "title" not in params
    assert "unknown" not in params
    assert "token" in params
    assert "content" in params


# ---------------------------------------------------------------------------
# McpToolset integration
# ---------------------------------------------------------------------------

def test_get_tools_for_request_mcp_sse():
    """get_tools_for_request가 SSE 기반의 McpToolset을 올바르게 반환한다."""
    result = get_tools_for_request([
        {
            "name": "mcp",
            "config": {
                "server_url": "http://mcp-server/sse",
                "tool_name_prefix": "prefix:",
            }
        }
    ])
    assert len(result) == 1
    toolset = result[0]
    
    from google.adk.tools import McpToolset
    from google.adk.tools.mcp_tool import SseConnectionParams
    
    assert isinstance(toolset, McpToolset)
    assert isinstance(toolset._connection_params, SseConnectionParams)
    assert toolset._connection_params.url == "http://mcp-server/sse"
    assert toolset.tool_name_prefix == "prefix:"


def test_get_tools_for_request_mcp_stdio_raises():
    """stdio(command) 기반 MCP는 미지원이므로 ValueError를 던진다.
    실행 컨테이너에 npx/uvx 런타임이 없어 조용한 FileNotFoundError로 죽는 것을 차단한다."""
    with pytest.raises(ValueError, match="stdio"):
        get_tools_for_request([
            {
                "name": "mcp",
                "config": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                }
            }
        ])

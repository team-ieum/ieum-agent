"""Slack 봇 도구(spec 2026-10-20 §8). HTTP는 목으로 대신한다 — test_google_calendar와 같은 방식."""
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from common.error_code import ToolErrorCode
from tests.action_outputs import assert_matches_outputs
from tests.test_google_tools import _make_async_client, _make_mock_response
from tools.slack_bot import slack_send_channel_message

API = "https://slack.com/api"
PUBLIC = {"ok": True, "channel": {"id": "C1", "is_channel": True, "is_private": False}}
SENT = {"ok": True, "channel": "C1", "ts": "1700000000.000100"}
LINK = {"ok": True, "permalink": "https://t.slack.com/archives/C1/p1700000000000100"}


def _resp(body: dict, status: int = 200, headers: dict | None = None):
    resp = _make_mock_response(status, body)
    resp.headers = headers or {}
    return resp


def _gets(info=PUBLIC, link=LINK) -> AsyncMock:
    """conversations.info·chat.getPermalink가 client.get 하나를 같이 쓴다 — URL 끝으로 고른다.
    값이 dict면 200 본문, 예외면 던지고, 그 밖은 응답 목 그대로."""
    routes = {"/conversations.info": info, "/chat.getPermalink": link}

    async def get(url, **kwargs):
        for suffix, body in routes.items():
            if url.endswith(suffix):
                if isinstance(body, Exception):
                    raise body
                return _resp(body) if isinstance(body, dict) else body
        raise AssertionError(f"예상 못 한 GET {url}")
    return AsyncMock(side_effect=get)


def _client(get=None, post=None):
    """넘기지 않은 메서드는 불리면 실패한다(호출 없음 단언용)."""
    return _make_async_client(get_mock=get or AsyncMock(side_effect=AssertionError("GET 없음")),
                              post_mock=post or AsyncMock(side_effect=AssertionError("POST 없음")))


async def _run(client, **kwargs) -> dict:
    with patch("tools.slack_bot.get_http_client", return_value=client):
        return json.loads(await slack_send_channel_message(token="xoxb-test", **kwargs))


# --- 성공 ---------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_공개_채널에_보내고_ts_channel_permalink를_돌려준다():
    get, post = _gets(), AsyncMock(return_value=_resp(SENT))

    result = await _run(_client(get, post), channel="C1", text="안녕")

    assert result == {"success": True, "ts": "1700000000.000100", "channel": "C1",
                      "permalink": "https://t.slack.com/archives/C1/p1700000000000100"}
    assert post.call_args.args[0] == f"{API}/chat.postMessage"
    assert post.call_args.kwargs["json"] == {"channel": "C1", "text": "안녕"}
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer xoxb-test"
    info_call, link_call = get.call_args_list
    assert info_call.args[0] == f"{API}/conversations.info" and info_call.kwargs["params"] == {"channel": "C1"}
    assert link_call.args[0] == f"{API}/chat.getPermalink"
    assert link_call.kwargs["params"] == {"channel": "C1", "message_ts": "1700000000.000100"}
    assert all(c.kwargs["headers"]["Authorization"] == "Bearer xoxb-test" for c in get.call_args_list)
    assert_matches_outputs("slack_bot.send_channel_message", result)


@pytest.mark.asyncio
async def test_출력_channel은_입력이_아니라_응답의_채널_ID다():
    """spec §8-4: getPermalink·출력은 chat.postMessage 응답의 channel(ID)을 쓴다."""
    get, post = _gets(), AsyncMock(return_value=_resp({"ok": True, "channel": "C0RESP", "ts": "1.2"}))

    result = await _run(_client(get, post), channel="C1", text="hi")

    assert result["channel"] == "C0RESP"
    assert get.call_args_list[1].kwargs["params"] == {"channel": "C0RESP", "message_ts": "1.2"}


@pytest.mark.asyncio
@pytest.mark.parametrize("thread_ts, extra", [
    ("1699.0001", {"thread_ts": "1699.0001"}),
    (" 1699.0001 ", {"thread_ts": "1699.0001"}),
    ("", {}), ("   ", {}), (None, {}),
])
async def test_thread_ts가_있으면_답글_비면_새_메시지(thread_ts, extra):
    post = AsyncMock(return_value=_resp(SENT))

    await _run(_client(_gets(), post), channel="C1", text="hi", thread_ts=thread_ts)

    assert post.call_args.kwargs["json"] == {"channel": "C1", "text": "hi", **extra}


@pytest.mark.asyncio
async def test_문자열이_아닌_값은_str로_채널은_공백을_뗀다():
    get, post = _gets(), AsyncMock(return_value=_resp(SENT))

    await _run(_client(get, post), channel=" C1 ", text=42)

    assert get.call_args_list[0].kwargs["params"] == {"channel": "C1"}
    assert post.call_args.kwargs["json"] == {"channel": "C1", "text": "42"}


# --- 입력 · 채널 검사 ------------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("channel, text, word", [
    ("", "안녕", "채널"), ("   ", "안녕", "채널"), (None, "안녕", "채널"),
    ("C1", "", "메시지"), ("C1", " \n ", "메시지"), ("C1", None, "메시지"),
])
async def test_채널_메시지가_비면_Slack을_부르지_않는다(channel, text, word):
    client = _client()

    result = await _run(client, channel=channel, text=text)

    assert ToolErrorCode.EXECUTION_FAILED.message in result["error"] and word in result["error"]
    assert client.get.await_count == 0 and client.post.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("conv", [
    {"id": "G1", "is_channel": False, "is_group": True, "is_private": True},   # 비공개 채널
    {"id": "C2", "is_channel": True, "is_private": True},                     # 비공개로 바뀐 채널
    {"id": "D1", "is_im": True, "is_private": True},                          # DM
    {"id": "G2", "is_mpim": True, "is_private": True},                        # 그룹 DM
    {"id": "C3", "is_channel": True},                                          # is_private 없음 — fail-closed
    {"id": "C4"},                                                              # 판정 필드 없음 — fail-closed
])
async def test_공개_채널이_아니면_보내지_않는다(conv):
    """S2: 봇 토큰·채널 멤버십을 링크된 사용자 전원이 공유한다 — 비공개·DM은 이 노드가 열지 않는다."""
    post = AsyncMock(side_effect=AssertionError("POST 없음"))

    result = await _run(_client(_gets(info={"ok": True, "channel": conv}), post), channel=conv["id"], text="hi")

    assert "공개 채널만" in result["error"]
    assert post.await_count == 0


@pytest.mark.asyncio
async def test_채널을_못_찾으면_ID_입력을_안내하고_보내지_않는다():
    """채널 이름(#general)은 conversations.info가 channel_not_found로 돌려준다."""
    post = AsyncMock(side_effect=AssertionError("POST 없음"))

    result = await _run(_client(_gets(info={"ok": False, "error": "channel_not_found"}), post),
                        channel="#general", text="hi")

    assert result["error"].startswith("Slack API 오류 (channel_not_found): ")
    assert "채널 ID" in result["error"]
    assert post.await_count == 0


# --- Slack 오류 -------------------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("code, hint", [
    ("not_in_channel", "초대"), ("is_archived", "보관된 채널"),
    ("invalid_auth", "다시 연결"), ("account_inactive", "다시 연결"),
    ("token_revoked", "다시 연결"), ("token_expired", "다시 연결"),
    ("missing_scope", "권한"), ("restricted_action", "처리하지 못했어요"),
])
async def test_전송_오류는_코드와_안내를_싣는다(code, hint):
    post = AsyncMock(return_value=_resp({"ok": False, "error": code}))

    result = await _run(_client(_gets(), post), channel="C1", text="hi")

    assert result["error"].startswith(f"Slack API 오류 ({code}): ")
    assert hint in result["error"]


@pytest.mark.asyncio
async def test_JSON이_아닌_응답은_HTTP_상태로_실패한다():
    bad = _resp({}, status=502)
    bad.json.side_effect = ValueError("html")

    result = await _run(_client(_gets(), AsyncMock(return_value=bad)), channel="C1", text="hi")

    assert result["error"].startswith("Slack API 오류 (http_502): ")


@pytest.mark.asyncio
@pytest.mark.parametrize("link", [{"ok": False, "error": "message_not_found"}, httpx.ConnectError("끊김")])
async def test_permalink를_못_얻어도_전송은_성공이다(link):
    result = await _run(_client(_gets(link=link), AsyncMock(return_value=_resp(SENT))), channel="C1", text="hi")

    assert result == {"success": True, "ts": SENT["ts"], "channel": "C1", "permalink": ""}


@pytest.mark.asyncio
async def test_네트워크_예외는_실행_실패_문구로_돌려준다():
    post = AsyncMock(side_effect=httpx.ConnectTimeout("timed out"))

    result = await _run(_client(_gets(), post), channel="C1", text="hi")

    assert ToolErrorCode.EXECUTION_FAILED.message in result["error"]


# --- 속도 제한 -------------------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("limited, waited", [
    (_resp({}, status=429, headers={"Retry-After": "2"}), 2.0),
    (_resp({}, status=429, headers={"Retry-After": "30"}), 5.0),      # 상한 5초
    (_resp({}, status=429, headers={"Retry-After": "soon"}), 1.0),    # 숫자 아님 → 1초
    (_resp({}, status=429), 1.0),                                      # 헤더 없음 → 1초
    (_resp({"ok": False, "error": "ratelimited"}), 1.0),               # 본문으로 오는 제한
    (_resp({"ok": False, "error": "rate_limited"}), 1.0),
])
async def test_속도_제한은_한_번_기다렸다_다시_보낸다(limited, waited):
    post = AsyncMock(side_effect=[limited, _resp(SENT)])
    sleep = AsyncMock()

    with patch("tools.slack_bot.asyncio.sleep", new=sleep):
        result = await _run(_client(_gets(), post), channel="C1", text="hi")

    assert result["success"] is True and post.await_count == 2
    sleep.assert_awaited_once_with(waited)


@pytest.mark.asyncio
async def test_두_번째도_제한이면_실패하고_더_기다리지_않는다():
    limited = _resp({}, status=429, headers={"Retry-After": "1"})
    post = AsyncMock(side_effect=[limited, limited])
    sleep = AsyncMock()

    with patch("tools.slack_bot.asyncio.sleep", new=sleep):
        result = await _run(_client(_gets(), post), channel="C1", text="hi")

    assert result["error"].startswith("Slack API 오류 (ratelimited): ")
    assert post.await_count == 2 and sleep.await_count == 1

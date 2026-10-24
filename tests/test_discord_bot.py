"""Discord 봇 도구(spec 2026-10-21 §8). HTTP는 httpx.MockTransport로 대신한다 — 진짜 httpx가 URL·헤더를 만들므로
경로 조립과 요청 헤더(User-Agent)를 그대로 본다."""
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from common.error_code import ToolErrorCode
from tests.action_outputs import assert_matches_outputs
from tools.discord_bot import discord_send_channel_message

API = "https://discord.com/api/v10"
UA = "DiscordBot (https://green.kiacms.co.kr, 1.0)"
G1, G2 = "111111111111111111", "222222222222222222"
CH = "333333333333333333"
MSG = "444444444444444444"
TEXT_CHANNEL = {"id": CH, "type": 0, "guild_id": G1, "name": "general"}
SENT = {"id": MSG, "channel_id": CH, "content": "안녕"}


def _discord(channel=(200, TEXT_CHANNEL), send=(200, SENT)):
    """GET = 채널 조회, POST = 메시지 전송. 값이 list면 차례로 응답하고 마지막 것을 되풀이한다.
    (상태, dict)는 JSON 본문, (상태, str)는 그 문자열 본문, 예외는 던진다. 받은 요청은 두 번째 반환값에 쌓인다."""
    seen = []
    queues = {"GET": list(channel) if isinstance(channel, list) else [channel],
              "POST": list(send) if isinstance(send, list) else [send]}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        q = queues[request.method]
        item = q.pop(0) if len(q) > 1 else q[0]
        if isinstance(item, Exception):
            raise item
        status, body = item
        return httpx.Response(status, text=body) if isinstance(body, str) else httpx.Response(status, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


async def _run(client, channel_id=CH, content="안녕", allowed=(G1,)) -> dict:
    with patch("tools.discord_bot.get_http_client", return_value=client):
        return json.loads(await discord_send_channel_message(
            token="bot-token", allowed_guild_ids=list(allowed) if isinstance(allowed, tuple) else allowed,
            channel_id=channel_id, content=content))


# --- 성공 ---------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_채널을_확인하고_보내서_id_channel_id_url을_돌려준다():
    client, seen = _discord()

    result = await _run(client)

    assert result == {"success": True, "id": MSG, "channel_id": CH,
                      "url": f"https://discord.com/channels/{G1}/{CH}/{MSG}"}
    assert [(r.method, str(r.url)) for r in seen] == [
        ("GET", f"{API}/channels/{CH}"), ("POST", f"{API}/channels/{CH}/messages")]
    assert json.loads(seen[1].content) == {"content": "안녕", "allowed_mentions": {"parse": ["users"]}}
    assert all(r.headers["authorization"] == "Bot bot-token" for r in seen)
    assert_matches_outputs("discord_bot.send_channel_message", result)


@pytest.mark.asyncio
async def test_User_Agent는_요청마다_싣고_공유_클라이언트_기본_헤더는_바꾸지_않는다():
    client, seen = _discord()
    before = client.headers["user-agent"]

    await _run(client)

    assert [r.headers["user-agent"] for r in seen] == [UA, UA]
    assert client.headers["user-agent"] == before and before.startswith("python-httpx/")


@pytest.mark.asyncio
async def test_공지_채널도_보낸다():
    client, seen = _discord(channel=(200, {**TEXT_CHANNEL, "type": 5}))

    result = await _run(client)

    assert result["success"] is True and [r.method for r in seen] == ["GET", "POST"]


@pytest.mark.asyncio
async def test_채널_ID는_공백을_떼고_숫자도_받으며_메시지는_str로_보낸다():
    client, seen = _discord()

    await _run(client, channel_id=f" {CH}\n", content=42)
    await _run(client, channel_id=int(CH), content="hi")

    assert [str(r.url) for r in seen if r.method == "GET"] == [f"{API}/channels/{CH}"] * 2
    assert json.loads(seen[1].content)["content"] == "42"


@pytest.mark.asyncio
async def test_허용_목록에_서버가_여럿이면_그중_하나면_된다():
    client, _ = _discord(channel=(200, {**TEXT_CHANNEL, "guild_id": G2}))

    result = await _run(client, allowed=(G1, f" {G2} "))

    assert result["url"] == f"https://discord.com/channels/{G2}/{CH}/{MSG}"


# --- 입력 검사(HTTP 없음) ----------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("channel_id, content, word", [
    ("", "안녕", "채널"), ("   ", "안녕", "채널"), (None, "안녕", "채널"),
    (CH, "", "메시지"), (CH, " \n ", "메시지"), (CH, None, "메시지"),
])
async def test_채널_메시지가_비면_Discord를_부르지_않는다(channel_id, content, word):
    client, seen = _discord()

    result = await _run(client, channel_id=channel_id, content=content)

    assert ToolErrorCode.EXECUTION_FAILED.message in result["error"] and word in result["error"]
    assert seen == []


@pytest.mark.asyncio
async def test_메시지는_2000자까지만():
    client, seen = _discord()

    assert (await _run(client, content="가" * 2000))["success"] is True
    seen.clear()
    result = await _run(client, content="가" * 2001)

    assert "최대 2,000자" in result["error"] and seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("channel_id", [
    "1/../2", "123?x", f"{CH}/../../users/@me", f"{CH}?with_counts=true", f"{CH}#x",
    "../channels/" + CH, "123", "1" * 21, "abc", "١" * 18,   # 마지막은 아라비아 숫자(유니코드 \d)
])
async def test_채널_ID가_snowflake가_아니면_경로에_끼우지_않는다(channel_id):
    """httpx는 경로의 ../를 정규화한다 — 검사 없이 끼우면 봇 토큰으로 다른 엔드포인트를 부른다(spec §16 규칙 1)."""
    client, seen = _discord()

    result = await _run(client, channel_id=channel_id)

    assert "17~20자리 숫자" in result["error"] and seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [(), ("", "  "), [], None, G1])   # 마지막은 list가 아닌 문자열
async def test_허용_목록이_비면_거절한다(allowed):
    """불변식 (c): 주입된 목록이 비면 어느 서버에도 쓰지 않는다. list가 아닌 값도 빈 것으로 본다."""
    client, seen = _discord()

    result = await _run(client, allowed=allowed)

    assert "Discord 서버가 연결되어 있지 않아요" in result["error"] and seen == []


# --- 소유·채널 종류 검사 ----------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("channel", [
    {**TEXT_CHANNEL, "guild_id": G2},                    # 남의 서버 채널
    {"id": CH, "type": 1, "recipients": []},             # DM — guild_id 없음
    {"id": CH, "type": 0},                                # guild_id 없음
    {**TEXT_CHANNEL, "guild_id": None},
])
async def test_연결하지_않은_서버의_채널이면_보내지_않는다(channel):
    client, seen = _discord(channel=(200, channel))

    result = await _run(client)

    assert "연결하지 않은 Discord 서버의 채널" in result["error"]
    assert [r.method for r in seen] == ["GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [2, 4, 11, 13, 15, 16, None, True, "0"])   # 음성·분류·스레드·스테이지·포럼·미디어
async def test_텍스트_공지_채널이_아니면_보내지_않는다(kind):
    channel = {**TEXT_CHANNEL, "type": kind} if kind is not None else {"id": CH, "guild_id": G1}
    client, seen = _discord(channel=(200, channel))

    result = await _run(client)

    assert "텍스트·공지 채널에만" in result["error"]
    assert [r.method for r in seen] == ["GET"]


# --- Discord 오류 -----------------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["GET", "POST"])
@pytest.mark.parametrize("status, code, label, hint", [
    (403, 50001, "50001", "Discord를 다시 연결해 주세요"),
    (403, 50013, "50013", "IEUM 봇 역할 권한"),
    (404, 10003, "10003", "채널 ID를 확인"),
    (400, 50035, "50035", "입력 형식"),
    (403, 60003, "60003", "운영자"),
    (401, 0, "http_401", "운영자"),
    (500, None, "http_500", "처리하지 못했어요"),
])
async def test_오류는_코드와_안내를_싣고_401_403은_다시_부르지_않는다(stage, status, code, label, hint):
    body = {"message": "x", "code": code} if code is not None else {"message": "x"}
    client, seen = _discord(**({"channel": (status, body)} if stage == "GET" else {"send": (status, body)}))
    sleep = AsyncMock()

    with patch("tools.discord_bot.asyncio.sleep", new=sleep):
        result = await _run(client)

    assert result["error"].startswith(f"Discord API 오류 ({label}): ")
    assert hint in result["error"]
    assert [r.method for r in seen] == (["GET"] if stage == "GET" else ["GET", "POST"])
    assert sleep.await_count == 0


@pytest.mark.asyncio
async def test_JSON이_아닌_응답은_HTTP_상태로_실패한다():
    client, _ = _discord(send=(502, "<html>bad gateway</html>"))

    result = await _run(client)

    assert result["error"].startswith("Discord API 오류 (http_502): ")


@pytest.mark.asyncio
async def test_네트워크_예외는_실행_실패_문구로_돌려준다():
    client, _ = _discord(send=httpx.ConnectTimeout("timed out"))

    result = await _run(client)

    assert ToolErrorCode.EXECUTION_FAILED.message in result["error"]


# --- 속도 제한 -------------------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["GET", "POST"])
@pytest.mark.parametrize("limited, waited", [
    ({"message": "rate limited", "retry_after": 0.512, "global": False}, 0.512),   # 소수 초
    ({"message": "rate limited", "retry_after": 5}, 5.0),                          # 상한 그대로
    ({"message": "rate limited", "retry_after": "soon"}, 1.0),                     # 숫자 아님 → 1초
    ({"message": "rate limited"}, 1.0),                                             # 없음 → 1초
])
async def test_429는_본문_retry_after만큼_한_번_기다렸다_다시_부른다(stage, limited, waited):
    ok = (200, TEXT_CHANNEL) if stage == "GET" else (200, SENT)
    client, seen = _discord(**{"channel" if stage == "GET" else "send": [(429, limited), ok]})
    sleep = AsyncMock()

    with patch("tools.discord_bot.asyncio.sleep", new=sleep):
        result = await _run(client)

    assert result["success"] is True
    assert [r.method for r in seen].count(stage) == 2
    sleep.assert_awaited_once_with(waited)


@pytest.mark.asyncio
@pytest.mark.parametrize("limited", [
    {"message": "rate limited", "retry_after": 30.0},
    '{"message": "rate limited", "retry_after": NaN}',       # JSON 본문에 NaN 리터럴 — max(nan, 0)은 nan
    '{"message": "rate limited", "retry_after": Infinity}',
    {"message": "rate limited", "retry_after": "NaN"},
])
async def test_retry_after가_5초를_넘으면_기다리지_않고_실패한다(limited):
    """다시 불러도 429다 — 429도 invalid request 한도에 든다(spec §13). NaN·inf도 기다리지 않는다."""
    client, seen = _discord(send=[(429, limited), (200, SENT)])
    sleep = AsyncMock()

    with patch("tools.discord_bot.asyncio.sleep", new=sleep):
        result = await _run(client)

    assert result["error"].startswith("Discord API 오류 (http_429): ")
    assert [r.method for r in seen] == ["GET", "POST"] and sleep.await_count == 0


@pytest.mark.asyncio
async def test_두_번째도_429면_실패하고_더_기다리지_않는다():
    limited = (429, {"message": "rate limited", "retry_after": 1})
    client, seen = _discord(send=[limited, limited])
    sleep = AsyncMock()

    with patch("tools.discord_bot.asyncio.sleep", new=sleep):
        result = await _run(client)

    assert result["error"].startswith("Discord API 오류 (http_429): ")
    assert [r.method for r in seen] == ["GET", "POST", "POST"] and sleep.await_count == 1

"""Discord 봇 도구(spec 2026-10-21 §8). `token`은 BE가 X-Discord-Token으로 넘긴 봇 토큰, `allowed_guild_ids`는
X-Discord-Guild-Ids(이 사용자가 연결한 서버) — 둘 다 실행 때 주입되고 LLM에는 보이지 않는다.
Discord는 실패를 HTTP 상태 + 본문 code로 준다(2xx 실패 없음)."""
import asyncio
import json
import re

from common.error_code import ToolErrorCode
from tools.http_client import get_http_client

_API = "https://discord.com/api/v10"
# Discord 문서상 필수. 공유 클라이언트(get_http_client) 기본 헤더는 바꾸지 않고 요청마다 싣는다(spec §8-3).
_USER_AGENT = "DiscordBot (https://green.kiacms.co.kr, 1.0)"
_TIMEOUT = 30.0
_MAX_RETRY_WAIT = 5.0  # 본문 retry_after 상한(spec §5·§8-6). 넘으면 기다리지 않고 실패
_MAX_CONTENT = 2000
_TEXT_CHANNEL_TYPES = (0, 5)  # GUILD_TEXT·GUILD_ANNOUNCEMENT
# fullmatch + ASCII — `$`는 끝 개행을 통과시키고 `\d`는 유니코드 숫자를 받는다.
_SNOWFLAKE_RE = re.compile(r"\d{17,20}", re.ASCII)
_RECONNECT_HINT = "봇이 이 채널을 볼 수 없어요. 채널 권한을 확인하고, 봇이 서버에서 내보내졌다면 Discord를 다시 연결해 주세요"
_OPERATOR_HINT = "IEUM Discord 봇 설정 문제예요. 운영자에게 알려 주세요"
_HINTS = {
    50001: _RECONNECT_HINT,  # 추방된 서버의 채널 경로도 10004가 아니라 50001이 온다
    50013: "봇 권한이 부족해요. 서버 설정에서 IEUM 봇 역할 권한을 확인해 주세요",
    10003: "채널이 없어요. 채널 ID를 확인해 주세요",
    50035: "입력 형식이 맞지 않아요",
    60003: _OPERATOR_HINT,
}
_STATUS_HINTS = {
    401: _OPERATOR_HINT,
    429: "요청이 많아 Discord가 잠시 막았어요. 잠시 뒤 다시 시도해 주세요",
}


def _blank(value) -> bool:
    """비운 칸(spec §8-1). BE는 없는 참조를 ""로, AI 슬롯도 ""를 보낸다."""
    return value is None or (isinstance(value, str) and not value.strip())


def _snowflake(value) -> str | None:
    """URL 경로에 끼울 Discord ID. 17~20자리 숫자가 아니면 None — httpx가 경로의 `../`를 정규화하므로
    검사 없이 조립하면 봇 토큰으로 다른 엔드포인트를 부를 수 있다(spec §8-1, §16 규칙 1)."""
    text = str(value).strip()
    return text if _SNOWFLAKE_RE.fullmatch(text) else None


def _fail(reason: str) -> str:
    return json.dumps({"error": f"{ToolErrorCode.EXECUTION_FAILED.message} (discord_send_channel_message: {reason})"},
                      ensure_ascii=False)


def _reject(message: str) -> str:
    return json.dumps({"error": f"Discord 채널 오류: {message}"}, ensure_ascii=False)


def _api_error(status: int, body: dict) -> str:
    code = body.get("code")
    if type(code) is not int or code == 0:  # 401 등은 code 0 — HTTP 상태로 안내한다
        code = None
    hint = _HINTS.get(code) or _STATUS_HINTS.get(status) or "Discord가 요청을 처리하지 못했어요"
    return json.dumps({"error": f"Discord API 오류 ({code if code is not None else f'http_{status}'}): {hint}"},
                      ensure_ascii=False)


def _json(response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _retry_after(body: dict) -> float | None:
    """429 본문의 retry_after(소수 초). 없거나 숫자가 아니면 1초, 5초를 넘으면 None(기다리지 않는다)."""
    try:
        seconds = float(body.get("retry_after", 1.0))
    except (TypeError, ValueError):
        seconds = 1.0
    seconds = max(seconds, 0.0)
    return None if seconds > _MAX_RETRY_WAIT else seconds


async def _call(verb: str, path: str, token: str, **kwargs) -> tuple[int, dict]:
    """Discord REST 한 번 → (HTTP 상태, 본문 dict). 429만 retry_after만큼 한 번 기다려 한 번 더 부른다.
    401·403은 다시 부르지 않는다 — 공유 봇의 invalid request 한도(spec §13)."""
    send = getattr(get_http_client(), verb)
    headers = {"Authorization": f"Bot {token}", "User-Agent": _USER_AGENT}
    for attempt in range(2):
        response = await send(f"{_API}{path}", headers=headers, timeout=_TIMEOUT, **kwargs)
        body = _json(response)
        if response.status_code != 429 or attempt:
            return response.status_code, body
        wait = _retry_after(body)
        if wait is None:
            return response.status_code, body
        await asyncio.sleep(wait)


async def discord_send_channel_message(token: str, allowed_guild_ids: list, channel_id: str, content: str) -> str:
    """
    연결한 Discord 서버의 텍스트·공지 채널에 봇으로 메시지를 보냅니다. @everyone·역할 멘션은 알림이 가지 않습니다.

    Args:
        token: Discord 봇 토큰
        allowed_guild_ids: 이 사용자가 연결한 Discord 서버 ID 목록
        channel_id: 채널 ID (17~20자리 숫자). 채널 이름(#general)은 받지 않는다
        content: 보낼 메시지. 최대 2,000자

    Returns:
        id(메시지 ID), channel_id, url(메시지 링크)을 담은 JSON 문자열
    """
    if _blank(channel_id):
        return _fail("채널이 비어 있어요")
    if _blank(content):
        return _fail("메시지가 비어 있어요")
    content = str(content)
    if len(content) > _MAX_CONTENT:
        return _fail(f"메시지는 최대 2,000자예요(지금 {len(content):,}자)")
    channel = _snowflake(channel_id)
    if channel is None:
        return _fail("채널 ID는 17~20자리 숫자예요")
    allowed = {str(g).strip() for g in allowed_guild_ids if str(g).strip()} \
        if isinstance(allowed_guild_ids, (list, tuple, set)) else set()
    if not allowed:
        return _reject("Discord 서버가 연결되어 있지 않아요. 연동 화면에서 Discord를 연결해 주세요")
    try:
        status, info = await _call("get", f"/channels/{channel}", token)
        if status != 200:
            return _api_error(status, info)
        guild_id = str(info.get("guild_id") or "")
        if guild_id not in allowed:  # DM 채널은 guild_id가 없어 여기서 막힌다
            return _reject("연결하지 않은 Discord 서버의 채널이에요")
        kind = info.get("type")
        if type(kind) is not int or kind not in _TEXT_CHANNEL_TYPES:  # bool은 int 하위형이라 type()으로 거른다
            return _reject("텍스트·공지 채널에만 보낼 수 있어요")
        # parse users 고정 — 생략하면 @everyone·역할까지 실제로 호출된다(spec D10)
        status, sent = await _call("post", f"/channels/{channel}/messages", token,
                                   json={"content": content, "allowed_mentions": {"parse": ["users"]}})
        if status != 200:
            return _api_error(status, sent)
    except Exception as e:
        return _fail(str(e))
    message_id, sent_channel = str(sent.get("id") or ""), str(sent.get("channel_id") or channel)
    return json.dumps({"success": True, "id": message_id, "channel_id": sent_channel,
                       "url": f"https://discord.com/channels/{guild_id}/{sent_channel}/{message_id}"},
                      ensure_ascii=False)

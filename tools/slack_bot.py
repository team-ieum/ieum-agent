"""Slack 봇 도구(spec 2026-10-20 §8). 토큰은 BE가 X-Slack-Token으로 넘긴 봇 토큰 — 인자 `token`으로 주입된다.
Slack은 오류를 HTTP 200 + ok:false로 준다. 판정은 ok·error 코드로 한다."""
import asyncio
import json

from common.error_code import ToolErrorCode
from tools.http_client import get_http_client

_SLACK_API = "https://slack.com/api"
_TIMEOUT = 30.0
_MAX_RETRY_WAIT = 5.0  # Retry-After 상한(spec §8-6). 노드 타임아웃 안에서 한 번만 기다린다
_RATE_LIMITED = {"ratelimited", "rate_limited"}
_RECONNECT = "Slack을 다시 연결해 주세요"
_HINTS = {
    "channel_not_found": "채널을 찾을 수 없어요. 드롭다운에서 고르거나 채널 ID(C로 시작)를 넣어 주세요",
    "not_in_channel": "봇이 채널에 없어요. 채널에 앱을 초대해 주세요",
    "is_archived": "보관된 채널이에요",
    "invalid_auth": _RECONNECT,
    "account_inactive": _RECONNECT,
    "token_revoked": _RECONNECT,
    "token_expired": _RECONNECT,
    "missing_scope": "앱 권한이 부족해요. Slack을 다시 연결해 주세요",
    "ratelimited": "요청이 많아 Slack이 잠시 막았어요. 잠시 뒤 다시 시도해 주세요",
    "rate_limited": "요청이 많아 Slack이 잠시 막았어요. 잠시 뒤 다시 시도해 주세요",
}


def _blank(value) -> bool:
    """비운 칸(spec §8-1, Calendar 기준). BE는 없는 참조를 ""로, AI 슬롯도 ""를 보낸다."""
    return value is None or (isinstance(value, str) and not value.strip())


def _fail(reason: str) -> str:
    return json.dumps({"error": f"{ToolErrorCode.EXECUTION_FAILED.message} (slack_send_channel_message: {reason})"},
                      ensure_ascii=False)


def _api_error(code) -> str:
    hint = _HINTS.get(code, "Slack이 요청을 처리하지 못했어요")
    return json.dumps({"error": f"Slack API 오류 ({code}): {hint}"}, ensure_ascii=False)


def _body(response) -> dict:
    """응답 → ok·error를 가진 dict. 429는 본문과 상관없이 ratelimited, JSON 객체가 아니면 http_<상태>."""
    if response.status_code == 429:
        return {"ok": False, "error": "ratelimited"}
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict) and "ok" in data:
        return data
    return {"ok": False, "error": f"http_{response.status_code}"}


def _retry_after(response) -> float:
    try:
        seconds = float(response.headers.get("Retry-After", 1))
    except (TypeError, ValueError):
        seconds = 1.0
    return min(max(seconds, 0.0), _MAX_RETRY_WAIT)


async def _call(verb: str, method: str, token: str, **kwargs) -> dict:
    """Slack Web API 한 번. 속도 제한이면 Retry-After(최대 5초)만큼 한 번 기다려 한 번만 다시 부른다(spec §8-6)."""
    send = getattr(get_http_client(), verb)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
    for attempt in range(2):
        response = await send(f"{_SLACK_API}/{method}", headers=headers, timeout=_TIMEOUT, **kwargs)
        data = _body(response)
        if data.get("error") not in _RATE_LIMITED or attempt:
            return data
        await asyncio.sleep(_retry_after(response))


async def _permalink(token: str, channel: str, ts: str) -> str:
    """전송은 끝났다 — 링크를 못 얻어도 노드는 성공이다(spec §8-4)."""
    try:
        data = await _call("get", "chat.getPermalink", token, params={"channel": channel, "message_ts": ts})
    except Exception:
        return ""
    return str(data.get("permalink") or "") if data.get("ok") else ""


async def slack_send_channel_message(token: str, channel: str, text: str, thread_ts: str = "") -> str:
    """
    연결한 Slack 워크스페이스의 공개 채널에 봇으로 메시지를 보냅니다. 비공개 채널·DM에는 보내지 않습니다.

    Args:
        token: Slack 봇 토큰
        channel: 채널 ID (예: "C0123456789"). 채널 이름(#general)은 받지 않는다
        text: 보낼 메시지. 4만 자가 넘으면 Slack이 잘라 보낸다
        thread_ts: 답글을 달 메시지의 ts. 비우면 새 메시지

    Returns:
        ts, channel(채널 ID), permalink를 담은 JSON 문자열
    """
    if _blank(channel):
        return _fail("채널이 비어 있어요")
    if _blank(text):
        return _fail("메시지가 비어 있어요")
    channel, text = str(channel).strip(), str(text)
    try:
        info = await _call("get", "conversations.info", token, params={"channel": channel})
        if not info.get("ok"):
            return _api_error(info.get("error"))
        conv = info.get("channel") or {}
        # S2: 봇 토큰을 링크된 사용자 전원이 공유한다 — 비공개·DM은 멤버 확인 없이 열지 않는다. 필드가 없으면 거절.
        if conv.get("is_channel") is not True or conv.get("is_private") is not False:
            return json.dumps({"error": "Slack 채널 오류: 공개 채널만 보낼 수 있어요. 비공개 채널·DM에는 보낼 수 없어요"},
                              ensure_ascii=False)
        payload = {"channel": channel, "text": text}
        if not _blank(thread_ts):
            payload["thread_ts"] = str(thread_ts).strip()
        sent = await _call("post", "chat.postMessage", token, json=payload)
        if not sent.get("ok"):
            return _api_error(sent.get("error"))
    except Exception as e:
        return _fail(str(e))
    ts, sent_channel = str(sent.get("ts") or ""), str(sent.get("channel") or "")
    return json.dumps({"success": True, "ts": ts, "channel": sent_channel,
                       "permalink": await _permalink(token, sent_channel, ts)}, ensure_ascii=False)

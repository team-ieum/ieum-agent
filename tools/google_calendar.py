import json
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo

import httpx

from common.error_code import ToolErrorCode
from tools.http_client import get_http_client

_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
_TIMEOUT = 30.0
_FIND_MAX = 250  # 노드 칸 상한(spec §4.1). events.list 한 페이지 상한(2500)보다 작아 페이지당 요청은 남은 개수 그대로


def _calendar_segment(calendar_id: Optional[str]) -> str:
    # 빈 값은 기본 캘린더(스키마 설명·BE 공급원과 동일). 임베드 링크에서 복사한 %23 id도 받도록
    # unquote 후 재인코딩한다 — 캘린더 id엔 리터럴 %가 없다.
    return quote(unquote(str(calendar_id or "").strip() or "primary"), safe="")


def _headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


class _ApiError(Exception):
    """Google API 실패 응답을 들고 올라간다 — 도구가 잡아 `_api_error`로 바꾼다."""

    def __init__(self, response):
        super().__init__(response.status_code)
        self.response = response


def _blank(value) -> bool:
    """비운 칸 = 변경 없음(spec §3). BE는 없는 참조를 ""로, AI 슬롯도 ""를 보낸다.
    PATCH는 배열을 통째로 바꾸므로 []를 보내면 참석자가 전부 지워진다."""
    return value is None or value == [] or (isinstance(value, str) and not value.strip())


def _api_error(response) -> str:
    # 본문은 실패일 때만 읽는다. 깨진 본문이어도 상태 코드는 남긴다.
    try:
        message = response.json().get("error", {}).get("message", "알 수 없는 오류")
    except Exception:
        message = "알 수 없는 오류"
    return json.dumps({"error": f"Google Calendar API 오류 ({response.status_code}): {message}"}, ensure_ascii=False)


def _fail(tool: str, reason: str) -> str:
    return json.dumps({"error": f"{ToolErrorCode.EXECUTION_FAILED.message} ({tool}: {reason})"}, ensure_ascii=False)


def _event_url(cal: str, event_id) -> str:
    return f"{_CALENDAR_API_BASE}/calendars/{cal}/events/{quote(str(event_id), safe='')}"


async def _get_json(client, headers: dict, url: str) -> dict:
    response = await client.get(url, headers=headers, timeout=_TIMEOUT)
    if not response.is_success:
        raise _ApiError(response)
    return response.json()


async def _resolve_target(client, headers: dict, cal: str, event_id, scope) -> dict:
    """대상 해석(spec §4.2-1): events.get. scope=all이고 반복 일정의 회차면 원본(recurringEventId)을,
    아니면 그 일정 그대로(에러 없음)."""
    event = await _get_json(client, headers, _event_url(cal, event_id))
    if scope == "all" and event.get("recurringEventId"):
        event = await _get_json(client, headers, _event_url(cal, event["recurringEventId"]))
    return event


def _send_updates(send_updates) -> dict:
    """events.patch·events.delete의 sendUpdates 쿼리. 비우면 생략한다(Google 기본 동작)."""
    return {} if _blank(send_updates) else {"sendUpdates": str(send_updates).strip()}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _Zone:
    """시간대 보정(spec §3). 오프셋 없는 시각은 time_zone, 없으면 캘린더 기본 timeZone(calendars.get)의
    벽시계 시각으로 읽는다. Google은 오프셋 없는 RFC3339를 400으로 거절한다. 캘린더 조회는 필요할 때 한 번만.
    calendarList.get이 아니라 calendars.get을 쓴다 — ID를 직접 넣은(목록에 없는) 캘린더도 읽힌다."""

    def __init__(self, client, headers: dict, cal: str, time_zone=None):
        self._client, self._headers, self._cal = client, headers, cal
        self.explicit = None if _blank(time_zone) else str(time_zone).strip()
        self._zone = ZoneInfo(self.explicit) if self.explicit else None

    async def get(self) -> ZoneInfo:
        if self._zone is None:
            data = await _get_json(self._client, self._headers, f"{_CALENDAR_API_BASE}/calendars/{self._cal}")
            self._zone = ZoneInfo(data.get("timeZone") or "UTC")
        return self._zone

    async def parse(self, value) -> datetime:
        dt = datetime.fromisoformat(str(value).strip())
        return dt if dt.tzinfo else dt.replace(tzinfo=await self.get())


def _item(event: dict) -> dict:
    """events.list 항목 → find items[] 원소. 키는 늘 같고 없는 값은 빈 문자열/빈 목록(spec §4.1)."""
    start, end = event.get("start") or {}, event.get("end") or {}
    return {
        "id": event.get("id", ""),
        "summary": event.get("summary", ""),
        "start": start.get("dateTime") or start.get("date", ""),
        "end": end.get("dateTime") or end.get("date", ""),
        "allDay": "date" in start,
        "location": event.get("location", ""),
        "description": event.get("description", ""),
        "htmlLink": event.get("htmlLink", ""),
        "recurringEventId": event.get("recurringEventId", ""),
        "status": event.get("status", ""),
        "attendees": [a["email"] for a in event.get("attendees", []) if a.get("email")],
    }


async def google_calendar_create(
    access_token: str,
    summary: str,
    start_datetime: str,
    end_datetime: str,
    description: str = "",
    calendar_id: str = "primary",
) -> str:
    """
    Google Calendar에 새 일정을 생성합니다.

    Args:
        access_token: Google OAuth Access Token
        summary: 일정 제목
        start_datetime: 시작 일시 (ISO 8601, 예: "2026-05-13T09:00:00+09:00")
        end_datetime: 종료 일시 (ISO 8601)
        description: 일정 설명 (optional)
        calendar_id: 캘린더 ID (기본값: "primary"). 공휴일 캘린더처럼 #·@가 든 id도 받는다(경로 인코딩).

    Returns:
        생성된 일정 ID, URL을 포함한 JSON 문자열
    """
    payload = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_datetime},
        "end": {"dateTime": end_datetime},
    }

    try:
        client = get_http_client()
        response = await client.post(
            f"{_CALENDAR_API_BASE}/calendars/{_calendar_segment(calendar_id)}/events",
            headers=_headers(access_token),
            json=payload,
            timeout=_TIMEOUT,
        )
        data = response.json()
        if not response.is_success:
            return json.dumps({
                "error": f"Google Calendar API 오류 ({response.status_code}): {data.get('error', {}).get('message', '알 수 없는 오류')}"
            }, ensure_ascii=False)

        return json.dumps({
            "success": True,
            "eventId": data.get("id", ""),
            "htmlLink": data.get("htmlLink", ""),
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (google_calendar_create: {str(e)})"
        }, ensure_ascii=False)


async def google_calendar_update(
    access_token: str,
    event_id: str,
    summary: Optional[str] = None,
    start_datetime: Optional[str] = None,
    end_datetime: Optional[str] = None,
    description: Optional[str] = None,
    calendar_id: str = "primary",
) -> str:
    """
    Google Calendar의 기존 일정을 수정합니다.

    Args:
        access_token: Google OAuth Access Token
        event_id: 수정할 일정 ID
        summary: 새 일정 제목 (optional, 없으면 기존 유지)
        start_datetime: 새 시작 일시 (ISO 8601, optional)
        end_datetime: 새 종료 일시 (ISO 8601, optional)
        description: 새 일정 설명 (optional)
        calendar_id: 캘린더 ID (기본값: "primary")

    Returns:
        수정된 일정 ID, URL을 포함한 JSON 문자열
    """
    # 빈 id면 PATCH가 .../events/(컬렉션 경로)로 나가 원인 모를 404/405가 난다.
    if not str(event_id or "").strip():
        return json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (google_calendar_update: event_id가 비어 있습니다)"
        }, ensure_ascii=False)

    payload = {}
    if summary is not None:
        payload["summary"] = summary
    if description is not None:
        payload["description"] = description
    # 빈 문자열 dateTime은 API가 400으로 반려하므로 실제 값이 있을 때만 포함한다(선택 슬롯 "" 방지).
    if start_datetime and start_datetime.strip():
        payload["start"] = {"dateTime": start_datetime}
    if end_datetime and end_datetime.strip():
        payload["end"] = {"dateTime": end_datetime}

    try:
        client = get_http_client()
        response = await client.patch(
            f"{_CALENDAR_API_BASE}/calendars/{_calendar_segment(calendar_id)}/events/{quote(str(event_id), safe='')}",
            headers=_headers(access_token),
            json=payload,
            timeout=_TIMEOUT,
        )
        data = response.json()
        if not response.is_success:
            return json.dumps({
                "error": f"Google Calendar API 오류 ({response.status_code}): {data.get('error', {}).get('message', '알 수 없는 오류')}"
            }, ensure_ascii=False)

        return json.dumps({
            "success": True,
            "eventId": data.get("id", ""),
            "htmlLink": data.get("htmlLink", ""),
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (google_calendar_update: {str(e)})"
        }, ensure_ascii=False)


async def google_calendar_list(
    access_token: str,
    time_min: str,
    time_max: str,
    max_results: int = 10,
    calendar_id: str = "primary",
) -> str:
    """
    Google Calendar에서 일정 목록을 조회합니다.

    Args:
        access_token: Google OAuth Access Token
        time_min: 조회 시작 일시 (ISO 8601)
        time_max: 조회 종료 일시 (ISO 8601)
        max_results: 최대 결과 수 (기본값: 10)
        calendar_id: 캘린더 ID (기본값: "primary")

    Returns:
        일정 목록을 포함한 JSON 문자열
    """
    params = {
        "timeMin": time_min,
        "timeMax": time_max,
        "maxResults": max_results,
        "singleEvents": "true",
        "orderBy": "startTime",
    }

    try:
        client = get_http_client()
        response = await client.get(
            f"{_CALENDAR_API_BASE}/calendars/{_calendar_segment(calendar_id)}/events",
            headers=_headers(access_token),
            params=params,
            timeout=_TIMEOUT,
        )
        data = response.json()
        if not response.is_success:
            return json.dumps({
                "error": f"Google Calendar API 오류 ({response.status_code}): {data.get('error', {}).get('message', '알 수 없는 오류')}"
            }, ensure_ascii=False)

        events = []
        for item in data.get("items", []):
            start = item.get("start", {})
            end = item.get("end", {})
            events.append({
                "id": item.get("id", ""),
                "summary": item.get("summary", ""),
                "start": start.get("dateTime", start.get("date", "")),
                "end": end.get("dateTime", end.get("date", "")),
            })

        return json.dumps({
            "success": True,
            "events": events,
            "total": len(events),
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (google_calendar_list: {str(e)})"
        }, ensure_ascii=False)


async def google_calendar_find(
    access_token: str,
    calendar_id: str = "primary",
    query: str = "",
    time_min: str = "",
    time_max: str = "",
    max_results: int = 50,
) -> str:
    """
    Google Calendar에서 조건에 맞는 일정을 시작 시각 순으로 찾습니다. 반복 일정은 회차별로 나옵니다.

    Args:
        access_token: Google OAuth Access Token
        calendar_id: 캘린더 ID (기본값: "primary")
        query: 검색어(제목·설명·장소·참석자). 비우면 전체
        time_min: 이 시각 이후에 끝나는 일정 (ISO 8601). 비우면 지금
        time_max: 이 시각 전에 시작하는 일정 (ISO 8601). 비우면 제한 없음
        max_results: 최대 개수 (기본 50, 최대 250)

    Returns:
        found·total·items를 담은 JSON 문자열. 0건도 성공이다.
    """
    try:  # BE는 없는 참조를 ""로, FE 숫자 칸은 문자열을 보낼 수 있다
        limit = min(max(int(max_results), 1), _FIND_MAX)
    except (TypeError, ValueError):
        limit = 50
    try:
        client = get_http_client()
        headers = _headers(access_token)
        cal = _calendar_segment(calendar_id)
        zone = _Zone(client, headers, cal)
        params = {
            "singleEvents": "true",  # 반복 일정은 회차로 펼친다
            "orderBy": "startTime",  # singleEvents=true일 때만 허용, 오름차순만
            "timeMin": (_now() if _blank(time_min) else await zone.parse(time_min)).isoformat(),
        }
        if not _blank(time_max):
            params["timeMax"] = (await zone.parse(time_max)).isoformat()
        if not _blank(query):
            params["q"] = query.strip()
        items = []
        while len(items) < limit:
            params["maxResults"] = limit - len(items)
            response = await client.get(f"{_CALENDAR_API_BASE}/calendars/{cal}/events", headers=headers,
                                        params=dict(params), timeout=_TIMEOUT)
            if not response.is_success:
                return _api_error(response)
            data = response.json()
            items += [_item(e) for e in data.get("items", [])]
            if not data.get("nextPageToken"):
                break
            params["pageToken"] = data["nextPageToken"]
        items = items[:limit]
        return json.dumps({"success": True, "found": bool(items), "total": len(items), "items": items},
                          ensure_ascii=False)
    except _ApiError as e:
        return _api_error(e.response)
    except Exception as e:
        return _fail("google_calendar_find", str(e))


def _deleted(event_id: str, deleted: bool) -> str:
    return json.dumps({"success": True, "eventId": event_id, "deleted": deleted}, ensure_ascii=False)


async def google_calendar_delete(
    access_token: str,
    event_id: str,
    calendar_id: str = "primary",
    scope: str = "this",
    send_updates: Optional[str] = None,
) -> str:
    """
    Google Calendar 일정을 삭제합니다. ACTION 노드 전용이라 AI 노드 도구 선택지에 싣지 않는다(_TOOL_MAP 밖).

    Args:
        access_token: Google OAuth Access Token
        event_id: 삭제할 일정 ID
        calendar_id: 캘린더 ID (기본값: "primary")
        scope: "this"(이 일정만, 기본) 또는 "all"(반복 일정의 회차면 반복 전체)
        send_updates: 참석자 알림 "all" | "externalOnly" | "none". 비우면 Google 기본 동작

    Returns:
        {eventId, deleted} JSON 문자열. eventId는 실제로 지운 대상(반복 전체면 원본) ID.
        이미 삭제된 일정(410·cancelled)은 성공이고 deleted=false다.
    """
    if _blank(event_id):
        return _fail("google_calendar_delete", "event_id가 비어 있습니다")
    try:
        client = get_http_client()
        headers = _headers(access_token)
        cal = _calendar_segment(calendar_id)
        try:
            target = await _resolve_target(client, headers, cal, event_id, scope)
        except _ApiError as e:
            if e.response.status_code == 410:
                return _deleted(str(event_id), False)
            raise
        target_id = target.get("id") or str(event_id)
        if target.get("status") == "cancelled":
            return _deleted(target_id, False)
        response = await client.delete(_event_url(cal, target_id), headers=headers,
                                       params=_send_updates(send_updates), timeout=_TIMEOUT)
        # 성공 응답은 빈 본문이다 — 상태 코드를 먼저 보고 본문은 실패일 때만 읽는다.
        if response.is_success:
            return _deleted(target_id, True)
        if response.status_code == 410:
            return _deleted(target_id, False)
        # 404(없는 ID·접근 불가 캘린더·캘린더/일정 엇갈림)는 실패다. 삼키면 일정이 남은 채 "성공"이 된다.
        return _api_error(response)
    except _ApiError as e:
        return _api_error(e.response)
    except Exception as e:
        return _fail("google_calendar_delete", str(e))

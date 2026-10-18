import json
from datetime import date, datetime, timedelta, timezone
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

    async def day(self, value) -> date:
        """종일 날짜. 오프셋 있는 시각은 이 시간대로 바꾼 뒤 날짜를 뽑는다(UTC Z 입력의 하루 어긋남 방지)."""
        dt = datetime.fromisoformat(str(value).strip())
        return dt.astimezone(await self.get()).date() if dt.tzinfo else dt.date()


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


def _timed(value: datetime, time_zone: Optional[str]) -> dict:
    # 종일 → 시각 전환이면 남은 date를 지운다(PATCH는 병합이다, spec §4.2-5).
    piece = {"dateTime": value.isoformat(), "date": None}
    if time_zone:
        piece["timeZone"] = time_zone
    return piece


async def _all_day_times(cur_start: dict, cur_end: dict, start, end, was_all_day: bool, zone: _Zone) -> dict:
    """종일(spec §4.2-5). 종료일은 포함으로 받아 Google 배타 규칙에 맞춰 +1일. 종료가 비면 원래 종일이면
    일수 유지(§4.2-2), 시각에서 바꾸면 시작일 하루. 시각 칸은 null로 지운다."""
    if start is not None:
        first = await zone.day(start)
    elif was_all_day:
        first = date.fromisoformat(cur_start["date"])
    else:
        first = await zone.day(cur_start["dateTime"])
    if end is not None:
        after = await zone.day(end) + timedelta(days=1)
    elif was_all_day:
        after = first + (date.fromisoformat(cur_end["date"]) - date.fromisoformat(cur_start["date"]))
    else:
        after = first + timedelta(days=1)
    return {"start": {"date": first.isoformat(), "dateTime": None, "timeZone": None},
            "end": {"date": after.isoformat(), "dateTime": None, "timeZone": None}}


async def _timed_times(cur_start: dict, cur_end: dict, start, end, was_all_day: bool, zone: _Zone):
    """시각 일정. 시작만 오면 기존 길이 유지(§4.2-2), 종료만 오면 시작은 그대로.
    종일에서 바꿀 땐 기존 시각이 없어 둘 다 필요하다 — 지어낸 시각으로 바꾸지 않는다."""
    if was_all_day and (start is None or end is None):
        return "종일 일정을 시각 일정으로 바꾸려면 시작과 종료를 모두 입력하세요"
    begin = await zone.parse(start) if start is not None else datetime.fromisoformat(cur_start["dateTime"])
    if end is not None:
        finish = await zone.parse(end)
    else:
        finish = begin + (datetime.fromisoformat(cur_end["dateTime"]) - datetime.fromisoformat(cur_start["dateTime"]))
    return {"start": _timed(begin, zone.explicit), "end": _timed(finish, zone.explicit)}


async def _series_times(cur_start: dict, cur_end: dict, start, end, zone: _Zone) -> dict:
    """반복 전체 + 시각 변경(spec §4.2-3). 원본 시작을 회차 날짜로 덮으면 반복 시작일이 옮겨져 이전 회차가
    사라지므로, 입력 시각을 원본 start.timeZone의 벽시계 시각으로 옮겨 원본 날짜와 합친다.
    종료 = 그 시작 + (입력 종료 − 입력 시작, 없으면 원본 길이). 종료만 오면 원본 날짜에 붙이고 시작 이하면 다음 날."""
    tz = ZoneInfo(cur_start["timeZone"]) if cur_start.get("timeZone") else await zone.get()
    orig_start = datetime.fromisoformat(cur_start["dateTime"]).astimezone(tz)
    orig_end = datetime.fromisoformat(cur_end["dateTime"]).astimezone(tz)
    begin_in = await zone.parse(start) if start is not None else None
    end_in = await zone.parse(end) if end is not None else None
    if begin_in is not None:
        begin = datetime.combine(orig_start.date(), begin_in.astimezone(tz).time(), tzinfo=tz)
        finish = begin + ((end_in - begin_in) if end_in is not None else (orig_end - orig_start))
    else:
        begin = orig_start
        finish = datetime.combine(orig_start.date(), end_in.astimezone(tz).time(), tzinfo=tz)
        if finish <= begin:
            finish += timedelta(days=1)  # 자정 넘김
    return {"start": _timed(begin, tz.key), "end": _timed(finish, tz.key)}


async def _new_times(target: dict, start, end, all_day, zone: _Zone, series: bool):
    """start/end PATCH 조각(spec §4.2 2~5). 바꿀 수 없으면 사용자에게 보일 이유(str)를 돌려준다."""
    start = None if _blank(start) else start
    end = None if _blank(end) else end
    cur_start, cur_end = target.get("start") or {}, target.get("end") or {}
    was_all_day = "date" in cur_start
    to_all_day = was_all_day if all_day is None else bool(all_day)
    if start is None and end is None and to_all_day == was_all_day:
        return {}
    if series:
        if to_all_day != was_all_day:
            return "반복 전체 일정의 종일 여부는 바꿀 수 없어요"
        if was_all_day:
            return "반복 전체 종일 일정의 날짜는 바꿀 수 없어요"  # 날짜를 유지하면 바꿀 것이 없다(§4.2-4)
        return await _series_times(cur_start, cur_end, start, end, zone)
    if to_all_day:
        return await _all_day_times(cur_start, cur_end, start, end, was_all_day, zone)
    return await _timed_times(cur_start, cur_end, start, end, was_all_day, zone)


async def google_calendar_update(
    access_token: str,
    event_id: str,
    summary: Optional[str] = None,
    start_datetime: Optional[str] = None,
    end_datetime: Optional[str] = None,
    description: Optional[str] = None,
    calendar_id: str = "primary",
    scope: str = "this",
    all_day: Optional[bool] = None,
    time_zone: Optional[str] = None,
    location: Optional[str] = None,
    attendees: Optional[str] = None,
    send_updates: Optional[str] = None,
) -> str:
    """
    Google Calendar의 기존 일정을 수정합니다. 비운 칸은 바꾸지 않습니다.

    Args:
        access_token: Google OAuth Access Token
        event_id: 수정할 일정 ID
        summary: 새 일정 제목 (optional)
        start_datetime: 새 시작 일시 (ISO 8601). 시작만 주면 기존 길이를 유지한다
        end_datetime: 새 종료 일시 (ISO 8601). 종일 일정이면 끝나는 날(포함)
        description: 새 일정 설명 (optional)
        calendar_id: 캘린더 ID (기본값: "primary")
        scope: "this"(이 일정만, 기본) 또는 "all"(반복 일정이면 반복 전체 — 날짜는 두고 시각만 바꾼다)
        all_day: true면 종일 일정, false면 시각 일정으로 바꾼다. 비우면 그대로
        time_zone: 시간대 IANA 이름(예: "Asia/Seoul"). 오프셋 없는 시각을 이 시간대로 읽는다. 비우면 캘린더 시간대
        location: 새 장소 (optional)
        attendees: 참석자 이메일, 쉼표로 구분. 기존 참석자를 모두 바꾼다
        send_updates: 참석자 알림 "all" | "externalOnly" | "none". 비우면 Google 기본 동작

    Returns:
        수정된 일정 ID, URL을 포함한 JSON 문자열
    """
    # 빈 id면 PATCH가 .../events/(컬렉션 경로)로 나가 원인 모를 404/405가 난다.
    if _blank(event_id):
        return _fail("google_calendar_update", "event_id가 비어 있습니다")
    payload = {k: v for k, v in (("summary", summary), ("description", description), ("location", location))
               if not _blank(v)}
    emails = [] if _blank(attendees) else [e.strip() for e in str(attendees).split(",") if e.strip()]
    if emails:  # 쉼표뿐이면 보내지 않는다 — []를 보내면 참석자가 전부 지워진다
        payload["attendees"] = [{"email": e} for e in emails]
    all_day = None if _blank(all_day) else all_day

    try:
        client = get_http_client()
        headers = _headers(access_token)
        cal = _calendar_segment(calendar_id)
        target_id = str(event_id)
        # 현재 일정이 필요할 때만 조회한다(반복 원본 해석·길이 유지·종일 전환). 제목만 바꾸면 PATCH 한 번.
        if scope == "all" or not _blank(start_datetime) or not _blank(end_datetime) or all_day is not None:
            target = await _resolve_target(client, headers, cal, event_id, scope)
            target_id = target.get("id") or target_id
            times = await _new_times(target, start_datetime, end_datetime, all_day,
                                     _Zone(client, headers, cal, time_zone),
                                     series=scope == "all" and bool(target.get("recurrence")))
            if isinstance(times, str):
                return _fail("google_calendar_update", times)
            payload.update(times)
        response = await client.patch(_event_url(cal, target_id), headers=headers, json=payload,
                                      params=_send_updates(send_updates), timeout=_TIMEOUT)
        if not response.is_success:
            return _api_error(response)
        data = response.json()
        return json.dumps({"success": True, "eventId": data.get("id", ""), "htmlLink": data.get("htmlLink", "")},
                          ensure_ascii=False)
    except _ApiError as e:
        return _api_error(e.response)
    except Exception as e:
        return _fail("google_calendar_update", str(e))


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

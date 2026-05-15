import json

import httpx

from common.error_code import ToolErrorCode

_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
_TIMEOUT = 30.0


def _headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
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
        calendar_id: 캘린더 ID (기본값: "primary")

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
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{_CALENDAR_API_BASE}/calendars/{calendar_id}/events",
                headers=_headers(access_token),
                json=payload,
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
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(
                f"{_CALENDAR_API_BASE}/calendars/{calendar_id}/events",
                headers=_headers(access_token),
                params=params,
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

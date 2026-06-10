import json
from typing import Optional

import httpx

from common.error_code import ToolErrorCode
from tools.http_client import get_http_client

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
        client = get_http_client()
        response = await client.post(
            f"{_CALENDAR_API_BASE}/calendars/{calendar_id}/events",
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
            f"{_CALENDAR_API_BASE}/calendars/{calendar_id}/events/{event_id}",
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
            f"{_CALENDAR_API_BASE}/calendars/{calendar_id}/events",
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

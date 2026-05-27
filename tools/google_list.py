import json
import httpx

_TIMEOUT = 30.0


async def google_list_calendars(access_token: str) -> str:
    """
    Google Calendar 목록을 조회합니다.

    Args:
        access_token: Google OAuth access token

    Returns:
        캘린더 목록 (id, summary, primary)을 포함한 JSON 문자열
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(
                "https://www.googleapis.com/calendar/v3/users/me/calendarList",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if response.status_code != 200:
                return json.dumps(
                    {"error": f"Google Calendar API 오류 ({response.status_code}): {response.text}"},
                    ensure_ascii=False,
                )
            data = response.json()
            calendars = [
                {
                    "id": c["id"],
                    "summary": c.get("summary", ""),
                    "primary": c.get("primary", False),
                }
                for c in data.get("items", [])
            ]
            return json.dumps({"success": True, "calendars": calendars, "total": len(calendars)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


async def google_list_sheets(access_token: str) -> str:
    """
    Google Drive에서 스프레드시트 파일 목록을 조회합니다.

    Args:
        access_token: Google OAuth access token

    Returns:
        스프레드시트 목록 (id, name)을 포함한 JSON 문자열
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(
                "https://www.googleapis.com/drive/v3/files",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "q": "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false",
                    "fields": "files(id,name)",
                    "pageSize": 30,
                    "orderBy": "modifiedTime desc",
                },
            )
            if response.status_code != 200:
                return json.dumps(
                    {"error": f"Google Drive API 오류 ({response.status_code}): {response.text}"},
                    ensure_ascii=False,
                )
            data = response.json()
            sheets = [{"id": f["id"], "name": f["name"]} for f in data.get("files", [])]
            return json.dumps({"success": True, "sheets": sheets, "total": len(sheets)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

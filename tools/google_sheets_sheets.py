"""Google Sheets 시트·파일 도구(레인 S): 워크시트 만들기·찾고 없으면 만들기·이름 바꾸기."""
import json

from tools.google_sheets import (
    _SHEET_PROPS, _TIMEOUT, _Invalid, _a1_range, _batch_update, _blank, _check, _fail, _find_sheet, _grid_sheets,
    _headers, _ok, _put, _sid, _spreadsheet_url,
)
from tools.http_client import get_http_client


def _split_headers(headers) -> list[str]:
    """쉼표 구분 헤더 → 목록. 끝쪽 빈 항목만 버리고 가운데 빈 항목은 ""로 둔다."""
    if _blank(headers):
        return []
    if not isinstance(headers, str):
        raise _Invalid("헤더는 쉼표로 구분한 글자여야 해요")
    items = [h.strip() for h in headers.split(",")]
    while items and not items[-1]:
        items.pop()
    return items


def _title(title, message: str) -> str:
    if _blank(title):
        raise _Invalid(message)
    return str(title)


async def _create_worksheet(client, access_token: str, spreadsheet_id: str, title: str, headers: list[str]) -> dict:
    data = await _batch_update(client, access_token, spreadsheet_id, {"addSheet": {"properties": {
        "title": title, "gridProperties": {"columnCount": max(26, len(headers))}}}})
    props = data["replies"][0]["addSheet"]["properties"]
    new_id, new_title = props.get("sheetId", 0), props.get("title", title)
    if headers:
        try:
            await _put(client, access_token, spreadsheet_id, _a1_range("A1", new_title), headers, "RAW")
        except Exception as e:
            try:  # 만든 시트를 지워 되돌린다
                await _batch_update(client, access_token, spreadsheet_id, {"deleteSheet": {"sheetId": new_id}})
            except Exception:
                raise _Invalid(f"{e} — 만든 시트 '{new_title}'이 남았을 수 있어요") from e
            raise
    return {"sheet_id": str(new_id), "title": new_title}


async def google_sheets_create_worksheet(access_token: str, spreadsheet_id: str, title: str,
                                         headers: str | None = None) -> str:
    """
    스프레드시트에 새 워크시트(탭)를 만듭니다. 같은 이름 시트가 있으면 실패합니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID
        title: 새 시트 제목
        headers: 1행에 쓸 열 제목(쉼표 구분). 예: 이름,이메일,날짜

    Returns:
        spreadsheet_id, sheet_id, title을 포함한 JSON 문자열
    """
    tool = "google_sheets_create_worksheet"
    try:
        spreadsheet_id = _sid(spreadsheet_id)
        title = _title(title, "시트 제목이 비어 있어요")
        cols = _split_headers(headers)
        client = get_http_client()
        sheet = await _create_worksheet(client, access_token, spreadsheet_id, title, cols)
        return json.dumps({"success": True, "spreadsheet_id": spreadsheet_id, **sheet}, ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))


async def google_sheets_find_or_create_worksheet(access_token: str, spreadsheet_id: str, title: str,
                                                 headers: str | None = None) -> str:
    """
    제목이 같은 워크시트를 찾고(대소문자 구분 정확히 일치) 없으면 만듭니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID
        title: 시트 제목
        headers: 새로 만들 때 1행에 쓸 열 제목(쉼표 구분)

    Returns:
        created, sheet_id, title을 포함한 JSON 문자열
    """
    tool = "google_sheets_find_or_create_worksheet"
    try:
        spreadsheet_id = _sid(spreadsheet_id)
        title = _title(title, "시트 제목이 비어 있어요")
        cols = _split_headers(headers)
        client = get_http_client()
        response = await client.get(
            _spreadsheet_url(spreadsheet_id),
            headers=_headers(access_token),
            params={"fields": _SHEET_PROPS},
            timeout=_TIMEOUT,
        )
        found = next((s for s in _grid_sheets(_ok(response)) if s["title"] == title), None)
        if found:
            return json.dumps({"success": True, "created": False, "sheet_id": found["sheet_id"],
                               "title": found["title"]}, ensure_ascii=False)
        sheet = await _create_worksheet(client, access_token, spreadsheet_id, title, cols)
        return json.dumps({"success": True, "created": True, **sheet}, ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))


async def google_sheets_rename_worksheet(access_token: str, spreadsheet_id: str, sheet_id: str,
                                         new_title: str) -> str:
    """
    워크시트 이름을 바꿉니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID
        sheet_id: 워크시트 ID 또는 제목
        new_title: 새 이름

    Returns:
        sheet_id, title(새 이름), previous_title을 포함한 JSON 문자열
    """
    tool = "google_sheets_rename_worksheet"
    try:
        spreadsheet_id = _check(spreadsheet_id, sheet_id)
        new_title = _title(new_title, "새 이름이 비어 있어요")
        client = get_http_client()
        sid, old, _ = await _find_sheet(client, access_token, spreadsheet_id, sheet_id)
        await _batch_update(client, access_token, spreadsheet_id, {"updateSheetProperties": {
            "properties": {"sheetId": sid, "title": new_title}, "fields": "title"}})
        return json.dumps({"success": True, "sheet_id": str(sid), "title": new_title, "previous_title": old},
                          ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))

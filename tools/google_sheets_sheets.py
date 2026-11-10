"""Google Sheets 시트·파일 도구(레인 S): 워크시트 만들기·복사·이름·속성·삭제, 스프레드시트 만들기, 찾고 없으면 만들기."""
import json
from urllib.parse import quote

from tools.google_sheets import (
    _DRIVE_FILES_URL, _SHEET_PROPS, _SPREADSHEET_ID, _TIMEOUT, _Invalid, _a1_range, _batch_update, _blank, _check,
    _fail, _find_sheet, _find_spreadsheets, _flag, _grid_sheets, _headers, _int, _ok, _put, _sid, _spreadsheet_url,
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


def _copied_id(props: dict) -> int:
    # 기본값 0을 쓰면 되돌리기 deleteSheet가 대상 파일의 기존 시트 0을 지울 수 있다.
    sheet_id = props.get("sheetId")
    if not isinstance(sheet_id, int) or isinstance(sheet_id, bool):
        raise _Invalid("복사본 시트 ID를 받지 못했어요. 대상 파일에 복사본이 생겼는지 확인해 주세요")
    return sheet_id


async def google_sheets_copy_worksheet(access_token: str, spreadsheet_id: str, sheet_id: str,
                                       destination_spreadsheet_id: str | None = None,
                                       new_title: str | None = None) -> str:
    """
    워크시트를 같은 파일이나 다른 스프레드시트로 복사합니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 원본 스프레드시트 ID
        sheet_id: 복사할 워크시트 ID 또는 제목
        destination_spreadsheet_id: 붙여넣을 스프레드시트 ID. 비우면 같은 파일에 복사
        new_title: 복사본 시트 이름. 비우면 Google이 정한 이름

    Returns:
        spreadsheet_id(복사본이 있는 파일), sheet_id, title을 포함한 JSON 문자열
    """
    tool = "google_sheets_copy_worksheet"
    try:
        sid = _check(spreadsheet_id, sheet_id)
        dest = sid if _blank(destination_spreadsheet_id) else _sid(destination_spreadsheet_id)
        name = None if _blank(new_title) else str(new_title)
        client = get_http_client()
        src_id, _, _ = await _find_sheet(client, access_token, sid, sheet_id)
        if dest == sid:
            data = await _batch_update(client, access_token, sid, {"duplicateSheet": {
                "sourceSheetId": src_id, **({"newSheetName": name} if name else {})}})
            props = data["replies"][0]["duplicateSheet"]["properties"]
            return json.dumps({"success": True, "spreadsheet_id": dest, "sheet_id": str(_copied_id(props)),
                               "title": props.get("title", "")}, ensure_ascii=False)
        response = await client.post(
            f"{_spreadsheet_url(sid)}/sheets/{src_id}:copyTo",
            headers=_headers(access_token),
            json={"destinationSpreadsheetId": dest},
            timeout=_TIMEOUT,
        )
        props = _ok(response)
        new_id, title = _copied_id(props), props.get("title", "")
        if name:
            try:
                await _batch_update(client, access_token, dest, {"updateSheetProperties": {
                    "properties": {"sheetId": new_id, "title": name}, "fields": "title"}})
            except Exception as e:
                try:  # 대상 파일의 복사본을 지워 되돌린다
                    await _batch_update(client, access_token, dest, {"deleteSheet": {"sheetId": new_id}})
                except Exception:
                    raise _Invalid(f"{e} — 대상 파일에 '{title}' 시트가 남았을 수 있어요") from e
                raise
            title = name
        return json.dumps({"success": True, "spreadsheet_id": dest, "sheet_id": str(new_id), "title": title},
                          ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))


async def _sheet_props(client, access_token: str, spreadsheet_id: str, sheet_id) -> dict:
    """_grid_sheets 항목 하나(index 포함). 규칙은 _find_sheet와 같다(숫자 id 먼저, 없으면 제목 정확히 일치)."""
    response = await client.get(
        _spreadsheet_url(spreadsheet_id),
        headers=_headers(access_token),
        params={"fields": _SHEET_PROPS},
        timeout=_TIMEOUT,
    )
    sheets = _grid_sheets(_ok(response))
    key = str(sheet_id).strip()
    if key.isascii() and key.isdigit():
        found = next((s for s in sheets if s["sheet_id"] == str(int(key))), None)
        if found:
            return found
    found = next((s for s in sheets if s["title"] == str(sheet_id)), None)
    if not found:
        raise _Invalid(f"워크시트 '{sheet_id}'를 찾을 수 없어요")
    return found


def _count(value, name: str) -> int | None:
    if _blank(value):
        return None
    n = _int(value, None, name)
    if n < 0:
        raise _Invalid(f"{name}은(는) 0 이상이어야 해요: {value!r}")
    return n


async def google_sheets_update_worksheet(access_token: str, spreadsheet_id: str, sheet_id: str,
                                         frozen_rows: int | None = None, frozen_columns: int | None = None,
                                         position: int | None = None, visibility: str | None = None) -> str:
    """
    워크시트의 고정 행·열, 위치, 숨기기를 바꿉니다. 비운 칸은 그대로 둡니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID
        sheet_id: 워크시트 ID 또는 제목
        frozen_rows: 고정할 행 수(0이면 고정 해제)
        frozen_columns: 고정할 열 수(0이면 고정 해제)
        position: 시트 위치(1이 맨 앞)
        visibility: hide(숨기기) 또는 show(보이기)

    Returns:
        sheet_id, title, updated(바꾼 속성 목록)를 포함한 JSON 문자열
    """
    tool = "google_sheets_update_worksheet"
    try:
        spreadsheet_id = _check(spreadsheet_id, sheet_id)
        rows = _count(frozen_rows, "고정할 행 수")
        cols = _count(frozen_columns, "고정할 열 수")
        pos = None if _blank(position) else _int(position, None, "시트 위치")
        if pos is not None and pos < 1:
            raise _Invalid(f"시트 위치는 1 이상이어야 해요: {position!r}")
        hidden = None
        if not _blank(visibility):
            if visibility not in ("hide", "show"):
                raise _Invalid("보이기는 hide 또는 show만 돼요")
            hidden = visibility == "hide"
        if rows is None and cols is None and pos is None and hidden is None:
            raise _Invalid("바꿀 속성이 없어요")
        client = get_http_client()
        sheet = await _sheet_props(client, access_token, spreadsheet_id, sheet_id)
        props, fields, updated = {"sheetId": int(sheet["sheet_id"])}, [], []
        if rows is not None:
            props.setdefault("gridProperties", {})["frozenRowCount"] = rows
            fields.append("gridProperties.frozenRowCount")
            updated.append("frozen_rows")
        if cols is not None:
            props.setdefault("gridProperties", {})["frozenColumnCount"] = cols
            fields.append("gridProperties.frozenColumnCount")
            updated.append("frozen_columns")
        if pos is not None:
            target = pos - 1
            # Google index는 "옮기기 전" 기준 — 오른쪽으로 옮길 땐 자기 자리가 빠지므로 하나 더한다.
            props["index"] = target + 1 if target > sheet["index"] else target
            fields.append("index")
            updated.append("position")
        if hidden is not None:
            props["hidden"] = hidden
            fields.append("hidden")
            updated.append("visibility")
        await _batch_update(client, access_token, spreadsheet_id, {"updateSheetProperties": {
            "properties": props, "fields": ",".join(fields)}})
        return json.dumps({"success": True, "sheet_id": sheet["sheet_id"], "title": sheet["title"],
                           "updated": updated}, ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))


async def google_sheets_delete_worksheet(access_token: str, spreadsheet_id: str, sheet_id: str,
                                         confirm_delete: bool) -> str:
    """
    워크시트를 지웁니다. 되돌릴 수 없으며 confirm_delete가 참일 때만 지웁니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID
        sheet_id: 워크시트 ID 또는 제목
        confirm_delete: 삭제 확인. 참이어야 삭제

    Returns:
        deleted, sheet_id, title(지운 시트 이름)을 포함한 JSON 문자열
    """
    tool = "google_sheets_delete_worksheet"
    try:
        if not _flag(confirm_delete):
            raise _Invalid("삭제 확인을 켜야 삭제돼요")
        spreadsheet_id = _check(spreadsheet_id, sheet_id)
        client = get_http_client()
        sid, title, _ = await _find_sheet(client, access_token, spreadsheet_id, sheet_id)
        await _batch_update(client, access_token, spreadsheet_id, {"deleteSheet": {"sheetId": sid}})
        return json.dumps({"success": True, "deleted": True, "sheet_id": str(sid), "title": title},
                          ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))


def _drive_id(value, message: str) -> str | None:
    if _blank(value):
        return None
    v = str(value).strip()
    if not _SPREADSHEET_ID.fullmatch(v):
        raise _Invalid(f"{message}: {value!r}")
    return v


def _file_args(title, folder_id, copy_from, headers) -> tuple[str, str | None, str | None, list[str]]:
    title = _title(title, "이름이 비어 있어요").strip()
    folder_id = _drive_id(folder_id, "폴더 ID가 올바르지 않아요")
    copy_from = _drive_id(copy_from, "복사할 스프레드시트 ID가 올바르지 않아요")
    return title, folder_id, copy_from, [] if copy_from else _split_headers(headers)


async def _create_spreadsheet(client, access_token: str, title: str, folder_id: str | None, copy_from: str | None,
                              headers: list[str]) -> dict:
    params = {"supportsAllDrives": "true", "fields": "id,name,webViewLink"}
    body = {"name": title, **({"parents": [folder_id]} if folder_id else {})}
    if copy_from:
        url = f"{_DRIVE_FILES_URL}/{quote(copy_from, safe='')}/copy"
    else:
        url, body = _DRIVE_FILES_URL, {**body, "mimeType": "application/vnd.google-apps.spreadsheet"}
    response = await client.post(url, headers=_headers(access_token), params=params, json=body, timeout=_TIMEOUT)
    data = _ok(response, "Google Drive API")
    fid = data.get("id", "")
    if headers:  # copy_from이면 _file_args가 비워 둔다
        try:
            if len(headers) > 26:
                await _batch_update(client, access_token, fid, {"appendDimension": {
                    "sheetId": 0, "dimension": "COLUMNS", "length": len(headers) - 26}})
            await _put(client, access_token, fid, "A1", headers, "RAW")
        except Exception as e:
            try:  # 만든 파일을 휴지통으로 보내 되돌린다
                trash = await client.patch(f"{_DRIVE_FILES_URL}/{quote(fid, safe='')}",
                                           headers=_headers(access_token), params={"supportsAllDrives": "true"},
                                           json={"trashed": True}, timeout=_TIMEOUT)
                _ok(trash, "Google Drive API")
            except Exception:
                raise _Invalid(f"{e} — 만든 스프레드시트 '{title}'({fid})가 남았을 수 있어요") from e
            raise
    return {"spreadsheet_id": fid, "title": data.get("name", title), "url": data.get("webViewLink", "")}


async def google_sheets_create_spreadsheet(access_token: str, title: str, folder_id: str | None = None,
                                           copy_from: str | None = None, headers: str | None = None) -> str:
    """
    새 Google Sheets 스프레드시트를 만들거나 기존 파일을 복사합니다. 같은 이름 파일이 있어도 새로 만듭니다.

    Args:
        access_token: Google OAuth Access Token
        title: 파일 이름
        folder_id: 만들 폴더 ID. 비우면 내 드라이브 최상위
        copy_from: 복사할 스프레드시트 ID. 고르면 이 파일을 복사(헤더는 무시)
        headers: 첫 시트 1행에 쓸 열 제목(쉼표 구분)

    Returns:
        spreadsheet_id, title, url을 포함한 JSON 문자열
    """
    tool = "google_sheets_create_spreadsheet"
    try:
        args = _file_args(title, folder_id, copy_from, headers)
        client = get_http_client()
        created = await _create_spreadsheet(client, access_token, *args)
        return json.dumps({"success": True, **created}, ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))


async def google_sheets_find_or_create_spreadsheet(access_token: str, title: str, folder_id: str | None = None,
                                                   copy_from: str | None = None, headers: str | None = None) -> str:
    """
    이름이 정확히 같은 스프레드시트를 찾고(폴더를 고르면 그 폴더 안에서만) 없으면 만듭니다.

    Args:
        access_token: Google OAuth Access Token
        title: 파일 이름(정확히 일치)
        folder_id: 찾고 만들 폴더 ID. 비우면 전체에서 찾고 내 드라이브 최상위에 만듦
        copy_from: 없을 때 복사할 스프레드시트 ID(헤더는 무시)
        headers: 새로 만들 때 첫 시트 1행에 쓸 열 제목(쉼표 구분)

    Returns:
        created, spreadsheet_id, title, url을 포함한 JSON 문자열
    """
    tool = "google_sheets_find_or_create_spreadsheet"
    try:
        title, folder_id, copy_from, cols = _file_args(title, folder_id, copy_from, headers)
        client = get_http_client()
        items, _ = await _find_spreadsheets(client, access_token, title, "exact", folder_id)
        if items:  # 최근 수정 순 첫 항목
            first = items[0]
            return json.dumps({"success": True, "created": False, "spreadsheet_id": first["id"],
                               "title": first["name"], "url": first["url"]}, ensure_ascii=False)
        created = await _create_spreadsheet(client, access_token, title, folder_id, copy_from, cols)
        return json.dumps({"success": True, "created": True, **created}, ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))

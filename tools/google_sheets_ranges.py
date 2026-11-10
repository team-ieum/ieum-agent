"""Google Sheets 열·범위 도구(ACTION 전용): 열 추가·범위 복사·정렬·서식·조건부 서식·데이터 유효성."""
import json
import re

from tools.google_sheets import (
    _TIMEOUT, _Invalid, _a1_range, _batch_update, _blank, _cell_format, _check, _col, _col_index, _fail,
    _find_sheet, _header_range, _headers, _ok, _put, _resolve, _values_url,
)
from tools.http_client import get_http_client

_PASTE_TYPES = ("PASTE_NORMAL", "PASTE_VALUES", "PASTE_FORMAT", "PASTE_NO_BORDERS", "PASTE_FORMULA",
                "PASTE_DATA_VALIDATION", "PASTE_CONDITIONAL_FORMATTING")
_PASTE_ORIENTATIONS = ("NORMAL", "TRANSPOSE")
_SORT_ORDERS = ("ASCENDING", "DESCENDING")
_NUMBER_FORMATS = ("TEXT", "NUMBER", "PERCENT", "CURRENCY", "DATE", "TIME", "DATE_TIME", "SCIENTIFIC")
_H_ALIGNS = ("LEFT", "CENTER", "RIGHT")
_V_ALIGNS = ("TOP", "MIDDLE", "BOTTOM")
_WRAPS = ("OVERFLOW_CELL", "CLIP", "WRAP")

_A1_PART = re.compile(r"([A-Z]{1,3})?([0-9]{1,7})?")


def _choice(value, allowed, name: str, default: str | None = None) -> str | None:
    """빈 값이면 default, 아니면 allowed 안의 값만."""
    if _blank(value):
        return default
    v = str(value).strip()
    if v not in allowed:
        raise _Invalid(f"{name} 값이 올바르지 않아요: {value!r}")
    return v


def _grid_range(cell_range, sheet_id: int) -> dict:
    """A1 범위(시트 부분은 버림) → batchUpdate GridRange. 끝 제외·0부터, 생략한 방향은 키를 빼 '끝까지'."""
    bad = _Invalid(f"범위가 올바르지 않아요: {cell_range!r}. 예: A1:C10, A:A, 2:10")
    if _blank(cell_range):
        raise bad
    pieces = str(cell_range).rpartition("!")[2].strip().upper().replace("$", "").split(":")
    if len(pieces) > 2:
        raise bad
    parsed = []
    for p in pieces:
        m = _A1_PART.fullmatch(p)
        if not p or not m or (m.group(2) and int(m.group(2)) == 0):
            raise bad
        parsed.append((m.group(1), int(m.group(2)) if m.group(2) else None))
    kinds = tuple("cell" if c and r else "col" if c else "row" for c, r in parsed)
    if kinds not in (("cell",), ("cell", "cell"), ("col", "col"), ("row", "row"), ("cell", "col")):
        raise bad
    (c1, r1), (c2, r2) = parsed[0], parsed[-1]
    grid = {"sheetId": sheet_id}
    if r1:
        grid["startRowIndex"] = r1 - 1
        if r2:
            if r2 < r1:
                raise bad
            grid["endRowIndex"] = r2
    if c1:
        start, end = _col_index(c1), _col_index(c2)
        if end < start:
            raise bad
        grid["startColumnIndex"], grid["endColumnIndex"] = start, end + 1
    return grid


async def google_sheets_copy_range(access_token: str, spreadsheet_id: str, sheet_id: str, source_range: str,
                                   destination_range: str, paste_type: str = "PASTE_NORMAL",
                                   paste_orientation: str = "NORMAL") -> str:
    """
    Google Sheets 범위를 같은 시트의 다른 위치에 붙여 넣습니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID
        sheet_id: 워크시트 ID 또는 제목
        source_range: 복사할 범위(A1 표기, 예: A1:C10). 시트 부분은 무시
        destination_range: 붙여 넣을 위치. 왼쪽 위 셀 하나만 써도 된다(예: E1)
        paste_type: 붙여 넣을 내용(PASTE_NORMAL·PASTE_VALUES·PASTE_FORMAT 등). 비우면 전부
        paste_orientation: NORMAL(그대로) 또는 TRANSPOSE(행·열 바꾸기)

    Returns:
        source, destination(시트 이름이 붙은 범위)을 포함한 JSON 문자열
    """
    tool = "google_sheets_copy_range"
    try:
        spreadsheet_id = _check(spreadsheet_id, sheet_id)
        paste_type = _choice(paste_type, _PASTE_TYPES, "붙여 넣을 내용", "PASTE_NORMAL")
        paste_orientation = _choice(paste_orientation, _PASTE_ORIENTATIONS, "방향", "NORMAL")
        _grid_range(source_range, 0)
        _grid_range(destination_range, 0)
        client = get_http_client()
        sid, title, _ = await _find_sheet(client, access_token, spreadsheet_id, sheet_id)
        await _batch_update(client, access_token, spreadsheet_id, [{"copyPaste": {
            "source": _grid_range(source_range, sid), "destination": _grid_range(destination_range, sid),
            "pasteType": paste_type, "pasteOrientation": paste_orientation}}])
        return json.dumps({"success": True, "source": _a1_range(str(source_range).strip(), title),
                           "destination": _a1_range(str(destination_range).strip(), title)}, ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))


async def google_sheets_sort_range(access_token: str, spreadsheet_id: str, sheet_id: str, sort_column: str,
                                   sort_order: str = "ASCENDING", cell_range: str | None = None) -> str:
    """
    Google Sheets 범위의 행을 기준 열 값으로 정렬합니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID
        sheet_id: 워크시트 ID 또는 제목
        sort_column: 기준 열 문자(A, B…). 시트 기준 열이며 범위 안에 있어야 한다
        sort_order: ASCENDING(오름차순) 또는 DESCENDING(내림차순)
        cell_range: 정렬할 범위(A1 표기). 비우면 머리글(1행)을 뺀 전체

    Returns:
        range(정렬한 범위)를 포함한 JSON 문자열
    """
    tool = "google_sheets_sort_range"
    try:
        spreadsheet_id = _check(spreadsheet_id, sheet_id)
        sort_order = _choice(sort_order, _SORT_ORDERS, "순서", "ASCENDING")
        column = "" if sort_column is None else str(sort_column).strip().upper()
        if not re.fullmatch(r"[A-Z]{1,3}", column):
            raise _Invalid(f"기준 열은 열 문자(A, B…)만 돼요: {sort_column!r}")
        index = _col_index(column)
        has_range = not _blank(cell_range)
        if has_range:
            shape = _grid_range(cell_range, 0)
            if "startColumnIndex" in shape and not shape["startColumnIndex"] <= index < shape["endColumnIndex"]:
                raise _Invalid(f"기준 열 {column}이(가) 정렬할 범위 밖이에요: {cell_range!r}")
        client = get_http_client()
        sid, title, column_count = await _find_sheet(client, access_token, spreadsheet_id, sheet_id)
        grid = _grid_range(cell_range, sid) if has_range else {"sheetId": sid, "startRowIndex": 1}
        await _batch_update(client, access_token, spreadsheet_id, [{"sortRange": {
            "range": grid, "sortSpecs": [{"dimensionIndex": index, "sortOrder": sort_order}]}}])
        shown = str(cell_range).strip() if has_range else f"A2:{_col(column_count - 1)}"
        return json.dumps({"success": True, "range": _a1_range(shown, title)}, ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))


async def google_sheets_add_column(access_token: str, spreadsheet_id: str, sheet_id: str, title: str,
                                   before_column: str | None = None) -> str:
    """
    Google Sheets 워크시트에 1행 머리글을 쓴 새 열을 넣습니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID
        sheet_id: 워크시트 ID 또는 제목
        title: 1행에 쓸 머리글(입력한 글자 그대로 저장)
        before_column: 이 열 앞에 넣는다(머리글 이름 또는 열 문자). 비우면 맨 끝

    Returns:
        column(넣은 열 문자), title을 포함한 JSON 문자열
    """
    tool = "google_sheets_add_column"
    try:
        spreadsheet_id = _check(spreadsheet_id, sheet_id)
        if _blank(title):
            raise _Invalid("열 제목이 비어 있어요")
        if len(str(title)) > 50_000:
            raise _Invalid("열 제목은 50,000자까지예요")
        client = get_http_client()
        sid, sheet_title, column_count = await _find_sheet(client, access_token, spreadsheet_id, sheet_id)
        response = await client.get(
            _values_url(spreadsheet_id, _a1_range("1:1", sheet_title)),
            headers=_headers(access_token),
            timeout=_TIMEOUT,
        )
        rows = _ok(response).get("values") or [[]]
        headers = _header_range(rows[0], column_count)
        if not _blank(before_column):
            index = _resolve(before_column, headers)
        elif all(h == "" for h in headers):
            raise _Invalid("머리글이 없는 시트는 넣을 위치를 골라 주세요")
        else:
            index = len(headers)
        dim = {"sheetId": sid, "dimension": "COLUMNS", "startIndex": index, "endIndex": index + 1}
        if index < column_count:
            request = {"insertDimension": {"range": dim, "inheritFromBefore": index > 0}}
        else:
            request = {"appendDimension": {"sheetId": sid, "dimension": "COLUMNS", "length": 1}}
        await _batch_update(client, access_token, spreadsheet_id, [request])
        try:
            await _put(client, access_token, spreadsheet_id, _a1_range(f"{_col(index)}1", sheet_title), [title],
                       "RAW")
        except Exception as e:
            try:  # 넣은 빈 열을 지워 되돌린다
                await _batch_update(client, access_token, spreadsheet_id, [{"deleteDimension": {"range": dim}}])
            except Exception:
                raise _Invalid(f"{e} — 빈 열이 남았을 수 있어요") from e
            raise
        return json.dumps({"success": True, "column": _col(index), "title": title}, ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))


async def google_sheets_format_range(access_token: str, spreadsheet_id: str, sheet_id: str, cell_range: str,
                                     number_format: str | None = None, number_pattern: str | None = None,
                                     background_color: str | None = None, text_color: str | None = None,
                                     bold: bool | None = None, italic: bool | None = None,
                                     strikethrough: bool | None = None, horizontal_alignment: str | None = None,
                                     vertical_alignment: str | None = None, wrap_strategy: str | None = None) -> str:
    """
    Google Sheets 범위의 숫자 형식·색·글자 모양·정렬을 바꿉니다. 채운 칸만 바꾸고 나머지 서식은 그대로 둡니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID
        sheet_id: 워크시트 ID 또는 제목
        cell_range: 범위(A1 표기, 예: A1:C10, A:A, 2:10)
        number_format: 숫자 형식(TEXT·NUMBER·PERCENT·CURRENCY·DATE·TIME·DATE_TIME·SCIENTIFIC). 비우면 그대로
        number_pattern: 숫자 형식 패턴(예: yyyy-mm-dd, #,##0). 숫자 형식을 고를 때만
        background_color: 배경색 #RRGGBB. 비우면 그대로
        text_color: 글자색 #RRGGBB. 비우면 그대로
        bold: 굵게(비움=그대로, true=켜기, false=끄기)
        italic: 기울임(비움=그대로, true=켜기, false=끄기)
        strikethrough: 취소선(비움=그대로, true=켜기, false=끄기)
        horizontal_alignment: 가로 정렬(LEFT·CENTER·RIGHT)
        vertical_alignment: 세로 정렬(TOP·MIDDLE·BOTTOM)
        wrap_strategy: 줄바꿈(OVERFLOW_CELL·CLIP·WRAP)

    Returns:
        range(서식을 바꾼 범위)를 포함한 JSON 문자열
    """
    tool = "google_sheets_format_range"
    try:
        spreadsheet_id = _check(spreadsheet_id, sheet_id)
        number_format = _choice(number_format, _NUMBER_FORMATS, "숫자 형식")
        aligns = {"horizontalAlignment": _choice(horizontal_alignment, _H_ALIGNS, "가로 정렬"),
                  "verticalAlignment": _choice(vertical_alignment, _V_ALIGNS, "세로 정렬"),
                  "wrapStrategy": _choice(wrap_strategy, _WRAPS, "줄바꿈")}
        if not _blank(number_pattern):
            if number_format is None:
                raise _Invalid("형식 패턴은 숫자 형식을 고를 때만 써요")
            if len(str(number_pattern).strip()) > 200:
                raise _Invalid("형식 패턴은 200자까지예요")
        fmt, mask = _cell_format(background_color=background_color, text_color=text_color, bold=bold,
                                 italic=italic, strikethrough=strikethrough)
        if number_format:
            fmt["numberFormat"] = {"type": number_format}
            if not _blank(number_pattern):
                fmt["numberFormat"]["pattern"] = str(number_pattern).strip()
            mask.append("userEnteredFormat.numberFormat")
        for key, value in aligns.items():
            if value:
                fmt[key] = value
                mask.append(f"userEnteredFormat.{key}")
        if not mask:
            raise _Invalid("바꿀 서식이 없어요")
        _grid_range(cell_range, 0)
        client = get_http_client()
        sid, title, _ = await _find_sheet(client, access_token, spreadsheet_id, sheet_id)
        await _batch_update(client, access_token, spreadsheet_id, [{"repeatCell": {
            "range": _grid_range(cell_range, sid), "cell": {"userEnteredFormat": fmt}, "fields": ",".join(mask)}}])
        return json.dumps({"success": True, "range": _a1_range(str(cell_range).strip(), title)}, ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))

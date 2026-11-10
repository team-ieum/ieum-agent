"""Google Sheets 열·범위 도구(ACTION 전용): 열 추가·범위 복사·정렬·서식·조건부 서식·데이터 유효성."""
import json
import re

from tools.google_sheets import (
    _TIMEOUT, _Invalid, _a1_range, _batch_update, _blank, _cell_format, _check, _col, _col_index, _fail,
    _find_sheet, _header_range, _headers, _ok, _put, _resolve, _tri, _values_url,
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
_RELATIVE_DATES = ("PAST_YEAR", "PAST_MONTH", "PAST_WEEK", "YESTERDAY", "TODAY", "TOMORROW")
# 조건 → 필요한 값 개수(카탈로그 choices 순서)
_NUMBER_CONDITIONS = {"NUMBER_GREATER": 1, "NUMBER_GREATER_THAN_EQ": 1, "NUMBER_LESS": 1, "NUMBER_LESS_THAN_EQ": 1,
                      "NUMBER_EQ": 1, "NUMBER_NOT_EQ": 1, "NUMBER_BETWEEN": 2, "NUMBER_NOT_BETWEEN": 2}
_CF_CONDITIONS = {**_NUMBER_CONDITIONS, "TEXT_CONTAINS": 1, "TEXT_NOT_CONTAINS": 1, "TEXT_EQ": 1,
                  "TEXT_STARTS_WITH": 1, "TEXT_ENDS_WITH": 1, "DATE_EQ": 1, "DATE_BEFORE": 1, "DATE_AFTER": 1,
                  "BLANK": 0, "NOT_BLANK": 0, "CUSTOM_FORMULA": 1}
_DV_CONDITIONS = {**_NUMBER_CONDITIONS, "TEXT_CONTAINS": 1, "TEXT_NOT_CONTAINS": 1, "TEXT_EQ": 1,
                  "TEXT_IS_EMAIL": 0, "TEXT_IS_URL": 0, "DATE_EQ": 1, "DATE_BEFORE": 1, "DATE_AFTER": 1,
                  "DATE_ON_OR_BEFORE": 1, "DATE_ON_OR_AFTER": 1, "DATE_BETWEEN": 2, "DATE_NOT_BETWEEN": 2,
                  "DATE_IS_VALID": 0, "ONE_OF_RANGE": 1, "ONE_OF_LIST": 0, "BOOLEAN": 0, "CUSTOM_FORMULA": 1}
_RELATIVE_DATE_CONDITIONS = ("DATE_EQ", "DATE_BEFORE", "DATE_AFTER")

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


def _condition(condition, allowed) -> str:
    condition = _choice(condition, allowed, "조건")
    if condition is None:
        raise _Invalid("조건을 골라 주세요")
    return condition


def _values_for(condition: str, count: int, value_1, value_2) -> list[str]:
    """조건이 쓰는 앞 count개 값. 비면 거절, 뒤에 남은 값은 무시."""
    picked = (value_1, value_2)[:count]
    if any(_blank(v) for v in picked):
        raise _Invalid(f"조건 {condition}은(는) 값 {count}개가 필요해요")
    values = [str(v).strip() for v in picked]
    if condition == "CUSTOM_FORMULA" and not values[0].startswith("="):
        raise _Invalid("맞춤 수식은 =로 시작해요")
    return values


async def google_sheets_add_conditional_format(access_token: str, spreadsheet_id: str, sheet_id: str,
                                               cell_range: str, condition: str, value_1: str | None = None,
                                               value_2: str | None = None, relative_date: str | None = None,
                                               background_color: str | None = None, text_color: str | None = None,
                                               bold: bool | None = None, italic: bool | None = None,
                                               strikethrough: bool | None = None) -> str:
    """
    Google Sheets 범위에 조건부 서식 규칙을 맨 앞에 추가합니다. 실행할 때마다 규칙이 하나씩 쌓입니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID
        sheet_id: 워크시트 ID 또는 제목
        cell_range: 범위(A1 표기, 예: A1:C10, A:A, 2:10)
        condition: 조건(NUMBER_GREATER·TEXT_CONTAINS·DATE_BEFORE·BLANK·CUSTOM_FORMULA 등)
        value_1: 비교할 값. 맞춤 수식은 =로 시작
        value_2: 두 번째 값(사이·사이 아님에서만)
        relative_date: 날짜 조건(DATE_EQ·DATE_BEFORE·DATE_AFTER)에서 값 대신 쓰는 상대 날짜
        background_color: 배경색 #RRGGBB
        text_color: 글자색 #RRGGBB
        bold: 굵게(비움=그대로, true=켜기, false=끄기)
        italic: 기울임(비움=그대로, true=켜기, false=끄기)
        strikethrough: 취소선(비움=그대로, true=켜기, false=끄기)

    Returns:
        range(규칙을 건 범위)를 포함한 JSON 문자열
    """
    tool = "google_sheets_add_conditional_format"
    try:
        spreadsheet_id = _check(spreadsheet_id, sheet_id)
        condition = _condition(condition, _CF_CONDITIONS)
        rd = _choice(relative_date, _RELATIVE_DATES, "상대 날짜") if condition in _RELATIVE_DATE_CONDITIONS else None
        if rd:
            values = [{"relativeDate": rd}]
        else:
            values = [{"userEnteredValue": v}
                      for v in _values_for(condition, _CF_CONDITIONS[condition], value_1, value_2)]
        fmt, mask = _cell_format(background_color=background_color, text_color=text_color, bold=bold,
                                 italic=italic, strikethrough=strikethrough)
        if not mask:
            raise _Invalid("바꿀 서식이 없어요")
        _grid_range(cell_range, 0)
        cond = {"type": condition, **({"values": values} if values else {})}
        client = get_http_client()
        sid, title, _ = await _find_sheet(client, access_token, spreadsheet_id, sheet_id)
        await _batch_update(client, access_token, spreadsheet_id, [{"addConditionalFormatRule": {
            "rule": {"ranges": [_grid_range(cell_range, sid)], "booleanRule": {"condition": cond, "format": fmt}},
            "index": 0}}])
        return json.dumps({"success": True, "range": _a1_range(str(cell_range).strip(), title)}, ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))


def _dv_values(condition: str, value_1, value_2, values) -> list[str]:
    if condition == "ONE_OF_LIST":
        if not isinstance(values, list) or not values:
            raise _Invalid("목록 항목이 1개 이상 필요해요")
        if len(values) > 500:
            raise _Invalid("목록 항목은 500개까지예요")
        for x in values:
            if not isinstance(x, str) or _blank(x) or x.strip().startswith("="):
                raise _Invalid(f"목록 항목은 =로 시작하지 않는 글자만 돼요: {x!r}")
        return list(values)
    if condition == "BOOLEAN":
        if _blank(value_1) and not _blank(value_2):
            raise _Invalid("체크 값을 먼저 채워 주세요")
        return [str(v).strip() for v in (value_1, value_2) if not _blank(v)]
    picked = _values_for(condition, _DV_CONDITIONS[condition], value_1, value_2)
    if condition == "ONE_OF_RANGE" and not picked[0].startswith("="):
        picked[0] = "=" + picked[0]
    return picked


async def google_sheets_set_data_validation(access_token: str, spreadsheet_id: str, sheet_id: str, cell_range: str,
                                            condition: str, value_1: str | None = None, value_2: str | None = None,
                                            values: list[str] | None = None, input_message: str | None = None,
                                            strict: bool = False, show_dropdown: bool = True) -> str:
    """
    Google Sheets 범위에 데이터 유효성(입력 규칙)을 겁니다. 범위에 있던 규칙은 이것으로 바뀝니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID
        sheet_id: 워크시트 ID 또는 제목
        cell_range: 범위(A1 표기, 예: A1:C10, A:A, 2:10)
        condition: 조건(NUMBER_BETWEEN·TEXT_IS_EMAIL·DATE_AFTER·ONE_OF_LIST·ONE_OF_RANGE·BOOLEAN 등)
        value_1: 비교할 값. ONE_OF_RANGE는 범위, CUSTOM_FORMULA는 =로 시작, BOOLEAN은 체크했을 때 값
        value_2: 두 번째 값(사이·사이 아님). BOOLEAN은 해제했을 때 값
        values: ONE_OF_LIST 목록 항목(최대 500개, =로 시작 불가)
        input_message: 셀을 고르면 보이는 안내 문구
        strict: 참이면 잘못된 입력을 거부, 거짓이면 경고만
        show_dropdown: 목록·범위 조건에서 셀에 드롭다운 표시

    Returns:
        range(규칙을 건 범위)를 포함한 JSON 문자열
    """
    tool = "google_sheets_set_data_validation"
    try:
        spreadsheet_id = _check(spreadsheet_id, sheet_id)
        condition = _condition(condition, _DV_CONDITIONS)
        picked = _dv_values(condition, value_1, value_2, values)
        rule = {"condition": {"type": condition, **({"values": [{"userEnteredValue": v} for v in picked]}
                                                    if picked else {})},
                "strict": _tri(strict) is True, "showCustomUi": _tri(show_dropdown) is not False}
        if not _blank(input_message):
            rule["inputMessage"] = str(input_message)
        _grid_range(cell_range, 0)
        client = get_http_client()
        sid, title, _ = await _find_sheet(client, access_token, spreadsheet_id, sheet_id)
        await _batch_update(client, access_token, spreadsheet_id,
                            [{"setDataValidation": {"range": _grid_range(cell_range, sid), "rule": rule}}])
        return json.dumps({"success": True, "range": _a1_range(str(cell_range).strip(), title)}, ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))

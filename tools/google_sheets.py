import json
import re
from typing import NamedTuple
from urllib.parse import quote

import httpx

from common.error_code import ToolErrorCode
from tools.http_client import get_http_client

_SHEETS_API_BASE = "https://sheets.googleapis.com/v4"
_TIMEOUT = 30.0


def _headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def _target_error(tool: str, spreadsheet_id: str, sheet_name: str | None) -> str | None:
    """빈 리소스 ID면 Google을 부르지 않고 에러 JSON을 반환한다.

    노드 설정(tools[].config)에 키가 있으면 빈 값도 그대로 고정 바인딩된다(미해결 참조식도 ""가 된다).
    여기서 끊어야 엉뚱한 대상에 요청이 나가지 않는다."""
    if spreadsheet_id is None or not str(spreadsheet_id).strip():
        detail = "spreadsheet_id가 비어 있습니다"
    elif sheet_name is not None and not str(sheet_name).strip():
        detail = "sheet_name이 비어 있습니다"
    else:
        return None
    return json.dumps({
        "error": f"{ToolErrorCode.EXECUTION_FAILED.message} ({tool}: {detail})"
    }, ensure_ascii=False)


def _a1_range(cell_range: str, sheet_name: str | None) -> str:
    """sheet_name이 있으면 cell_range의 시트 부분을 버리고 '<sheet_name>'!<범위>로 조합한다.

    워크시트를 노드 설정으로 고정해도 LLM이 cell_range에 'Sheet1!A:B'처럼 시트를 붙여 보낼 수 있다.
    고정값이 이겨야 하므로 마지막 '!' 뒤(범위)만 쓴다. 범위가 비면 시트 전체다."""
    if sheet_name is None:
        return cell_range
    sheet = "'" + str(sheet_name).replace("'", "''") + "'"
    part = str(cell_range or "").rpartition("!")[2]
    return f"{sheet}!{part}" if part else sheet


def _parse_values(tool: str, values) -> tuple[list | None, str | None]:
    """values는 JSON 배열 문자열이 기본이나, 프레임워크/LLM이 이미 list로 넘길 수도 있어 둘 다 허용한다.
    (파싱 결과, None) 또는 (None, 에러 JSON)을 반환한다."""
    if isinstance(values, list):
        return values, None
    try:
        return json.loads(values), None
    except (json.JSONDecodeError, TypeError) as e:
        return None, json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} ({tool}: values JSON 파싱 오류 - {str(e)})"
        }, ensure_ascii=False)


def _values_url(spreadsheet_id: str, a1_range: str) -> str:
    # 탭 이름의 '/', '#', '?'가 경로·프래그먼트·쿼리로 해석되지 않도록 경로 조각을 인코딩한다.
    return (f"{_SHEETS_API_BASE}/spreadsheets/{quote(str(spreadsheet_id), safe='')}"
            f"/values/{quote(a1_range, safe='')}")


async def google_sheets_read(
    access_token: str,
    spreadsheet_id: str,
    cell_range: str,
    sheet_name: str | None = None,
) -> str:
    """
    Google Sheets에서 지정 범위의 데이터를 읽습니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID (URL에서 추출)
        cell_range: 읽을 범위 (A1 표기법, 예: "Sheet1!A1:D10")
        sheet_name: 워크시트(탭) 제목. 지정하면 cell_range의 시트 부분은 무시되고 범위만 쓰인다

    Returns:
        범위, 값 목록을 포함한 JSON 문자열
    """
    error = _target_error("google_sheets_read", spreadsheet_id, sheet_name)
    if error:
        return error
    a1_range = _a1_range(cell_range, sheet_name)

    try:
        client = get_http_client()
        response = await client.get(
            _values_url(spreadsheet_id, a1_range),
            headers=_headers(access_token),
            timeout=_TIMEOUT,
        )
        data = response.json()
        if not response.is_success:
            return json.dumps({
                "error": f"Google Sheets API 오류 ({response.status_code}): {data.get('error', {}).get('message', '알 수 없는 오류')}"
            }, ensure_ascii=False)

        return json.dumps({
            "success": True,
            "range": data.get("range", a1_range),
            "values": data.get("values", []),
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (google_sheets_read: {str(e)})"
        }, ensure_ascii=False)


async def google_sheets_append(
    access_token: str,
    spreadsheet_id: str,
    cell_range: str,
    values: str,
    sheet_name: str | None = None,
) -> str:
    """
    Google Sheets의 기존 데이터 마지막 행 뒤에 새 행을 추가합니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID (URL에서 추출)
        cell_range: 추가 대상 범위 (A1 표기법, 예: "Sheet1!A:B")
        values: JSON 배열 문자열 (2차원, 예: '[["홍길동","100"]]')
        sheet_name: 워크시트(탭) 제목. 지정하면 cell_range의 시트 부분은 무시되고 범위만 쓰인다

    Returns:
        추가 결과를 포함한 JSON 문자열
    """
    error = _target_error("google_sheets_append", spreadsheet_id, sheet_name)
    if error:
        return error
    a1_range = _a1_range(cell_range, sheet_name)

    parsed_values, error = _parse_values("google_sheets_append", values)
    if error:
        return error

    payload = {
        "range": a1_range,
        "majorDimension": "ROWS",
        "values": parsed_values,
    }

    try:
        client = get_http_client()
        response = await client.post(
            f"{_values_url(spreadsheet_id, a1_range)}:append",
            headers=_headers(access_token),
            params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
            json=payload,
            timeout=_TIMEOUT,
        )
        data = response.json()
        if not response.is_success:
            return json.dumps({
                "error": f"Google Sheets API 오류 ({response.status_code}): {data.get('error', {}).get('message', '알 수 없는 오류')}"
            }, ensure_ascii=False)

        updates = data.get("updates", {})
        return json.dumps({
            "success": True,
            "updatedRange": updates.get("updatedRange", a1_range),
            "updatedRows": updates.get("updatedRows", 0),
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (google_sheets_append: {str(e)})"
        }, ensure_ascii=False)


async def google_sheets_write(
    access_token: str,
    spreadsheet_id: str,
    cell_range: str,
    values: str,
    sheet_name: str | None = None,
) -> str:
    """
    Google Sheets의 지정 범위에 데이터를 씁니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID (URL에서 추출)
        cell_range: 쓸 범위 (A1 표기법, 예: "Sheet1!A1")
        values: JSON 배열 문자열 (2차원, 예: '[["이름","점수"],["홍길동","100"]]')
        sheet_name: 워크시트(탭) 제목. 지정하면 cell_range의 시트 부분은 무시되고 범위만 쓰인다

    Returns:
        업데이트 결과를 포함한 JSON 문자열
    """
    error = _target_error("google_sheets_write", spreadsheet_id, sheet_name)
    if error:
        return error
    # 범위가 비면 시트 전체가 대상이 돼 A1부터 헤더·기존 데이터를 덮어쓴다(미해결 참조식도 ""가 된다).
    if not str(cell_range or "").rpartition("!")[2].strip():
        return json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (google_sheets_write: cell_range가 비어 있습니다)"
        }, ensure_ascii=False)
    a1_range = _a1_range(cell_range, sheet_name)

    parsed_values, error = _parse_values("google_sheets_write", values)
    if error:
        return error

    payload = {
        "range": a1_range,
        "majorDimension": "ROWS",
        "values": parsed_values,
    }

    try:
        client = get_http_client()
        response = await client.put(
            _values_url(spreadsheet_id, a1_range),
            headers=_headers(access_token),
            params={"valueInputOption": "USER_ENTERED"},
            json=payload,
            timeout=_TIMEOUT,
        )
        data = response.json()
        if not response.is_success:
            return json.dumps({
                "error": f"Google Sheets API 오류 ({response.status_code}): {data.get('error', {}).get('message', '알 수 없는 오류')}"
            }, ensure_ascii=False)

        return json.dumps({
            "success": True,
            "updatedRange": data.get("updatedRange", a1_range),
            "updatedCells": data.get("updatedCells", 0),
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (google_sheets_write: {str(e)})"
        }, ensure_ascii=False)


# --- 열별 칸 도구(append_top·update_row·lookup_row·find_or_create_row)와 공용 헬퍼 ---

class _Invalid(ValueError):
    """사용자에게 그대로 보일 실패 사유."""


class _Sheet(NamedTuple):
    id: int
    title: str
    headers: list[str]  # 열 범위 규칙을 적용한 머리글(길이 = 쓸 수 있는 열 수, 빈 머리글은 "")


_SPREADSHEET_ID = re.compile(r"[A-Za-z0-9_-]{1,128}", re.ASCII)
_VALUE_INPUT_OPTIONS = ("USER_ENTERED", "RAW")
_UNSET = object()


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _flag(v) -> bool:
    # "false" 문자열이 참이 되어 셀을 지우지 않도록 True·"true"만 참이다.
    return v is True or (isinstance(v, str) and v.lower() == "true")


def _fail(tool: str, detail: str) -> str:
    return json.dumps({"error": f"{ToolErrorCode.EXECUTION_FAILED.message} ({tool}: {detail})"},
                      ensure_ascii=False)


def _check(spreadsheet_id, sheet_id, *, columns=_UNSET, value_input_option=_UNSET, lookup=None) -> str:
    """Google을 부르기 전 대상·입력 검사. 미해결 참조식은 ""로 오므로 여기서 끊어야 엉뚱한 행을 찾거나 고치지 않는다.
    lookup = (찾을 열, 찾을 값, 보조 열, 보조 값). 앞뒤 공백을 뗀 spreadsheet_id를 돌려준다."""
    if value_input_option is not _UNSET and value_input_option not in _VALUE_INPUT_OPTIONS:
        raise _Invalid(f"value_input_option은 USER_ENTERED 또는 RAW만 돼요: {value_input_option!r}")
    if columns is not _UNSET:
        if not isinstance(columns, dict):
            raise _Invalid("columns는 {열: 값} 객체여야 해요")
        for key, value in columns.items():
            if value is not None and not isinstance(value, (str, int, float, bool)):
                raise _Invalid(f"열 '{key}' 값은 문자열·숫자·참거짓만 돼요")
    if lookup is not None:
        column, value, column_2, value_2 = lookup
        if _blank(column):
            raise _Invalid("찾을 열이 비어 있어요")
        if _blank(value):
            raise _Invalid("찾을 값이 비어 있어요")
        if not _blank(column_2) and _blank(value_2):
            raise _Invalid("보조 찾을 값이 비어 있어요")
    sid = "" if spreadsheet_id is None else str(spreadsheet_id).strip()
    if not _SPREADSHEET_ID.fullmatch(sid):
        raise _Invalid(f"spreadsheet_id가 올바르지 않아요: {spreadsheet_id!r}")
    if _blank(sheet_id):  # "0"·0은 첫 시트의 정상 id다
        raise _Invalid("sheet_id가 비어 있어요")
    return sid


def _row_index(row_number) -> int:
    if isinstance(row_number, bool):
        n = None
    elif isinstance(row_number, int):
        n = row_number
    elif isinstance(row_number, str) and row_number.strip().isascii() and row_number.strip().isdigit():
        n = int(row_number.strip())
    else:
        n = None
    if n is None or n < 1:
        raise _Invalid(f"행 번호가 올바르지 않아요: {row_number!r}")
    return n


def _ok(response) -> dict:
    try:
        data = response.json()
    except Exception:
        data = None
    if not response.is_success:
        err = data.get("error") if isinstance(data, dict) else None
        message = err.get("message") if isinstance(err, dict) else None
        raise _Invalid(f"Google Sheets API 오류 ({response.status_code}): {message or '알 수 없는 오류'}")
    return data if isinstance(data, dict) else {}


def _spreadsheet_url(spreadsheet_id: str) -> str:
    return f"{_SHEETS_API_BASE}/spreadsheets/{quote(str(spreadsheet_id), safe='')}"


async def _find_sheet(client, access_token: str, spreadsheet_id, sheet_id) -> tuple[int, str, int]:
    """(sheetId, 제목, columnCount). 정수 문자열이고 그 sheetId가 있으면 id, 아니면 제목 정확히 일치."""
    response = await client.get(
        _spreadsheet_url(spreadsheet_id),
        headers=_headers(access_token),
        params={"fields": "sheets.properties(sheetId,title,gridProperties.columnCount)"},
        timeout=_TIMEOUT,
    )
    sheets = [s.get("properties", {}) for s in _ok(response).get("sheets", [])]
    found = lambda p: (p.get("sheetId", 0), p.get("title", ""), p.get("gridProperties", {}).get("columnCount", 26))
    key = str(sheet_id).strip()
    if key.isascii() and key.isdigit():
        for p in sheets:
            if p.get("sheetId", 0) == int(key):  # Google은 기본값 0인 sheetId를 생략할 수 있다
                return found(p)
    for p in sheets:
        if p.get("title") == str(sheet_id):
            return found(p)
    listed = ", ".join(f"{p.get('title', '')}({p.get('sheetId', 0)})" for p in sheets)
    raise _Invalid(f"워크시트 '{sheet_id}'를 찾을 수 없어요. 있는 시트: {listed}")


def _blank_header(h: str) -> bool:
    # BE Java isBlank()와 같은 집합: NBSP·U+2007·U+202F는 글자, NEL(U+0085)도 Java는 공백으로 보지 않는다.
    return all(c.isspace() and c not in "\x85\xa0  " for c in h)


def _header_range(first_row: list, column_count: int) -> list[str]:
    """A부터 마지막으로 비어 있지 않은 머리글까지. 전부 비면 A부터 min(columnCount, 26)까지."""
    headers = ["" if _blank_header(str(h)) else str(h) for h in first_row]
    while headers and headers[-1] == "":
        headers.pop()
    return headers or [""] * min(column_count, 26)


def _col(i: int) -> str:
    """0-based 인덱스 → 열 문자(bijective base-26: 0 → A, 26 → AA)."""
    letters = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        letters = chr(65 + r) + letters
    return letters


def _col_index(letters: str) -> int:
    n = 0
    for c in letters:
        n = n * 26 + ord(c) - 64
    return n - 1


def _available(headers: list[str]) -> str:
    return "쓸 수 있는 열: " + ", ".join(f"{_col(i)}({h or '(제목 없음)'})" for i, h in enumerate(headers))


def _resolve(key, headers: list[str]) -> int:
    """열 키 → 열 인덱스. 머리글 범위 안의 대문자 열 문자가 먼저, 아니면 머리글 이름."""
    if key is None or str(key).strip() == "":
        raise _Invalid(f"열이 비어 있어요. {_available(headers)}")
    key = str(key)
    if re.fullmatch(r"[A-Z]{1,3}", key) and _col_index(key) < len(headers):
        return _col_index(key)
    # 채팅 빌더는 동적 칸 키의 '.'을 '%2E'로 바꿔 저장한다.
    for name in dict.fromkeys((key, key.replace("%2E", "."))):
        count = headers.count(name)
        if count == 1:
            return headers.index(name)
        if count > 1:
            raise _Invalid(f"같은 이름 머리글 '{name}'이 여러 개라 열 문자로 골라 주세요. {_available(headers)}")
    raise _Invalid(f"열 '{key}'를 찾을 수 없어요. {_available(headers)}")


def _row(columns, headers: list[str], *, blank) -> list:
    """{열 키: 값} → A부터 채운 마지막 열까지의 행. 안 채운 칸은 blank."""
    cells = {}
    for key, value in columns.items():
        i = _resolve(key, headers)
        if i in cells:
            raise _Invalid(f"열 {_col(i)}에 값이 두 번 왔어요. {_available(headers)}")
        cells[i] = value
    filled = {i: v for i, v in cells.items() if not _blank(v)}
    if not filled:
        raise _Invalid("넣을 값이 없어요")
    row = [blank] * (max(filled) + 1)
    for i, v in filled.items():
        row[i] = v
    return row


def _row_number(updated_range) -> int | None:
    m = re.search(r"[A-Z]+(\d+)", str(updated_range or "").rpartition("!")[2])
    return int(m.group(1)) if m else None


async def _open(client, access_token, spreadsheet_id, sheet_id) -> _Sheet:
    sid, title, column_count = await _find_sheet(client, access_token, spreadsheet_id, sheet_id)
    response = await client.get(
        _values_url(spreadsheet_id, _a1_range("1:1", title)),
        headers=_headers(access_token),
        timeout=_TIMEOUT,
    )
    rows = _ok(response).get("values") or [[]]
    return _Sheet(sid, title, _header_range(rows[0], column_count))


async def _put(client, access_token, spreadsheet_id, a1_range: str, row: list, value_input_option: str) -> dict:
    response = await client.put(
        _values_url(spreadsheet_id, a1_range),
        headers=_headers(access_token),
        params={"valueInputOption": value_input_option},
        json={"range": a1_range, "majorDimension": "ROWS", "values": [row]},
        timeout=_TIMEOUT,
    )
    return _ok(response)


async def _batch_update(client, access_token, spreadsheet_id, request: dict) -> None:
    response = await client.post(
        f"{_spreadsheet_url(spreadsheet_id)}:batchUpdate",
        headers=_headers(access_token),
        json={"requests": [request]},
        timeout=_TIMEOUT,
    )
    _ok(response)


async def _append(client, access_token, spreadsheet_id, title: str, row: list, value_input_option: str) -> dict:
    a1_range = _a1_range("A1", title)
    response = await client.post(
        f"{_values_url(spreadsheet_id, a1_range)}:append",
        headers=_headers(access_token),
        params={"valueInputOption": value_input_option, "insertDataOption": "INSERT_ROWS"},
        json={"range": a1_range, "majorDimension": "ROWS", "values": [row]},
        timeout=_TIMEOUT,
    )
    updated = _ok(response).get("updates", {}).get("updatedRange")
    return {"updatedRange": updated, "row_number": _row_number(updated)}


async def _lookup(client, access_token, spreadsheet_id, sheet_id, lookup_column, lookup_value,
                  lookup_column_2, lookup_value_2, from_bottom) -> tuple[_Sheet, int | None, dict]:
    """(시트, 찾은 행 번호 또는 None, {열 문자: 값}). 시트 전체를 한 번 읽고 첫 행을 머리글로 쓴다."""
    sid, title, column_count = await _find_sheet(client, access_token, spreadsheet_id, sheet_id)
    response = await client.get(
        _values_url(spreadsheet_id, _a1_range("", title)),
        headers=_headers(access_token),
        params={"valueRenderOption": "FORMATTED_VALUE"},
        timeout=_TIMEOUT,
    )
    rows = _ok(response).get("values") or [[]]
    sheet = _Sheet(sid, title, _header_range(rows[0], column_count))
    conditions = [(_resolve(lookup_column, sheet.headers), str(lookup_value))]
    if not _blank(lookup_column_2):
        conditions.append((_resolve(lookup_column_2, sheet.headers), str(lookup_value_2)))

    def cell(row, i):
        return str(row[i]) if i < len(row) else ""

    numbered = list(enumerate(rows[1:], start=2))
    if _flag(from_bottom):
        numbered.reverse()
    for n, row in numbered:
        if all(cell(row, i) == value for i, value in conditions):
            return sheet, n, {_col(i): cell(row, i) for i in range(len(sheet.headers))}
    return sheet, None, {}


async def google_sheets_append_top(access_token: str, spreadsheet_id: str, sheet_id: str, columns: dict,
                                   value_input_option: str = "USER_ENTERED") -> str:
    """
    Google Sheets 워크시트 맨 위(머리글 바로 아래 2행)에 새 행을 넣습니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID
        sheet_id: 워크시트 ID 또는 제목
        columns: {열: 값}. 열은 머리글 이름 또는 열 문자(A, B…)
        value_input_option: USER_ENTERED(수식·숫자 해석) 또는 RAW(그대로)
    """
    tool = "google_sheets_append_top"
    try:
        spreadsheet_id = _check(spreadsheet_id, sheet_id, columns=columns, value_input_option=value_input_option)
        client = get_http_client()
        sheet = await _open(client, access_token, spreadsheet_id, sheet_id)
        row = _row(columns, sheet.headers, blank="")
        dim = {"sheetId": sheet.id, "dimension": "ROWS", "startIndex": 1, "endIndex": 2}
        await _batch_update(client, access_token, spreadsheet_id,
                            {"insertDimension": {"range": dim, "inheritFromBefore": False}})
        a1_range = _a1_range("A2", sheet.title)
        try:
            data = await _put(client, access_token, spreadsheet_id, a1_range, row, value_input_option)
        except Exception as e:
            try:  # 넣은 빈 행을 지워 되돌린다
                await _batch_update(client, access_token, spreadsheet_id, {"deleteDimension": {"range": dim}})
            except Exception:
                raise _Invalid(f"{e} — 2행에 빈 행이 남았을 수 있어요") from e
            raise
        updated = data.get("updatedRange", a1_range)
        return json.dumps({"success": True, "updatedRange": updated, "row_number": _row_number(updated)},
                          ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))


async def google_sheets_update_row(access_token: str, spreadsheet_id: str, sheet_id: str, row_number: int,
                                   columns: dict, overwrite_all: bool = False,
                                   value_input_option: str = "USER_ENTERED") -> str:
    """
    Google Sheets 워크시트의 지정한 행을 고칩니다. 안 채운 열은 기존 값을 그대로 둡니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID
        sheet_id: 워크시트 ID 또는 제목
        row_number: 고칠 행 번호(1부터)
        columns: {열: 값}. 열은 머리글 이름 또는 열 문자(A, B…)
        overwrite_all: 참이면 안 채운 열을 머리글 끝까지 비운다
        value_input_option: USER_ENTERED(수식·숫자 해석) 또는 RAW(그대로)
    """
    tool = "google_sheets_update_row"
    try:
        spreadsheet_id = _check(spreadsheet_id, sheet_id, columns=columns, value_input_option=value_input_option)
        n = _row_index(row_number)
        client = get_http_client()
        sheet = await _open(client, access_token, spreadsheet_id, sheet_id)
        if _flag(overwrite_all):
            row = _row(columns, sheet.headers, blank="")
            row += [""] * (len(sheet.headers) - len(row))
        else:
            row = _row(columns, sheet.headers, blank=None)  # None = 셀 유지
        a1_range = _a1_range(f"A{n}", sheet.title)
        data = await _put(client, access_token, spreadsheet_id, a1_range, row, value_input_option)
        return json.dumps({"success": True, "updatedRange": data.get("updatedRange", a1_range), "row_number": n},
                          ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))


async def google_sheets_lookup_row(access_token: str, spreadsheet_id: str, sheet_id: str, lookup_column: str,
                                   lookup_value: str, lookup_column_2: str | None = None,
                                   lookup_value_2: str | None = None, from_bottom: bool = False) -> str:
    """
    Google Sheets 워크시트에서 열 값이 정확히 일치하는(대소문자 구분) 첫 행을 찾습니다. 1행(머리글)은 찾지 않습니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID
        sheet_id: 워크시트 ID 또는 제목
        lookup_column: 찾을 열(머리글 이름 또는 열 문자)
        lookup_value: 찾을 값
        lookup_column_2: 보조로 함께 맞춰 볼 열(머리글 이름 또는 열 문자)
        lookup_value_2: 보조 열에서 찾을 값
        from_bottom: 참이면 마지막 행부터 찾는다

    Returns:
        found, row_number, values({열 문자: 값})를 포함한 JSON 문자열
    """
    tool = "google_sheets_lookup_row"
    try:
        spreadsheet_id = _check(spreadsheet_id, sheet_id,
                                lookup=(lookup_column, lookup_value, lookup_column_2, lookup_value_2))
        client = get_http_client()
        _, n, values = await _lookup(client, access_token, spreadsheet_id, sheet_id, lookup_column, lookup_value,
                                     lookup_column_2, lookup_value_2, from_bottom)
        items = [] if n is None else [{"row_number": n, "values": values}]
        return json.dumps({"success": True, "found": n is not None, "total": len(items), "items": items,
                           "row_number": n, "values": values}, ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))


async def google_sheets_find_or_create_row(access_token: str, spreadsheet_id: str, sheet_id: str,
                                           lookup_column: str, lookup_value: str,
                                           lookup_column_2: str | None = None, lookup_value_2: str | None = None,
                                           from_bottom: bool = False, columns: dict | None = None,
                                           value_input_option: str = "USER_ENTERED") -> str:
    """
    Google Sheets 워크시트에서 행을 찾고, 없으면 표 끝에 새 행을 추가합니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID
        sheet_id: 워크시트 ID 또는 제목
        lookup_column: 찾을 열(머리글 이름 또는 열 문자)
        lookup_value: 찾을 값
        lookup_column_2: 보조로 함께 맞춰 볼 열(머리글 이름 또는 열 문자)
        lookup_value_2: 보조 열에서 찾을 값
        from_bottom: 참이면 마지막 행부터 찾는다
        columns: 없을 때 넣을 {열: 값}. 열은 머리글 이름 또는 열 문자(A, B…)
        value_input_option: USER_ENTERED(수식·숫자 해석) 또는 RAW(그대로)
    """
    tool = "google_sheets_find_or_create_row"
    try:
        columns = {} if columns is None else columns
        spreadsheet_id = _check(spreadsheet_id, sheet_id, columns=columns, value_input_option=value_input_option,
                                lookup=(lookup_column, lookup_value, lookup_column_2, lookup_value_2))
        client = get_http_client()
        sheet, n, values = await _lookup(client, access_token, spreadsheet_id, sheet_id, lookup_column,
                                         lookup_value, lookup_column_2, lookup_value_2, from_bottom)
        if n is not None:
            return json.dumps({"success": True, "created": False, "row_number": n, "values": values},
                              ensure_ascii=False)
        row = _row(columns, sheet.headers, blank="")
        appended = await _append(client, access_token, spreadsheet_id, sheet.title, row, value_input_option)
        values = {_col(i): row[i] if i < len(row) else "" for i in range(len(sheet.headers))}
        return json.dumps({"success": True, "created": True, "row_number": appended["row_number"],
                           "values": values}, ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))

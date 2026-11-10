"""Google Sheets 여러 행 쓰기 도구(추가·수정·비우기·삭제·서식). 공용 헬퍼는 tools.google_sheets에서 가져온다."""
import json
import re

from tools.http_client import get_http_client
from tools.google_sheets import (_Invalid, _a1_range, _batch_update, _blank, _check, _col, _fail, _find_sheet,
                                 _flag, _headers, _ok, _open, _row, _row_index, _row_number, _spreadsheet_url,
                                 _values_url, _TIMEOUT)

_APPEND_LIMIT = 1000
_UPDATE_LIMIT = 500
_SPAN_LIMIT = 1000
_CODE_FENCE = re.compile(r"```[A-Za-z]*")
_SCALAR = (str, int, float, bool)


def _parse_rows(rows, limit: int) -> list:
    """행 목록. AI 출력은 JSON 문자열에 코드 펜스·설명이 붙어 오기 쉽다(tools/github.py _parse_comments와 같은 방식)."""
    if isinstance(rows, list):
        items = rows
    elif _blank(rows):
        raise _Invalid("행 목록이 비어 있어요")
    elif isinstance(rows, str):
        # RecursionError: `[`를 깊게 중첩한 입력.
        try:
            items = json.loads(rows)
        except (json.JSONDecodeError, RecursionError):
            text = _CODE_FENCE.sub("", rows)
            start, end = text.find("["), text.rfind("]")
            try:
                items = json.loads(text[start:end + 1]) if 0 <= start < end else None
            except (json.JSONDecodeError, RecursionError):
                items = None
    else:
        items = None
    if not isinstance(items, list):
        raise _Invalid("행 목록은 JSON 배열이어야 해요")
    if len(items) > limit:
        raise _Invalid(f"행은 한 번에 {limit}개까지예요: {len(items)}개")
    return items


def _mapping(column_map) -> dict:
    """{열 키: 항목 키 이름}. 값이 빈 열은 버린다 — 하나도 안 남으면 자동 해석."""
    if column_map is None:
        return {}
    if not isinstance(column_map, dict):
        raise _Invalid("열별 항목 키는 {열: 항목 키} 객체여야 해요")
    mapping = {}
    for col, key in column_map.items():
        if _blank(key):
            continue
        if not isinstance(key, str):
            raise _Invalid(f"열별 항목 키 '{col}' 값은 글자여야 해요")
        mapping[col] = key.strip()
    return mapping


def _pick(item, path: str):
    """항목에서 키를 꺼낸다. 정확히 같은 키가 먼저, 없으면 '.' 경로(dict 키·list 숫자 인덱스). 없으면 None."""
    if isinstance(item, dict) and path in item:
        return item[path]
    cur = item
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isascii() and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def _item_columns(item, mapping: dict) -> dict:
    """항목 → {열 키: 값}. 매핑이 있으면 매핑한 열만, 없으면 찾기 결과(values)·평평한 객체·배열(A부터)."""
    if mapping:
        if not isinstance(item, (dict, list)):
            raise _Invalid("항목이 객체가 아니에요")
        cols = {col: _pick(item, key) for col, key in mapping.items()}
    elif isinstance(item, dict):
        cols = item["values"] if isinstance(item.get("values"), dict) else item
    elif isinstance(item, list):
        cols = {_col(i): v for i, v in enumerate(item)}
    else:
        raise _Invalid("항목이 객체나 배열이 아니에요")
    for k, v in cols.items():
        if v is not None and not isinstance(v, _SCALAR):
            raise _Invalid(f"열 '{k}' 값은 문자열·숫자·참거짓만 돼요")
    return cols


def _each(items, fn) -> list:
    """항목마다 fn. 실패는 'N번째 행:'을 붙여 — 한 행이라도 틀리면 아무것도 쓰지 않는다."""
    out = []
    for n, item in enumerate(items, start=1):
        try:
            out.append(fn(item))
        except _Invalid as e:
            raise _Invalid(f"{n}번째 행: {e}") from None
    return out


async def google_sheets_append_rows(access_token: str, spreadsheet_id: str, sheet_id: str, rows: str,
                                    column_map: dict | None = None,
                                    value_input_option: str = "USER_ENTERED") -> str:
    """
    Google Sheets 워크시트 표 끝에 여러 행을 한 번에 추가합니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID
        sheet_id: 워크시트 ID 또는 제목
        rows: 행 목록(JSON 배열). 항목은 {열: 값} 객체(열은 머리글 이름 또는 열 문자) 또는 A부터 채울 값 배열
        column_map: {열: 항목 키 이름}. 있으면 매핑한 열만 항목에서 꺼내 쓴다
        value_input_option: USER_ENTERED(수식·숫자 해석) 또는 RAW(그대로)

    Returns:
        updatedRange, row_number(첫 행), row_count를 포함한 JSON 문자열
    """
    tool = "google_sheets_append_rows"
    value_input_option = value_input_option or "USER_ENTERED"  # 빈 칸 = 카탈로그 기본값
    try:
        spreadsheet_id = _check(spreadsheet_id, sheet_id, value_input_option=value_input_option)
        mapping = _mapping(column_map)
        items = _parse_rows(rows, _APPEND_LIMIT)
        cols = _each(items, lambda item: _item_columns(item, mapping))
        if not cols:
            return json.dumps({"success": True, "updatedRange": None, "row_number": None, "row_count": 0},
                              ensure_ascii=False)
        client = get_http_client()
        sheet = await _open(client, access_token, spreadsheet_id, sheet_id)
        values = _each(cols, lambda c: _row(c, sheet.headers, blank=""))
        a1_range = _a1_range("A1", sheet.title)
        response = await client.post(
            f"{_values_url(spreadsheet_id, a1_range)}:append",
            headers=_headers(access_token),
            params={"valueInputOption": value_input_option, "insertDataOption": "INSERT_ROWS"},
            json={"range": a1_range, "majorDimension": "ROWS", "values": values},
            timeout=_TIMEOUT,
        )
        updates = _ok(response).get("updates", {})
        updated = updates.get("updatedRange")
        return json.dumps({"success": True, "updatedRange": updated, "row_number": _row_number(updated),
                           "row_count": updates.get("updatedRows", len(values))}, ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))


def _numbered(item, mapping: dict) -> tuple[int, dict]:
    """수정 항목 → (행 번호, {열 키: 값}). 행 번호는 항목 최상위 row_number(매핑 유무 무관)."""
    if not isinstance(item, dict) or "row_number" not in item:
        raise _Invalid("row_number가 필요해요")
    n = _row_index(item["row_number"])
    if not mapping and not isinstance(item.get("values"), dict):
        item = {k: v for k, v in item.items() if k != "row_number"}  # 평평한 객체의 row_number는 열 값이 아니다
    return n, _item_columns(item, mapping)


async def google_sheets_update_rows(access_token: str, spreadsheet_id: str, sheet_id: str, rows: str,
                                    column_map: dict | None = None, overwrite_all: bool = False,
                                    value_input_option: str = "USER_ENTERED") -> str:
    """
    Google Sheets 워크시트의 여러 행을 행 번호마다 한 번에 고칩니다. 안 채운 열은 기존 값을 그대로 둡니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID
        sheet_id: 워크시트 ID 또는 제목
        rows: 행 목록(JSON 배열). 항목마다 row_number와 바꿀 {열: 값}
        column_map: {열: 항목 키 이름}. 있으면 매핑한 열만 항목에서 꺼내 쓴다
        overwrite_all: 참이면 안 채운 열을 머리글 끝까지 비운다
        value_input_option: USER_ENTERED(수식·숫자 해석) 또는 RAW(그대로)
    """
    tool = "google_sheets_update_rows"
    value_input_option = value_input_option or "USER_ENTERED"  # 빈 칸 = 카탈로그 기본값
    try:
        spreadsheet_id = _check(spreadsheet_id, sheet_id, value_input_option=value_input_option)
        mapping = _mapping(column_map)
        items = _parse_rows(rows, _UPDATE_LIMIT)
        seen = set()

        def numbered(item):
            n, cols = _numbered(item, mapping)
            if n in seen:
                raise _Invalid(f"{n}행이 두 번 왔어요")
            seen.add(n)
            return n, cols

        targets = _each(items, numbered)
        if not targets:
            return json.dumps({"success": True, "row_count": 0, "row_numbers": []}, ensure_ascii=False)
        client = get_http_client()
        sheet = await _open(client, access_token, spreadsheet_id, sheet_id)
        overwrite = _flag(overwrite_all)

        def row(target):
            n, cols = target
            if overwrite:
                values = _row(cols, sheet.headers, blank="")
                values += [""] * (len(sheet.headers) - len(values))
            else:
                values = _row(cols, sheet.headers, blank=None)  # None = 셀 유지
            return {"range": _a1_range(f"A{n}", sheet.title), "majorDimension": "ROWS", "values": [values]}

        data = _each(targets, row)
        response = await client.post(
            f"{_spreadsheet_url(spreadsheet_id)}/values:batchUpdate",
            headers=_headers(access_token),
            json={"valueInputOption": value_input_option, "data": data},
            timeout=_TIMEOUT,
        )
        return json.dumps({"success": True, "row_count": _ok(response).get("totalUpdatedRows", len(data)),
                           "row_numbers": sorted(seen)}, ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))


# ASCII 숫자만 — \d는 전각 숫자를 받는다.
_ONE = re.compile(r"\s*([0-9]+)\s*")
_SPAN = re.compile(r"\s*([0-9]+)\s*-\s*([0-9]+)\s*")


def _span(token) -> tuple[int, int]:
    s = e = None
    if isinstance(token, dict) and "row_number" in token:  # '시트에서 여러 행 찾기' 결과 항목
        return _span(token["row_number"])
    if isinstance(token, int) and not isinstance(token, bool):
        s = e = token
    elif isinstance(token, str):
        if m := _ONE.fullmatch(token):
            s = e = int(m.group(1))
        elif m := _SPAN.fullmatch(token):
            s, e = int(m.group(1)), int(m.group(2))
    if s is None or s < 1 or s > e:
        raise _Invalid(f"행 번호가 올바르지 않아요: {token!r}")
    return s, e


def _parse_row_numbers(value, limit: int = _SPAN_LIMIT) -> list[tuple[int, int]]:
    """'5' / '5,7' / '5-9' / JSON 배열(숫자·'5-9'·찾기 결과 항목) → 병합한 구간(1부터, 끝 포함, 오름차순).
    상한은 구간 길이 합으로 잰다 — '2-99999999'를 펼치지 않는다."""
    if _blank(value):
        raise _Invalid("행 번호가 비어 있어요")
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, RecursionError):
            raise _Invalid(f"행 번호가 올바르지 않아요: {value!r}") from None
    if isinstance(value, list):
        tokens = value
    elif isinstance(value, str):
        tokens = value.split(",")
    else:
        tokens = [value]
    merged = []
    for s, e in sorted(_span(t) for t in tokens):
        if merged and merged[-1][1] + 1 >= s:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    if sum(e - s + 1 for s, e in merged) > limit:
        raise _Invalid(f"행은 한 번에 {limit}개까지예요")
    return merged


def _expand(spans) -> list[int]:
    return [n for s, e in spans for n in range(s, e + 1)]


def _done(spans) -> str:
    rows = _expand(spans)
    return json.dumps({"success": True, "row_count": len(rows), "row_numbers": rows}, ensure_ascii=False)


async def google_sheets_clear_rows(access_token: str, spreadsheet_id: str, sheet_id: str, row_numbers: str) -> str:
    """
    Google Sheets 워크시트 행의 값만 지웁니다. 서식과 행은 남습니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID
        sheet_id: 워크시트 ID 또는 제목
        row_numbers: 행 번호(예: 5 / 5,7 / 5-9 또는 찾기 결과 목록)
    """
    tool = "google_sheets_clear_rows"
    try:
        spreadsheet_id = _check(spreadsheet_id, sheet_id)
        spans = _parse_row_numbers(row_numbers)
        if not spans:
            return _done(spans)
        client = get_http_client()
        _, title, _ = await _find_sheet(client, access_token, spreadsheet_id, sheet_id)
        response = await client.post(
            f"{_spreadsheet_url(spreadsheet_id)}/values:batchClear",
            headers=_headers(access_token),
            json={"ranges": [_a1_range(f"{s}:{e}", title) for s, e in spans]},
            timeout=_TIMEOUT,
        )
        _ok(response)
        return _done(spans)
    except Exception as e:
        return _fail(tool, str(e))


async def google_sheets_delete_rows(access_token: str, spreadsheet_id: str, sheet_id: str, row_numbers: str,
                                    confirm_delete: bool) -> str:
    """
    Google Sheets 워크시트 행을 지우고 아래 행을 당깁니다. 되돌릴 수 없습니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID
        sheet_id: 워크시트 ID 또는 제목
        row_numbers: 행 번호(예: 5 / 5,7 / 5-9 또는 찾기 결과 목록)
        confirm_delete: 참이어야 실행한다
    """
    tool = "google_sheets_delete_rows"
    try:
        if not _flag(confirm_delete):
            raise _Invalid("삭제 확인을 켜야 행을 지워요")
        spreadsheet_id = _check(spreadsheet_id, sheet_id)
        spans = _parse_row_numbers(row_numbers)
        if not spans:
            return _done(spans)
        client = get_http_client()
        sheet_num, _, _ = await _find_sheet(client, access_token, spreadsheet_id, sheet_id)
        # 아래 구간부터 지워야 앞 구간의 행 번호가 밀리지 않는다.
        await _batch_update(client, access_token, spreadsheet_id, [
            {"deleteDimension": {"range": {"sheetId": sheet_num, "dimension": "ROWS", "startIndex": s - 1,
                                           "endIndex": e}}}
            for s, e in reversed(spans)])
        return _done(spans)
    except Exception as e:
        return _fail(tool, str(e))

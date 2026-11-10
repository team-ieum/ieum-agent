"""Google Sheets 여러 행 쓰기 도구(추가·수정·비우기·삭제·서식). 공용 헬퍼는 tools.google_sheets에서 가져온다."""
import json
import re

from tools.http_client import get_http_client
from tools.google_sheets import (_Invalid, _a1_range, _blank, _check, _col, _fail, _headers, _ok, _open, _row,
                                 _row_number, _values_url, _TIMEOUT)

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

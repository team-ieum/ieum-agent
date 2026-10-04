import json
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
    part = (cell_range or "").rpartition("!")[2]
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
    if not (cell_range or "").rpartition("!")[2].strip():
        return json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (google_sheets_write: "
                     "쓰기 범위(cell_range)가 비어 있습니다. 덮어쓸 위치를 'A1'처럼 지정해 주세요.)"
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

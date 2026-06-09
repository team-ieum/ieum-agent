import json

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


async def google_sheets_read(
    access_token: str,
    spreadsheet_id: str,
    cell_range: str,
) -> str:
    """
    Google Sheets에서 지정 범위의 데이터를 읽습니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID (URL에서 추출)
        cell_range: 읽을 범위 (A1 표기법, 예: "Sheet1!A1:D10")

    Returns:
        범위, 값 목록을 포함한 JSON 문자열
    """
    try:
        client = get_http_client()
        response = await client.get(
            f"{_SHEETS_API_BASE}/spreadsheets/{spreadsheet_id}/values/{cell_range}",
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
            "range": data.get("range", cell_range),
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
) -> str:
    """
    Google Sheets의 기존 데이터 마지막 행 뒤에 새 행을 추가합니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID (URL에서 추출)
        cell_range: 추가 대상 범위 (A1 표기법, 예: "Sheet1!A:B")
        values: JSON 배열 문자열 (2차원, 예: '[["홍길동","100"]]')

    Returns:
        추가 결과를 포함한 JSON 문자열
    """
    try:
        parsed_values = json.loads(values)
    except json.JSONDecodeError as e:
        return json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (google_sheets_append: values JSON 파싱 오류 - {str(e)})"
        }, ensure_ascii=False)

    payload = {
        "range": cell_range,
        "majorDimension": "ROWS",
        "values": parsed_values,
    }

    try:
        client = get_http_client()
        response = await client.post(
            f"{_SHEETS_API_BASE}/spreadsheets/{spreadsheet_id}/values/{cell_range}:append",
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
            "updatedRange": updates.get("updatedRange", cell_range),
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
) -> str:
    """
    Google Sheets의 지정 범위에 데이터를 씁니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID (URL에서 추출)
        cell_range: 쓸 범위 (A1 표기법, 예: "Sheet1!A1")
        values: JSON 배열 문자열 (2차원, 예: '[["이름","점수"],["홍길동","100"]]')

    Returns:
        업데이트 결과를 포함한 JSON 문자열
    """
    try:
        parsed_values = json.loads(values)
    except json.JSONDecodeError as e:
        return json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (google_sheets_write: values JSON 파싱 오류 - {str(e)})"
        }, ensure_ascii=False)

    payload = {
        "range": cell_range,
        "majorDimension": "ROWS",
        "values": parsed_values,
    }

    try:
        client = get_http_client()
        response = await client.put(
            f"{_SHEETS_API_BASE}/spreadsheets/{spreadsheet_id}/values/{cell_range}",
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
            "updatedRange": data.get("updatedRange", cell_range),
            "updatedCells": data.get("updatedCells", 0),
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (google_sheets_write: {str(e)})"
        }, ensure_ascii=False)

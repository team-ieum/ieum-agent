import json

import httpx

from common.error_code import ToolErrorCode

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
    range: str,
) -> str:
    """
    Google Sheets에서 지정 범위의 데이터를 읽습니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID (URL에서 추출)
        range: 읽을 범위 (A1 표기법, 예: "Sheet1!A1:D10")

    Returns:
        범위, 값 목록을 포함한 JSON 문자열
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(
                f"{_SHEETS_API_BASE}/spreadsheets/{spreadsheet_id}/values/{range}",
                headers=_headers(access_token),
            )
        data = response.json()
        if response.status_code != 200:
            return json.dumps({
                "error": f"Google Sheets API 오류 ({response.status_code}): {data.get('error', {}).get('message', '알 수 없는 오류')}"
            }, ensure_ascii=False)

        return json.dumps({
            "success": True,
            "range": data.get("range", range),
            "values": data.get("values", []),
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (google_sheets_read: {str(e)})"
        }, ensure_ascii=False)


async def google_sheets_write(
    access_token: str,
    spreadsheet_id: str,
    range: str,
    values: str,
) -> str:
    """
    Google Sheets의 지정 범위에 데이터를 씁니다.

    Args:
        access_token: Google OAuth Access Token
        spreadsheet_id: 스프레드시트 ID (URL에서 추출)
        range: 쓸 범위 (A1 표기법, 예: "Sheet1!A1")
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
        "range": range,
        "majorDimension": "ROWS",
        "values": parsed_values,
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.put(
                f"{_SHEETS_API_BASE}/spreadsheets/{spreadsheet_id}/values/{range}",
                headers=_headers(access_token),
                params={"valueInputOption": "USER_ENTERED"},
                json=payload,
            )
        data = response.json()
        if response.status_code != 200:
            return json.dumps({
                "error": f"Google Sheets API 오류 ({response.status_code}): {data.get('error', {}).get('message', '알 수 없는 오류')}"
            }, ensure_ascii=False)

        return json.dumps({
            "success": True,
            "updatedRange": data.get("updatedRange", range),
            "updatedCells": data.get("updatedCells", 0),
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (google_sheets_write: {str(e)})"
        }, ensure_ascii=False)

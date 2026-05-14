import json

import httpx

from common.error_code import ToolErrorCode

_DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
_DRIVE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"
_TIMEOUT = 30.0
_MAX_CONTENT_BYTES = 1 * 1024 * 1024

_EXPORT_MIME_MAP = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
}


def _headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
    }


async def google_drive_read(
    access_token: str,
    file_id: str,
) -> str:
    """
    Google Drive 파일의 내용을 읽습니다.

    Args:
        access_token: Google OAuth Access Token
        file_id: Google Drive 파일 ID

    Returns:
        파일 이름, MIME 타입, 내용을 포함한 JSON 문자열
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            # 메타데이터 조회
            meta_response = await client.get(
                f"{_DRIVE_API_BASE}/files/{file_id}",
                headers=_headers(access_token),
                params={"fields": "name,mimeType"},
            )
            if meta_response.status_code != 200:
                data = meta_response.json()
                return json.dumps({
                    "error": f"Google Drive API 오류 ({meta_response.status_code}): {data.get('error', {}).get('message', '알 수 없는 오류')}"
                }, ensure_ascii=False)

            meta = meta_response.json()
            name = meta.get("name", "")
            mime_type = meta.get("mimeType", "")

            # MIME 타입별 콘텐츠 가져오기
            export_mime = _EXPORT_MIME_MAP.get(mime_type)
            if export_mime:
                content_response = await client.get(
                    f"{_DRIVE_API_BASE}/files/{file_id}/export",
                    headers=_headers(access_token),
                    params={"mimeType": export_mime},
                )
            else:
                content_response = await client.get(
                    f"{_DRIVE_API_BASE}/files/{file_id}",
                    headers=_headers(access_token),
                    params={"alt": "media"},
                )

            if content_response.status_code != 200:
                return json.dumps({
                    "error": f"Google Drive 파일 읽기 실패 ({content_response.status_code})"
                }, ensure_ascii=False)

            raw = content_response.content
            if len(raw) > _MAX_CONTENT_BYTES:
                raw = raw[:_MAX_CONTENT_BYTES]

            content = raw.decode("utf-8", errors="replace")

        return json.dumps({
            "success": True,
            "fileId": file_id,
            "name": name,
            "mimeType": export_mime or mime_type,
            "content": content,
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (google_drive_read: {str(e)})"
        }, ensure_ascii=False)


async def google_drive_upload(
    access_token: str,
    name: str,
    content: str,
    mime_type: str = "text/plain",
    folder_id: str = "",
) -> str:
    """
    Google Drive에 텍스트 파일을 업로드합니다.

    Args:
        access_token: Google OAuth Access Token
        name: 업로드할 파일 이름
        content: 파일 내용 (텍스트)
        mime_type: MIME 타입 (기본값: "text/plain")
        folder_id: 폴더 ID (없으면 루트)

    Returns:
        업로드된 파일 ID, URL을 포함한 JSON 문자열
    """
    metadata = {"name": name}
    if folder_id:
        metadata["parents"] = [folder_id]

    boundary = "ieum_multipart_boundary"
    body_parts = [
        f"--{boundary}",
        "Content-Type: application/json; charset=UTF-8",
        "",
        json.dumps(metadata, ensure_ascii=False),
        f"--{boundary}",
        f"Content-Type: {mime_type}",
        "",
        content,
        f"--{boundary}--",
    ]
    body = "\r\n".join(body_parts)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{_DRIVE_UPLOAD_BASE}/files",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": f"multipart/related; boundary={boundary}",
                },
                params={"uploadType": "multipart", "fields": "id,name,webViewLink"},
                content=body.encode("utf-8"),
            )
        data = response.json()
        if response.status_code != 200:
            return json.dumps({
                "error": f"Google Drive API 오류 ({response.status_code}): {data.get('error', {}).get('message', '알 수 없는 오류')}"
            }, ensure_ascii=False)

        return json.dumps({
            "success": True,
            "fileId": data.get("id", ""),
            "name": data.get("name", ""),
            "webViewLink": data.get("webViewLink", ""),
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (google_drive_upload: {str(e)})"
        }, ensure_ascii=False)

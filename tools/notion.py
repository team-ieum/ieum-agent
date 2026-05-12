import json
import httpx
from common.error_code import ErrorCode

_NOTION_API_BASE = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"
_TIMEOUT = 30.0


async def notion_create_page(
    token: str,
    parent_page_id: str,
    title: str,
    content: str,
) -> str:
    """
    Notion에 새 페이지를 생성합니다.

    Args:
        token: Notion Integration Token (secret_xxx 형태)
        parent_page_id: 부모 페이지 ID
        title: 생성할 페이지 제목
        content: 페이지 본문 텍스트

    Returns:
        생성된 페이지 URL 또는 에러 메시지 JSON 문자열
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": _NOTION_VERSION,
    }

    def split_content(text: str, chunk_size: int = 2000) -> list[str]:
        return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]

    children = []
    for chunk in split_content(content):
        children.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": chunk}}]
            }
        })

    payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "properties": {
            "title": {"title": [{"type": "text", "text": {"content": title}}]}
        },
        "children": children,
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{_NOTION_API_BASE}/pages",
                headers=headers,
                json=payload,
            )

        data = response.json()

        if response.status_code != 200:
            message = data.get("message", "알 수 없는 오류")
            return json.dumps({"error": f"Notion API 오류 ({response.status_code}): {message}"}, ensure_ascii=False)

        return json.dumps({
            "success": True,
            "pageId": data.get("id", ""),
            "url": data.get("url", ""),
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": f"{ErrorCode.TOOL_EXECUTION_FAILED.message} (notion: {str(e)})"}, ensure_ascii=False)

import json
import httpx
from common.error_code import ErrorCode

_NOTION_API_BASE = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"
_TIMEOUT = 30.0


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": _NOTION_VERSION,
    }


def _split_content(text: str, chunk_size: int = 2000) -> list[str]:
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]


def _blocks_from_text(content: str) -> list[dict]:
    blocks = []
    for chunk in _split_content(content):
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": chunk}}]
            }
        })
    return blocks


def _extract_plain_text(blocks: list) -> str:
    """Notion 블록 목록에서 plain text를 추출한다."""
    texts = []
    for block in blocks:
        block_type = block.get("type")
        content = block.get(block_type, {})
        rich_text = content.get("rich_text", [])
        for rt in rich_text:
            texts.append(rt.get("plain_text", ""))
    return "\n".join(texts)


async def _fetch_all_blocks(client: httpx.AsyncClient, page_id: str, token: str) -> list[dict]:
    """Notion 블록을 페이지네이션을 처리하며 전체 조회한다."""
    all_blocks = []
    cursor = None

    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor

        response = await client.get(
            f"{_NOTION_API_BASE}/blocks/{page_id}/children",
            headers=_headers(token),
            params=params,
        )
        data = response.json()
        all_blocks.extend(data.get("results", []))

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    return all_blocks


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
        생성된 페이지 ID, URL을 포함한 JSON 문자열
    """
    payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "properties": {
            "title": {"title": [{"type": "text", "text": {"content": title}}]}
        },
        "children": _blocks_from_text(content),
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{_NOTION_API_BASE}/pages",
                headers=_headers(token),
                json=payload,
            )
        data = response.json()
        if response.status_code != 200:
            return json.dumps({
                "error": f"Notion API 오류 ({response.status_code}): {data.get('message', '알 수 없는 오류')}"
            }, ensure_ascii=False)
        return json.dumps({
            "success": True,
            "pageId": data.get("id", ""),
            "url": data.get("url", ""),
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "error": f"{ErrorCode.TOOL_EXECUTION_FAILED.message} (notion_create_page: {str(e)})"
        }, ensure_ascii=False)


async def notion_read_page(
    token: str,
    page_id: str,
) -> str:
    """
    Notion 페이지의 제목과 본문 내용을 읽습니다.

    Args:
        token: Notion Integration Token (secret_xxx 형태)
        page_id: 읽을 페이지 ID

    Returns:
        페이지 제목, 본문 텍스트, 마지막 수정일을 포함한 JSON 문자열
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            # 페이지 메타데이터 조회
            page_response = await client.get(
                f"{_NOTION_API_BASE}/pages/{page_id}",
                headers=_headers(token),
            )
            if page_response.status_code != 200:
                data = page_response.json()
                return json.dumps({
                    "error": f"Notion API 오류 ({page_response.status_code}): {data.get('message', '알 수 없는 오류')}"
                }, ensure_ascii=False)

            page_data = page_response.json()

            # 제목 추출
            title = ""
            title_property = page_data.get("properties", {}).get("title", {})
            title_list = title_property.get("title", [])
            if title_list:
                title = title_list[0].get("plain_text", "")

            last_edited = page_data.get("last_edited_time", "")

            # 블록(본문) 전체 조회 (페이지네이션 처리)
            blocks = await _fetch_all_blocks(client, page_id, token)
            body_text = _extract_plain_text(blocks)

        return json.dumps({
            "success": True,
            "pageId": page_id,
            "title": title,
            "content": body_text,
            "lastEditedAt": last_edited,
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "error": f"{ErrorCode.TOOL_EXECUTION_FAILED.message} (notion_read_page: {str(e)})"
        }, ensure_ascii=False)


async def notion_search(
    token: str,
    query: str,
    filter_type: str = "page",
) -> str:
    """
    Notion 워크스페이스에서 페이지 또는 데이터베이스를 검색합니다.

    Args:
        token: Notion Integration Token (secret_xxx 형태)
        query: 검색어
        filter_type: "page" 또는 "database" (기본값: "page")

    Returns:
        검색 결과 목록 (id, title, url)을 포함한 JSON 문자열
    """
    payload = {
        "query": query,
        "filter": {"value": filter_type, "property": "object"},
        "page_size": 10,
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{_NOTION_API_BASE}/search",
                headers=_headers(token),
                json=payload,
            )
        data = response.json()
        if response.status_code != 200:
            return json.dumps({
                "error": f"Notion API 오류 ({response.status_code}): {data.get('message', '알 수 없는 오류')}"
            }, ensure_ascii=False)

        results = []
        for item in data.get("results", []):
            # 제목 추출 (page/database 구조 차이 처리)
            title = ""
            props = item.get("properties", {})
            title_prop = props.get("title") or props.get("Name") or {}
            title_list = title_prop.get("title", [])
            if title_list:
                title = title_list[0].get("plain_text", "")
            # database는 title 필드가 최상위에 있기도 함
            if not title and item.get("title"):
                for t in item["title"]:
                    title += t.get("plain_text", "")

            results.append({
                "id": item.get("id", ""),
                "title": title,
                "url": item.get("url", ""),
                "lastEditedAt": item.get("last_edited_time", ""),
            })

        return json.dumps({
            "success": True,
            "results": results,
            "total": len(results),
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "error": f"{ErrorCode.TOOL_EXECUTION_FAILED.message} (notion_search: {str(e)})"
        }, ensure_ascii=False)


async def notion_update_page(
    token: str,
    page_id: str,
    title: str = None,
    content: str = None,
) -> str:
    """
    Notion 페이지의 제목 또는 본문을 수정합니다.

    Args:
        token: Notion Integration Token (secret_xxx 형태)
        page_id: 수정할 페이지 ID
        title: 새 페이지 제목 (optional, 없으면 기존 유지)
        content: 새 본문 내용 (optional, 기존 내용을 전체 덮어씀)

    Returns:
        수정된 페이지 ID, URL을 포함한 JSON 문자열
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            # 제목 수정
            if title is not None:
                properties_payload = {
                    "properties": {
                        "title": {"title": [{"type": "text", "text": {"content": title}}]}
                    }
                }
                title_response = await client.patch(
                    f"{_NOTION_API_BASE}/pages/{page_id}",
                    headers=_headers(token),
                    json=properties_payload,
                )
                if title_response.status_code != 200:
                    data = title_response.json()
                    return json.dumps({
                        "error": f"Notion API 오류 ({title_response.status_code}): {data.get('message', '알 수 없는 오류')}"
                    }, ensure_ascii=False)

            # 본문 수정 — 새 블록 먼저 추가 후 기존 블록 삭제 (데이터 손실 최소화)
            # NOTE: Notion API는 트랜잭션을 지원하지 않으므로 완전한 원자성 보장 불가.
            #       새 블록 추가 성공 후 기존 블록 삭제 순서로 데이터 손실 위험을 최소화한다.
            if content is not None:
                # 1단계: 기존 블록 목록 조회 (페이지네이션 처리)
                existing_blocks = await _fetch_all_blocks(client, page_id, token)

                # 2단계: 새 블록 먼저 추가
                append_payload = {"children": _blocks_from_text(content)}
                append_response = await client.patch(
                    f"{_NOTION_API_BASE}/blocks/{page_id}/children",
                    headers=_headers(token),
                    json=append_payload,
                )
                if append_response.status_code != 200:
                    data = append_response.json()
                    return json.dumps({
                        "error": f"새 블록 추가 실패 ({append_response.status_code}): {data.get('message', '알 수 없는 오류')}"
                    }, ensure_ascii=False)

                # 3단계: 새 블록 추가 성공 후 기존 블록 삭제
                for block in existing_blocks:
                    await client.delete(
                        f"{_NOTION_API_BASE}/blocks/{block['id']}",
                        headers=_headers(token),
                    )

            # 최종 페이지 정보 조회
            page_response = await client.get(
                f"{_NOTION_API_BASE}/pages/{page_id}",
                headers=_headers(token),
            )
            page_data = page_response.json()

        return json.dumps({
            "success": True,
            "pageId": page_id,
            "url": page_data.get("url", ""),
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "error": f"{ErrorCode.TOOL_EXECUTION_FAILED.message} (notion_update_page: {str(e)})"
        }, ensure_ascii=False)


async def notion_append_block(
    token: str,
    page_id: str,
    content: str,
) -> str:
    """
    Notion 페이지 하단에 텍스트 블록을 추가합니다.

    Args:
        token: Notion Integration Token (secret_xxx 형태)
        page_id: 블록을 추가할 페이지 ID
        content: 추가할 텍스트 내용

    Returns:
        추가된 블록 ID 목록을 포함한 JSON 문자열
    """
    payload = {"children": _blocks_from_text(content)}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.patch(
                f"{_NOTION_API_BASE}/blocks/{page_id}/children",
                headers=_headers(token),
                json=payload,
            )
        data = response.json()
        if response.status_code != 200:
            return json.dumps({
                "error": f"Notion API 오류 ({response.status_code}): {data.get('message', '알 수 없는 오류')}"
            }, ensure_ascii=False)

        block_ids = [b.get("id", "") for b in data.get("results", [])]
        return json.dumps({
            "success": True,
            "blockIds": block_ids,
            "appendedCount": len(block_ids),
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "error": f"{ErrorCode.TOOL_EXECUTION_FAILED.message} (notion_append_block: {str(e)})"
        }, ensure_ascii=False)

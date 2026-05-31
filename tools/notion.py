import json
import re
import httpx
from common.error_code import ToolErrorCode
from tools.http_client import get_http_client

_NOTION_API_BASE = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"
_TIMEOUT = 30.0

# Notion rich_text 단일 텍스트 content 최대 길이
_RICH_TEXT_LIMIT = 2000

# Notion code 블록이 허용하는 언어 식별자(자주 쓰는 항목만). 미지원 시 "plain text"로 폴백
_NOTION_LANGUAGES = {
    "bash", "c", "c#", "c++", "css", "diff", "docker", "go", "graphql",
    "html", "java", "javascript", "json", "kotlin", "markdown", "php",
    "plain text", "python", "ruby", "rust", "shell", "sql", "swift",
    "typescript", "xml", "yaml",
}
_LANGUAGE_ALIASES = {
    "js": "javascript", "ts": "typescript", "py": "python", "sh": "shell",
    "yml": "yaml", "md": "markdown", "dockerfile": "docker", "golang": "go",
    "text": "plain text", "txt": "plain text", "": "plain text",
}

# 인라인 마크다운 토큰 (앞쪽 우선순위가 높음 — 코드 스팬이 먼저 매칭되어 내부 기호 보호)
_INLINE_PATTERN = re.compile(
    r"(?P<code>`[^`]+?`)"
    r"|(?P<bold>\*\*.+?\*\*)"
    r"|(?P<strike>~~.+?~~)"
    r"|(?P<link>\[[^\]]+?\]\([^)]+?\))"
    r"|(?P<italic>\*[^*]+?\*|_[^_]+?_)"
)


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": _NOTION_VERSION,
    }


def _notion_language(lang: str) -> str:
    """코드펜스 언어 식별자를 Notion이 허용하는 값으로 정규화한다. 미지원 시 'plain text'."""
    normalized = lang.strip().lower()
    normalized = _LANGUAGE_ALIASES.get(normalized, normalized)
    return normalized if normalized in _NOTION_LANGUAGES else "plain text"


def _text_segments(content: str, *, link: str = None, **annotations) -> list[dict]:
    """문자열을 Notion rich_text 세그먼트 목록으로 변환한다.

    - content가 2000자를 초과하면 여러 세그먼트로 분할 (Notion 제한)
    - annotations 중 True인 항목만 부여 (bold/italic/code/strikethrough)
    """
    active = {k: v for k, v in annotations.items() if v}
    segments = []
    # 빈 문자열은 세그먼트 생성하지 않음 (호출부에서 빈 rich_text 허용)
    for i in range(0, len(content), _RICH_TEXT_LIMIT):
        chunk = content[i:i + _RICH_TEXT_LIMIT]
        text_obj = {"content": chunk}
        if link:
            text_obj["link"] = {"url": link}
        segment = {"type": "text", "text": text_obj}
        if active:
            segment["annotations"] = active
        segments.append(segment)
    return segments


def _parse_inline(text: str) -> list[dict]:
    """인라인 마크다운(**bold**, *italic*, `code`, ~~strike~~, [text](url))을 rich_text 배열로 변환한다."""
    segments = []
    pos = 0
    for m in _INLINE_PATTERN.finditer(text):
        if m.start() > pos:
            segments.extend(_text_segments(text[pos:m.start()]))
        kind = m.lastgroup
        raw = m.group()
        if kind == "code":
            segments.extend(_text_segments(raw[1:-1], code=True))
        elif kind == "bold":
            segments.extend(_text_segments(raw[2:-2], bold=True))
        elif kind == "strike":
            segments.extend(_text_segments(raw[2:-2], strikethrough=True))
        elif kind == "italic":
            segments.extend(_text_segments(raw[1:-1], italic=True))
        elif kind == "link":
            link_match = re.match(r"\[([^\]]+)\]\(([^)]+)\)", raw)
            segments.extend(_text_segments(link_match.group(1), link=link_match.group(2)))
        pos = m.end()
    if pos < len(text):
        segments.extend(_text_segments(text[pos:]))
    return segments


def _blocks_from_text(content: str) -> list[dict]:
    """마크다운 텍스트를 Notion 블록 목록으로 변환한다.

    지원: heading_1~3(#), bulleted_list_item(-/*/+), numbered_list_item(1.),
    to_do(- [ ]/- [x]), quote(>), code(```), divider(---), paragraph(그 외).
    인라인 서식(bold/italic/code/strike/link)은 _parse_inline이 처리.
    """
    blocks = []
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 코드펜스 ```lang ... ```
        if stripped.startswith("```"):
            language = _notion_language(stripped[3:])
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # 닫는 펜스 스킵 (없으면 EOF에서 자연 종료)
            blocks.append({
                "object": "block",
                "type": "code",
                "code": {
                    "rich_text": _text_segments("\n".join(code_lines)),
                    "language": language,
                },
            })
            continue

        # 빈 줄은 블록 미생성
        if not stripped:
            i += 1
            continue

        # 구분선
        if stripped in ("---", "***", "___"):
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            i += 1
            continue

        # 헤딩 (#, ##, ###)
        heading = re.match(r"^(#{1,3})\s+(.*)", stripped)
        if heading:
            htype = f"heading_{len(heading.group(1))}"
            blocks.append({
                "object": "block",
                "type": htype,
                htype: {"rich_text": _parse_inline(heading.group(2))},
            })
            i += 1
            continue

        # 인용
        if stripped.startswith(">"):
            quote_text = stripped[1:].lstrip()
            blocks.append({
                "object": "block",
                "type": "quote",
                "quote": {"rich_text": _parse_inline(quote_text)},
            })
            i += 1
            continue

        # 체크박스 (- [ ] / - [x])
        todo = re.match(r"^[-*+]\s+\[([ xX])\]\s+(.*)", stripped)
        if todo:
            blocks.append({
                "object": "block",
                "type": "to_do",
                "to_do": {
                    "rich_text": _parse_inline(todo.group(2)),
                    "checked": todo.group(1).lower() == "x",
                },
            })
            i += 1
            continue

        # 불릿 리스트
        bullet = re.match(r"^[-*+]\s+(.*)", stripped)
        if bullet:
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": _parse_inline(bullet.group(1))},
            })
            i += 1
            continue

        # 넘버드 리스트
        numbered = re.match(r"^\d+\.\s+(.*)", stripped)
        if numbered:
            blocks.append({
                "object": "block",
                "type": "numbered_list_item",
                "numbered_list_item": {"rich_text": _parse_inline(numbered.group(1))},
            })
            i += 1
            continue

        # 일반 문단
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": _parse_inline(stripped)},
        })
        i += 1

    # 빈 입력이면 빈 문단 1개 (Notion children 빈 배열 회피)
    if not blocks:
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": []},
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
            timeout=_TIMEOUT,
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
        client = get_http_client()
        response = await client.post(
            f"{_NOTION_API_BASE}/pages",
            headers=_headers(token),
            json=payload,
            timeout=_TIMEOUT,
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
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (notion_create_page: {str(e)})"
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
        client = get_http_client()
        # 페이지 메타데이터 조회
        page_response = await client.get(
            f"{_NOTION_API_BASE}/pages/{page_id}",
            headers=_headers(token),
            timeout=_TIMEOUT,
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
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (notion_read_page: {str(e)})"
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
        client = get_http_client()
        response = await client.post(
            f"{_NOTION_API_BASE}/search",
            headers=_headers(token),
            json=payload,
            timeout=_TIMEOUT,
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
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (notion_search: {str(e)})"
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
        client = get_http_client()
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
                timeout=_TIMEOUT,
            )
            if title_response.status_code != 200:
                data = title_response.json()
                return json.dumps({
                    "error": f"Notion API 오류 ({title_response.status_code}): {data.get('message', '알 수 없는 오류')}"
                }, ensure_ascii=False)

        # 본문 수정 — 새 블록 먼저 추가 후 기존 블록 삭제 (데이터 손실 최소화)
        if content is not None:
            # 1단계: 기존 블록 목록 조회 (페이지네이션 처리)
            existing_blocks = await _fetch_all_blocks(client, page_id, token)

            # 2단계: 새 블록 먼저 추가
            append_payload = {"children": _blocks_from_text(content)}
            append_response = await client.patch(
                f"{_NOTION_API_BASE}/blocks/{page_id}/children",
                headers=_headers(token),
                json=append_payload,
                timeout=_TIMEOUT,
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
                    timeout=_TIMEOUT,
                )

        # 최종 페이지 정보 조회
        page_response = await client.get(
            f"{_NOTION_API_BASE}/pages/{page_id}",
            headers=_headers(token),
            timeout=_TIMEOUT,
        )
        page_data = page_response.json()

        return json.dumps({
            "success": True,
            "pageId": page_id,
            "url": page_data.get("url", ""),
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (notion_update_page: {str(e)})"
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
        client = get_http_client()
        response = await client.patch(
            f"{_NOTION_API_BASE}/blocks/{page_id}/children",
            headers=_headers(token),
            json=payload,
            timeout=_TIMEOUT,
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
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (notion_append_block: {str(e)})"
        }, ensure_ascii=False)

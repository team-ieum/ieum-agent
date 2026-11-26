"""Notion 페이지·블록·사용자 조회 도구(IEUM-AI-118, Notion 설계 3절 S5·S6·S7·S8·S11).

페이지·속성·블록은 다른 사람이 쓴 글(프롬프트 인젝션)이라 ACTION 전용, 사용자 정보만 AI 노출이다
(agent 규칙 .claude/rules/security.md — 외부 글을 다루는 도구는 AI 노드에 싣지 않는다)."""
import json
import re
from urllib.parse import quote

from common.error_code import ToolErrorCode
from tools.http_client import get_http_client
from tools.notion import _NOTION_API_BASE, _TIMEOUT, _headers, _simplify_property

# Notion id — UUID(하이픈 있거나 없음). 페이지 주소(…/제목-<32hex>?…)를 붙여 넣으면 마지막 32자리 hex를 쓴다.
_ID = re.compile(r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}")
_OUTPUT_BUDGET = 1_000_000  # 하위 블록 출력 상한(문자) — BE 응답 한도 안에서 끝나게
_MAX_PROPERTY_PAGES = 20  # 속성 하나 가져오기 페이지 넘김 상한(100개씩 → 2,000개)


def _brief(value) -> str:
    """오류 문구용 — 큰 입력이 문구를 부풀리지 않게 앞 80자만(agent 규칙 tools.md)."""
    r = repr(value)
    return r if len(r) <= 80 else r[:80] + "…"


def _notion_id(value) -> str | None:
    text = str(value or "").strip()
    if _ID.fullmatch(text):
        return text
    found = _ID.findall(text.split("?")[0])
    return found[-1] if found else None


def _fail(tool: str, reason: str) -> str:
    return json.dumps({"error": f"{ToolErrorCode.EXECUTION_FAILED.message} ({tool}: {reason})"}, ensure_ascii=False)


def _api_error(response) -> str:
    try:
        message = response.json().get("message", "알 수 없는 오류")
    except Exception:
        message = "알 수 없는 오류"
    return json.dumps({"error": f"Notion API 오류 ({response.status_code}): {message}"}, ensure_ascii=False)


def _ok(response) -> bool:
    return 200 <= response.status_code < 300


async def _get(token: str, path: str, params: dict | None = None):
    return await get_http_client().get(f"{_NOTION_API_BASE}{path}", headers=_headers(token), params=params or {},
                                       timeout=_TIMEOUT)


def _plain(rich) -> str:
    return "".join(r.get("plain_text", "") for r in rich or [])


def _value(prop: dict):
    """속성 값을 단순 값으로. 기존 _simplify_property가 모르는 종류(관계·수식·파일 등)도 채운다."""
    ptype = prop.get("type")
    v = prop.get(ptype)
    if ptype == "relation":
        return [r.get("id", "") for r in v or []]
    if ptype == "formula":
        return (v or {}).get((v or {}).get("type"))
    if ptype == "files":
        return [f.get("name", "") for f in v or []]
    if ptype in ("created_time", "last_edited_time"):
        return v
    if ptype in ("created_by", "last_edited_by"):
        return (v or {}).get("name") or (v or {}).get("id")
    if ptype == "unique_id":
        return f"{v.get('prefix') + '-' if v and v.get('prefix') else ''}{(v or {}).get('number')}"
    if ptype == "date":
        return (v or {}).get("start") if v else None
    return _simplify_property(prop)


def _title(page: dict) -> str:
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            return _plain(prop.get("title"))
    return ""


async def notion_get_page(token: str, page_id: str) -> str:
    """
    Notion 페이지나 DB 행의 속성을 가져옵니다(본문 제외). 없거나 공유되지 않았으면 found가 거짓입니다.
    ACTION 노드 전용(외부 글) — AI 노드 도구 선택지에 싣지 않는다.

    Args:
        token: Notion Integration Token
        page_id: 페이지·행 ID 또는 Notion 페이지 주소

    Returns:
        found·total·items와 title·url·properties를 담은 JSON 문자열. 관계·사람은 Notion이 25개까지만 준다.
    """
    tool = "notion_get_page"
    pid = _notion_id(page_id)
    if not pid:
        return _fail(tool, f"페이지 ID 형식이 아니에요: {_brief(page_id)}")
    try:
        response = await _get(token, f"/pages/{pid}")
        if response.status_code == 404:
            return json.dumps({"success": True, "found": False, "total": 0, "items": [], "title": "", "url": "",
                               "properties": {}}, ensure_ascii=False)
        if not _ok(response):
            return _api_error(response)
        page = response.json()
        props = {name: _value(prop) for name, prop in (page.get("properties") or {}).items()}
        item = {"id": page.get("id", pid), "title": _title(page), "url": page.get("url", ""), "properties": props,
                "parentType": (page.get("parent") or {}).get("type", ""), "inTrash": bool(page.get("in_trash")),
                "createdAt": page.get("created_time", ""), "lastEditedAt": page.get("last_edited_time", "")}
        return json.dumps({"success": True, "found": True, "total": 1, "items": [item], "title": item["title"],
                           "url": item["url"], "properties": props}, ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))


_LIST_TYPES = {"title", "rich_text", "people", "relation"}


def _property_item(item: dict):
    ptype = item.get("type")
    v = item.get(ptype)
    if ptype in ("title", "rich_text"):
        return (v or {}).get("plain_text", "")
    if ptype == "people":
        return (v or {}).get("name") or (v or {}).get("id", "")
    if ptype == "relation":
        return (v or {}).get("id", "")
    return _value(item)


async def notion_get_property(token: str, page_id: str, property_id: str) -> str:
    """
    페이지·DB 행의 속성 하나를 페이지를 넘기며 끝까지 가져옵니다(관계·사람 25개 제한 없음, 최대 2,000개).
    ACTION 노드 전용(외부 글) — AI 노드 도구 선택지에 싣지 않는다.

    Args:
        token: Notion Integration Token
        page_id: 페이지·행 ID 또는 Notion 페이지 주소
        property_id: 속성 ID(속성 드롭다운 값)

    Returns:
        found·total·items(값 하나씩)·type·value(한 덩어리) JSON 문자열
    """
    tool = "notion_get_property"
    pid = _notion_id(page_id)
    prop_id = str(property_id or "").strip()
    if not pid or not prop_id:
        return _fail(tool, "페이지와 속성이 필요해요")
    try:
        items, ptype, params = [], "", {"page_size": 100}
        for _ in range(_MAX_PROPERTY_PAGES):
            response = await _get(token, f"/pages/{pid}/properties/{quote(prop_id, safe='')}", params)
            if response.status_code == 404:
                return json.dumps({"success": True, "found": False, "total": 0, "items": [], "type": "",
                                   "value": None}, ensure_ascii=False)
            if not _ok(response):
                return _api_error(response)
            data = response.json()
            if data.get("object") != "list":  # 값 하나짜리 종류(숫자·선택·날짜…)는 목록이 아니다
                ptype = data.get("type", "")
                value = _value(data)
                empty = value is None or value == "" or value == []
                listed = value if isinstance(value, list) else ([] if empty else [value])
                return json.dumps({"success": True, "found": not empty, "total": len(listed), "items": listed,
                                   "type": ptype, "value": value}, ensure_ascii=False)
            ptype = (data.get("property_item") or {}).get("type") or ptype
            items += [_property_item(r) for r in data.get("results", [])]
            if not data.get("has_more") or not data.get("next_cursor"):
                break
            params = {"page_size": 100, "start_cursor": data["next_cursor"]}
        if ptype == "rollup":
            rollup = (data.get("property_item") or {}).get("rollup") or {}
            value = rollup.get(rollup.get("type"))
        else:
            value = "".join(items) if ptype in ("title", "rich_text") else items
        return json.dumps({"success": True, "found": bool(items) or value not in (None, "", []),
                           "total": len(items), "items": items, "type": ptype, "value": value}, ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))


def _block_text(block: dict) -> str:
    btype = block.get("type", "")
    body = block.get(btype) or {}
    if btype in ("child_page", "child_database"):
        return body.get("title", "")
    if btype in ("bookmark", "embed", "link_preview"):
        return body.get("url", "")
    if btype == "equation":
        return body.get("expression", "")
    return _plain(body.get("rich_text"))


def _block_item(block: dict) -> dict:
    return {"id": block.get("id", ""), "type": block.get("type", ""), "text": _block_text(block),
            "hasChildren": bool(block.get("has_children"))}


_MD_PREFIX = {"heading_1": "# ", "heading_2": "## ", "heading_3": "### ", "heading_4": "#### ",
              "bulleted_list_item": "- ", "numbered_list_item": "1. ", "quote": "> ", "toggle": "- "}


def _markdown(block: dict) -> str:
    btype, text = block.get("type", ""), _block_text(block)
    body = block.get(btype) or {}
    if btype == "to_do":
        return f"- [{'x' if body.get('checked') else ' '}] {text}"
    if btype == "code":
        return f"```{body.get('language', '')}\n{text}\n```"
    if btype == "divider":
        return "---"
    if btype == "child_page":
        return f"📄 {text}"
    return _MD_PREFIX.get(btype, "") + text


async def notion_get_block(token: str, block_id: str) -> str:
    """
    Notion 블록 하나의 종류와 글을 가져옵니다. 없거나 공유되지 않았으면 found가 거짓입니다.
    ACTION 노드 전용(외부 글) — AI 노드 도구 선택지에 싣지 않는다.

    Args:
        token: Notion Integration Token
        block_id: 블록·페이지 ID 또는 Notion 주소

    Returns:
        found·total·items와 type·text JSON 문자열
    """
    tool = "notion_get_block"
    bid = _notion_id(block_id)
    if not bid:
        return _fail(tool, f"블록 ID 형식이 아니에요: {_brief(block_id)}")
    try:
        response = await _get(token, f"/blocks/{bid}")
        if response.status_code == 404:
            return json.dumps({"success": True, "found": False, "total": 0, "items": [], "type": "", "text": ""},
                              ensure_ascii=False)
        if not _ok(response):
            return _api_error(response)
        block = response.json()
        item = {**_block_item(block), "createdAt": block.get("created_time", ""),
                "lastEditedAt": block.get("last_edited_time", "")}
        return json.dumps({"success": True, "found": True, "total": 1, "items": [item], "type": item["type"],
                           "text": item["text"]}, ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))


async def notion_get_block_children(token: str, block_id: str, output_format: str = "markdown",
                                    page_size: int = 100, start_cursor: str = "") -> str:
    """
    블록이나 페이지 바로 아래 블록을 가져옵니다(한 단계, 최대 100개씩 — 설계 결정 ④로 두 노드를 하나로).
    ACTION 노드 전용(외부 글) — AI 노드 도구 선택지에 싣지 않는다.

    Args:
        token: Notion Integration Token
        block_id: 블록·페이지 ID 또는 Notion 주소
        output_format: "markdown"(기본) 또는 "json"(원본 블록)
        page_size: 가져올 개수 (1~100, 기본 100)
        start_cursor: 이전 결과의 nextCursor. 비우면 처음부터

    Returns:
        found·total·items·markdown·raw·nextCursor·hasMore·truncated JSON 문자열. 출력이 너무 크면 뒤쪽 블록을
        빼고 truncated=true, nextCursor는 뺀 첫 블록부터 이어 가져오게 한다.
    """
    tool = "notion_get_block_children"
    bid = _notion_id(block_id)
    if not bid:
        return _fail(tool, f"블록 ID 형식이 아니에요: {_brief(block_id)}")
    fmt = str(output_format or "").strip() or "markdown"
    if fmt not in ("markdown", "json"):
        return _fail(tool, f"모르는 출력 형식: {_brief(output_format)}")
    try:  # FE 숫자 칸은 문자열을 보낼 수 있다
        size = min(max(int(page_size), 1), 100)
    except (TypeError, ValueError):
        size = 100
    params = {"page_size": size}
    if str(start_cursor or "").strip():
        params["start_cursor"] = str(start_cursor).strip()
    try:
        response = await _get(token, f"/blocks/{bid}/children", params)
        if not _ok(response):
            return _api_error(response)
        data = response.json()
        blocks = data.get("results", [])
        has_more = bool(data.get("has_more"))
        next_cursor = (data.get("next_cursor") or "") if has_more else ""
        truncated = False

        def render(bs):
            return {"items": [_block_item(b) for b in bs],
                    "markdown": "\n".join(_markdown(b) for b in bs) if fmt == "markdown" else "",
                    "raw": bs if fmt == "json" else []}

        out = render(blocks)
        while blocks and len(json.dumps(out, ensure_ascii=False)) > _OUTPUT_BUDGET:
            cut = len(blocks) // 2  # 뒤쪽 절반을 빼고, 뺀 첫 블록부터 이어 읽게 커서를 둔다(블록 id가 곧 커서)
            next_cursor, has_more, truncated = blocks[cut]["id"], True, True
            blocks = blocks[:cut]
            out = render(blocks)
        return json.dumps({"success": True, "found": bool(blocks), "total": len(blocks), **out,
                           "nextCursor": next_cursor, "hasMore": has_more, "truncated": truncated},
                          ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))


async def notion_get_user(token: str, user_id: str) -> str:
    """
    Notion 워크스페이스 사용자 한 명의 이름·이메일을 가져옵니다. 없으면 found가 거짓입니다.
    이메일은 연동에 사용자 이메일 권한이 있을 때만 온다.

    Args:
        token: Notion Integration Token
        user_id: 사용자 ID

    Returns:
        found·total·items와 name·email JSON 문자열
    """
    tool = "notion_get_user"
    uid = _notion_id(user_id)
    if not uid:
        return _fail(tool, f"사용자 ID 형식이 아니에요: {_brief(user_id)}")
    try:
        response = await _get(token, f"/users/{uid}")
        if response.status_code == 404:
            return json.dumps({"success": True, "found": False, "total": 0, "items": [], "name": "", "email": ""},
                              ensure_ascii=False)
        if not _ok(response):
            return _api_error(response)
        user = response.json()
        item = {"id": user.get("id", uid), "name": user.get("name") or "", "type": user.get("type", ""),
                "email": (user.get("person") or {}).get("email", ""), "avatarUrl": user.get("avatar_url") or ""}
        return json.dumps({"success": True, "found": True, "total": 1, "items": [item], "name": item["name"],
                           "email": item["email"]}, ensure_ascii=False)
    except Exception as e:
        return _fail(tool, str(e))

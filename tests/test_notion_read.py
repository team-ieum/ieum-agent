"""Notion 페이지·블록·사용자 조회 도구(IEUM-AI-118). HTTP는 목으로 대신한다."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.action_outputs import assert_matches_outputs
from tools import notion_read as nr

PID = "1f2e3d4c5b6a79881f2e3d4c5b6a7988"


def _resp(status: int, data: dict) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = data
    return r


def _client(*responses):
    client = MagicMock()
    client.get = AsyncMock(side_effect=list(responses))
    return client


async def _run(fn, client, **kwargs) -> dict:
    with patch("tools.notion_read.get_http_client", return_value=client):
        return json.loads(await fn(token="secret", **kwargs))


_PAGE = {"id": PID, "url": "https://notion.so/x", "parent": {"type": "data_source_id"}, "in_trash": False,
         "created_time": "2026-11-01T00:00:00.000Z", "last_edited_time": "2026-11-02T00:00:00.000Z",
         "properties": {
             "이름": {"id": "title", "type": "title", "title": [{"plain_text": "주간 "}, {"plain_text": "회의"}]},
             "상태": {"id": "s", "type": "status", "status": {"name": "진행중"}},
             "관련": {"id": "r", "type": "relation", "relation": [{"id": "rel-1"}]},
             "번호": {"id": "u", "type": "unique_id", "unique_id": {"prefix": "TASK", "number": 7}}}}


@pytest.mark.asyncio
async def test_get_page_속성을_단순_값으로_돌려준다():
    client = _client(_resp(200, _PAGE))
    result = await _run(nr.notion_get_page, client, page_id=PID)
    assert_matches_outputs("notion.get_page", result)
    assert (result["title"], result["items"][0]["parentType"]) == ("주간 회의", "data_source_id")
    assert result["properties"] == {"이름": "주간 회의", "상태": "진행중", "관련": ["rel-1"], "번호": "TASK-7"}
    assert client.get.call_args.args[0].endswith(f"/pages/{PID}")


@pytest.mark.asyncio
async def test_get_page_주소를_붙여넣어도_ID를_뽑는다():
    client = _client(_resp(200, _PAGE))
    await _run(nr.notion_get_page, client, page_id=f"https://www.notion.so/team/회의록-{PID}?pvs=4")
    assert client.get.call_args.args[0].endswith(f"/pages/{PID}")


@pytest.mark.asyncio
async def test_get_page_없으면_found_false_성공():
    result = await _run(nr.notion_get_page, _client(_resp(404, {"message": "Could not find page"})), page_id=PID)
    assert_matches_outputs("notion.get_page", result)
    assert (result["found"], result["properties"]) == (False, {})


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "../users", "x" * 200])
async def test_get_page_ID_형식이_아니면_호출_없이_실패하고_입력은_앞부분만(bad):
    client = _client()
    result = await _run(nr.notion_get_page, client, page_id=bad)
    assert "error" in result and len(result["error"]) < 200
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_get_page_권한_오류는_실패다():
    result = await _run(nr.notion_get_page, _client(_resp(403, {"message": "restricted"})), page_id=PID)
    assert "403" in result["error"]


@pytest.mark.asyncio
async def test_get_property_목록은_페이지를_넘기며_끝까지_모은다():
    page1 = {"object": "list", "property_item": {"type": "relation"}, "has_more": True, "next_cursor": "c2",
             "results": [{"type": "relation", "relation": {"id": f"r{i}"}} for i in range(25)]}
    page2 = {"object": "list", "property_item": {"type": "relation"}, "has_more": False,
             "results": [{"type": "relation", "relation": {"id": "r25"}}]}
    client = _client(_resp(200, page1), _resp(200, page2))
    result = await _run(nr.notion_get_property, client, page_id=PID, property_id="a%3Db")
    assert_matches_outputs("notion.get_property", result)
    assert (result["total"], result["type"], result["items"][-1]) == (26, "relation", "r25")
    assert client.get.call_args_list[0].args[0].endswith(f"/pages/{PID}/properties/a%253Db")
    assert client.get.call_args_list[1].kwargs["params"]["start_cursor"] == "c2"


@pytest.mark.asyncio
async def test_get_property_글은_이어붙이고_조각은_items로():
    data = {"object": "list", "property_item": {"type": "rich_text"}, "has_more": False,
            "results": [{"type": "rich_text", "rich_text": {"plain_text": "안녕 "}},
                        {"type": "rich_text", "rich_text": {"plain_text": "하세요"}}]}
    result = await _run(nr.notion_get_property, _client(_resp(200, data)), page_id=PID, property_id="t")
    assert (result["value"], result["items"]) == ("안녕 하세요", ["안녕 ", "하세요"])


@pytest.mark.asyncio
@pytest.mark.parametrize("data, value, found", [
    ({"object": "property_item", "type": "number", "number": 3}, 3, True),
    ({"object": "property_item", "type": "select", "select": None}, None, False),
    ({"object": "property_item", "type": "checkbox", "checkbox": False}, False, True),
])
async def test_get_property_값_하나짜리_종류(data, value, found):
    result = await _run(nr.notion_get_property, _client(_resp(200, data)), page_id=PID, property_id="p")
    assert_matches_outputs("notion.get_property", result)
    assert (result["value"], result["found"]) == (value, found)


@pytest.mark.asyncio
async def test_get_property_빈_속성은_호출_없이_실패():
    assert "error" in await _run(nr.notion_get_property, _client(), page_id=PID, property_id=" ")


_BLOCKS = [{"id": "b1", "type": "heading_1", "has_children": False, "heading_1": {"rich_text": [{"plain_text": "제목"}]}},
           {"id": "b2", "type": "to_do", "has_children": False, "to_do": {"checked": True, "rich_text": [{"plain_text": "할 일"}]}},
           {"id": "b3", "type": "code", "has_children": False, "code": {"language": "python", "rich_text": [{"plain_text": "x = 1"}]}},
           {"id": "b4", "type": "child_page", "has_children": True, "child_page": {"title": "하위"}}]


@pytest.mark.asyncio
async def test_get_block_종류와_글():
    result = await _run(nr.notion_get_block, _client(_resp(200, {**_BLOCKS[1], "created_time": "t"})), block_id=PID)
    assert_matches_outputs("notion.get_block", result)
    assert (result["type"], result["text"]) == ("to_do", "할 일")


@pytest.mark.asyncio
async def test_get_block_없으면_found_false_성공():
    result = await _run(nr.notion_get_block, _client(_resp(404, {})), block_id=PID)
    assert_matches_outputs("notion.get_block", result)
    assert result["found"] is False


@pytest.mark.asyncio
async def test_get_block_children_markdown():
    client = _client(_resp(200, {"results": _BLOCKS, "has_more": True, "next_cursor": "b5"}))
    result = await _run(nr.notion_get_block_children, client, block_id=PID, page_size="30")
    assert_matches_outputs("notion.get_block_children", result)
    assert result["markdown"] == "# 제목\n- [x] 할 일\n```python\nx = 1\n```\n📄 하위"
    assert (result["raw"], result["nextCursor"], result["hasMore"], result["truncated"]) == ([], "b5", True, False)
    assert client.get.call_args.kwargs["params"] == {"page_size": 30}


@pytest.mark.asyncio
async def test_get_block_children_json과_커서():
    client = _client(_resp(200, {"results": _BLOCKS[:1], "has_more": False, "next_cursor": None}))
    result = await _run(nr.notion_get_block_children, client, block_id=PID, output_format="json", start_cursor="b0")
    assert (result["raw"], result["markdown"], result["nextCursor"]) == (_BLOCKS[:1], "", "")
    assert client.get.call_args.kwargs["params"] == {"page_size": 100, "start_cursor": "b0"}


@pytest.mark.asyncio
async def test_get_block_children_출력_예산을_넘으면_뒤쪽을_빼고_이어읽을_커서를_준다():
    big = [{"id": f"b{i}", "type": "paragraph", "has_children": False,
            "paragraph": {"rich_text": [{"plain_text": "가" * 2000}]}} for i in range(100)]
    with patch.object(nr, "_OUTPUT_BUDGET", 50_000):
        result = await _run(nr.notion_get_block_children, _client(_resp(200, {"results": big, "has_more": False})),
                            block_id=PID, output_format="json")
    assert result["truncated"] is True and result["hasMore"] is True
    assert result["nextCursor"] == f"b{result['total']}"
    assert len(json.dumps(result, ensure_ascii=False)) < 60_000


@pytest.mark.asyncio
async def test_get_block_children_모르는_형식은_실패():
    assert "error" in await _run(nr.notion_get_block_children, _client(), block_id=PID, output_format="html")


@pytest.mark.asyncio
async def test_get_user_사람():
    user = {"id": PID, "name": "한민정", "type": "person", "avatar_url": None, "person": {"email": "a@x.com"}}
    result = await _run(nr.notion_get_user, _client(_resp(200, user)), user_id=PID)
    assert_matches_outputs("notion.get_user", result)
    assert (result["name"], result["email"], result["items"][0]["avatarUrl"]) == ("한민정", "a@x.com", "")


@pytest.mark.asyncio
async def test_get_user_없으면_found_false():
    result = await _run(nr.notion_get_user, _client(_resp(404, {})), user_id=PID)
    assert_matches_outputs("notion.get_user", result)
    assert result["found"] is False

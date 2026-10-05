import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_response(status_code: int, data: dict) -> MagicMock:
    """httpx 응답 mock 생성 헬퍼."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = data
    return response


@pytest.fixture
def mock_client():
    """httpx.AsyncClient mock 픽스처."""
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


# ---------------------------------------------------------------------------
# notion_create_page
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_notion_create_page_성공(mock_client):
    mock_client.post = AsyncMock(return_value=_make_response(200, {
        "id": "page-123",
        "url": "https://notion.so/page-123",
    }))

    with patch("tools.notion.get_http_client", return_value=mock_client):
        from tools.notion import notion_create_page
        result = json.loads(await notion_create_page(
            token="secret_test",
            parent_page_id="parent-123",
            title="테스트 페이지",
            content="본문 내용",
        ))

    assert result["success"] is True
    assert result["pageId"] == "page-123"
    assert result["url"] == "https://notion.so/page-123"


@pytest.mark.asyncio
async def test_notion_create_page_api_오류(mock_client):
    mock_client.post = AsyncMock(return_value=_make_response(401, {
        "message": "API token is invalid."
    }))

    with patch("tools.notion.get_http_client", return_value=mock_client):
        from tools.notion import notion_create_page
        result = json.loads(await notion_create_page(
            token="invalid_token",
            parent_page_id="parent-123",
            title="테스트",
            content="내용",
        ))

    assert "error" in result
    assert "401" in result["error"]


# ---------------------------------------------------------------------------
# notion_read_page
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_notion_read_page_성공(mock_client):
    page_data = {
        "id": "page-123",
        "last_edited_time": "2026-05-13T00:00:00.000Z",
        "properties": {
            "title": {
                "title": [{"plain_text": "테스트 페이지"}]
            }
        }
    }
    blocks_data = {
        "results": [
            {
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"plain_text": "본문 내용입니다."}]
                }
            }
        ]
    }

    mock_client.get = AsyncMock(side_effect=[
        _make_response(200, page_data),
        _make_response(200, blocks_data),
    ])

    with patch("tools.notion.get_http_client", return_value=mock_client):
        from tools.notion import notion_read_page
        result = json.loads(await notion_read_page(
            token="secret_test",
            page_id="page-123",
        ))

    assert result["success"] is True
    assert result["title"] == "테스트 페이지"
    assert "본문 내용입니다." in result["content"]


# ---------------------------------------------------------------------------
# notion_search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_notion_search_성공(mock_client):
    mock_client.post = AsyncMock(return_value=_make_response(200, {
        "results": [
            {
                "id": "page-1",
                "url": "https://notion.so/page-1",
                "last_edited_time": "2026-05-13T00:00:00.000Z",
                "properties": {
                    "title": {"title": [{"plain_text": "경제 뉴스"}]}
                }
            }
        ]
    }))

    with patch("tools.notion.get_http_client", return_value=mock_client):
        from tools.notion import notion_search
        result = json.loads(await notion_search(
            token="secret_test",
            query="경제 뉴스",
        ))

    assert result["success"] is True
    assert result["total"] == 1
    assert result["results"][0]["title"] == "경제 뉴스"


@pytest.mark.asyncio
@pytest.mark.parametrize("filter_type, sent", [
    ("database", "data_source"),   # LLM·템플릿이 쓰는 값 → 2026-03-11의 data source
    ("page", "page"),
    ("data_source", "data_source"),
])
async def test_notion_search_filter_type_매핑(mock_client, filter_type, sent):
    mock_client.post = AsyncMock(return_value=_make_response(200, {"results": []}))

    with patch("tools.notion.get_http_client", return_value=mock_client):
        from tools.notion import notion_search
        await notion_search(token="secret_test", query="업무", filter_type=filter_type)

    kwargs = mock_client.post.call_args.kwargs
    assert kwargs["json"]["filter"] == {"value": sent, "property": "object"}
    assert kwargs["headers"]["Notion-Version"] == "2026-03-11"


@pytest.mark.asyncio
async def test_notion_search_data_source_결과는_최상위_title이_제목(mock_client):
    """data source의 properties는 스키마 정의(title 값이 {})라 최상위 title로 이름을 만든다."""
    mock_client.post = AsyncMock(return_value=_make_response(200, {"results": [{
        "object": "data_source",
        "id": "ds-1",
        "parent": {"type": "database_id", "database_id": "aaaa-bbbb"},
        "title": [{"plain_text": "업무 "}, {"plain_text": "보드"}],
        "properties": {"Name": {"id": "title", "name": "Name", "type": "title", "title": {}}},
    }]}))

    with patch("tools.notion.get_http_client", return_value=mock_client):
        from tools.notion import notion_search
        result = json.loads(await notion_search(token="secret_test", query="업무", filter_type="database"))

    assert result["results"][0]["id"] == "ds-1"
    assert result["results"][0]["title"] == "업무 보드"
    # data source 객체엔 url이 없다 — 부모 database id로 만든다
    assert result["results"][0]["url"] == "https://www.notion.so/aaaabbbb"


# ---------------------------------------------------------------------------
# notion_append_block
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_notion_append_block_성공(mock_client):
    mock_client.patch = AsyncMock(return_value=_make_response(200, {
        "results": [
            {"id": "block-1"},
            {"id": "block-2"},
        ]
    }))

    with patch("tools.notion.get_http_client", return_value=mock_client):
        from tools.notion import notion_append_block
        result = json.loads(await notion_append_block(
            token="secret_test",
            page_id="page-123",
            content="추가할 내용",
        ))

    assert result["success"] is True
    assert result["appendedCount"] == 2
    assert "block-1" in result["blockIds"]


# ---------------------------------------------------------------------------
# notion_update_page
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_notion_update_page_제목만_수정(mock_client):
    """title만 전달하면 제목만 수정되고 content 관련 API는 호출되지 않는다."""
    page_data = {"id": "page-123", "url": "https://notion.so/page-123"}

    mock_client.patch = AsyncMock(return_value=_make_response(200, page_data))
    mock_client.get = AsyncMock(return_value=_make_response(200, {"id": "page-123", "url": "https://notion.so/page-123"}))

    with patch("tools.notion.get_http_client", return_value=mock_client):
        from tools.notion import notion_update_page
        result = json.loads(await notion_update_page(
            token="secret_test",
            page_id="page-123",
            title="새 제목",
        ))

    assert result["success"] is True
    assert result["pageId"] == "page-123"


@pytest.mark.asyncio
async def test_notion_update_page_본문_수정_새블록_먼저_추가(mock_client):
    """content 수정 시 새 블록을 먼저 추가하고 기존 블록을 삭제한다."""
    existing_blocks_data = {
        "results": [{"id": "old-block-1"}, {"id": "old-block-2"}],
        "has_more": False,
    }
    append_response_data = {
        "results": [{"id": "new-block-1"}]
    }
    page_data = {"id": "page-123", "url": "https://notion.so/page-123"}

    get_call_count = {"value": 0}

    async def mock_get(url, **kwargs):
        get_call_count["value"] += 1
        if "children" in url:
            return _make_response(200, existing_blocks_data)
        return _make_response(200, page_data)

    patch_call_order = []

    async def mock_patch(url, **kwargs):
        patch_call_order.append("append")
        return _make_response(200, append_response_data)

    delete_call_order = []

    async def mock_delete(url, **kwargs):
        delete_call_order.append(url)
        return _make_response(200, {})

    mock_client.get = mock_get
    mock_client.patch = mock_patch
    mock_client.delete = mock_delete

    with patch("tools.notion.get_http_client", return_value=mock_client):
        from tools.notion import notion_update_page
        result = json.loads(await notion_update_page(
            token="secret_test",
            page_id="page-123",
            content="새로운 본문 내용",
        ))

    assert result["success"] is True
    # 새 블록 추가가 먼저 호출되었는지 확인
    assert "append" in patch_call_order
    # 기존 블록 2개가 삭제되었는지 확인
    assert len(delete_call_order) == 2
    assert any("old-block-1" in url for url in delete_call_order)
    assert any("old-block-2" in url for url in delete_call_order)


@pytest.mark.asyncio
async def test_notion_update_page_새블록_추가_실패시_기존블록_유지(mock_client):
    """새 블록 추가가 실패하면 기존 블록을 삭제하지 않는다."""
    existing_blocks_data = {
        "results": [{"id": "old-block-1"}],
        "has_more": False,
    }

    async def mock_get(url, **kwargs):
        return _make_response(200, existing_blocks_data)

    async def mock_patch(url, **kwargs):
        return _make_response(500, {"message": "Internal Server Error"})

    delete_called = {"value": False}

    async def mock_delete(url, **kwargs):
        delete_called["value"] = True
        return _make_response(200, {})

    mock_client.get = mock_get
    mock_client.patch = mock_patch
    mock_client.delete = mock_delete

    with patch("tools.notion.get_http_client", return_value=mock_client):
        from tools.notion import notion_update_page
        result = json.loads(await notion_update_page(
            token="secret_test",
            page_id="page-123",
            content="새로운 본문",
        ))

    assert "error" in result
    # 새 블록 추가 실패 시 기존 블록 삭제가 호출되지 않아야 한다
    assert delete_called["value"] is False


@pytest.mark.asyncio
async def test_notion_update_page_api_오류(mock_client):
    """제목 수정 시 API 오류가 발생하면 에러를 반환한다."""
    mock_client.patch = AsyncMock(return_value=_make_response(403, {
        "message": "Insufficient permissions."
    }))

    with patch("tools.notion.get_http_client", return_value=mock_client):
        from tools.notion import notion_update_page
        result = json.loads(await notion_update_page(
            token="secret_test",
            page_id="page-123",
            title="새 제목",
        ))

    assert "error" in result
    assert "403" in result["error"]


# ---------------------------------------------------------------------------
# 마크다운 → Notion 블록 변환 (_blocks_from_text / _parse_inline)
# ---------------------------------------------------------------------------

def _block_text(block: dict) -> str:
    """블록 rich_text의 plain content를 합쳐 반환."""
    rich_text = block[block["type"]].get("rich_text", [])
    return "".join(rt["text"]["content"] for rt in rich_text)


def test_headings_conversion():
    from tools.notion import _blocks_from_text
    blocks = _blocks_from_text("# 제목1\n## 제목2\n### 제목3")
    assert [b["type"] for b in blocks] == ["heading_1", "heading_2", "heading_3"]
    assert _block_text(blocks[0]) == "제목1"
    assert _block_text(blocks[2]) == "제목3"


def test_bulleted_and_numbered_list_conversion():
    from tools.notion import _blocks_from_text
    blocks = _blocks_from_text("- 항목A\n* 항목B\n1. 첫째\n2. 둘째")
    assert [b["type"] for b in blocks] == [
        "bulleted_list_item", "bulleted_list_item",
        "numbered_list_item", "numbered_list_item",
    ]
    assert _block_text(blocks[0]) == "항목A"
    assert _block_text(blocks[3]) == "둘째"


def test_todo_checkbox_conversion():
    from tools.notion import _blocks_from_text
    blocks = _blocks_from_text("- [ ] 미완료\n- [x] 완료")
    assert blocks[0]["type"] == "to_do"
    assert blocks[0]["to_do"]["checked"] is False
    assert blocks[1]["to_do"]["checked"] is True


def test_code_block_conversion():
    from tools.notion import _blocks_from_text
    blocks = _blocks_from_text("```python\nprint('hi')\nx = 1\n```")
    assert len(blocks) == 1
    assert blocks[0]["type"] == "code"
    assert blocks[0]["code"]["language"] == "python"
    assert _block_text(blocks[0]) == "print('hi')\nx = 1"


def test_code_block_unsupported_language_falls_back_to_plain_text():
    from tools.notion import _blocks_from_text
    blocks = _blocks_from_text("```없는언어\ncode\n```")
    assert blocks[0]["code"]["language"] == "plain text"


def test_code_block_language_alias_normalized():
    from tools.notion import _blocks_from_text
    blocks = _blocks_from_text("```js\nconst a = 1;\n```")
    assert blocks[0]["code"]["language"] == "javascript"


def test_quote_and_divider_conversion():
    from tools.notion import _blocks_from_text
    blocks = _blocks_from_text("> 인용문\n---")
    assert blocks[0]["type"] == "quote"
    assert _block_text(blocks[0]) == "인용문"
    assert blocks[1]["type"] == "divider"


def test_plain_paragraph_conversion():
    from tools.notion import _blocks_from_text
    blocks = _blocks_from_text("그냥 평범한 문장.")
    assert len(blocks) == 1
    assert blocks[0]["type"] == "paragraph"
    assert _block_text(blocks[0]) == "그냥 평범한 문장."


def test_blank_lines_produce_no_block():
    from tools.notion import _blocks_from_text
    blocks = _blocks_from_text("첫 줄\n\n\n둘째 줄")
    assert [b["type"] for b in blocks] == ["paragraph", "paragraph"]


def test_empty_input_produces_single_empty_paragraph():
    from tools.notion import _blocks_from_text
    blocks = _blocks_from_text("")
    assert len(blocks) == 1
    assert blocks[0]["type"] == "paragraph"
    assert blocks[0]["paragraph"]["rich_text"] == []


def test_inline_bold_and_italic():
    from tools.notion import _parse_inline
    segments = _parse_inline("이건 **굵게** 그리고 *기울임*")
    # 평문 + 볼드 + 평문 + 이탤릭
    assert segments[1]["text"]["content"] == "굵게"
    assert segments[1]["annotations"] == {"bold": True}
    assert segments[3]["text"]["content"] == "기울임"
    assert segments[3]["annotations"] == {"italic": True}


def test_inline_code():
    from tools.notion import _parse_inline
    segments = _parse_inline("값은 `x = 1` 이다")
    assert segments[1]["text"]["content"] == "x = 1"
    assert segments[1]["annotations"] == {"code": True}


def test_inline_link():
    from tools.notion import _parse_inline
    segments = _parse_inline("자세히는 [여기](https://example.com) 참고")
    assert segments[1]["text"]["content"] == "여기"
    assert segments[1]["text"]["link"] == {"url": "https://example.com"}


def test_inline_symbols_inside_code_span_are_protected():
    from tools.notion import _parse_inline
    # 코드 스팬 안의 ** 는 볼드로 해석되지 않아야 함
    segments = _parse_inline("`**не bold**`")
    assert len(segments) == 1
    assert segments[0]["text"]["content"] == "**не bold**"
    assert segments[0]["annotations"] == {"code": True}


def test_segment_split_over_2000_chars():
    from tools.notion import _text_segments
    segments = _text_segments("가" * 4500)
    assert len(segments) == 3  # 2000 + 2000 + 500
    assert all(len(s["text"]["content"]) <= 2000 for s in segments)


def test_inline_formatting_inside_heading():
    from tools.notion import _blocks_from_text
    blocks = _blocks_from_text("# **중요** 제목")
    rich_text = blocks[0]["heading_1"]["rich_text"]
    assert rich_text[0]["text"]["content"] == "중요"
    assert rich_text[0]["annotations"] == {"bold": True}


def test_empty_list_items_match_their_block_type():
    from tools.notion import _blocks_from_text
    blocks = _blocks_from_text("-\n1.\n- [ ]")
    assert [b["type"] for b in blocks] == [
        "bulleted_list_item", "numbered_list_item", "to_do",
    ]
    assert blocks[0]["bulleted_list_item"]["rich_text"] == []
    assert blocks[1]["numbered_list_item"]["rich_text"] == []
    assert blocks[2]["to_do"]["rich_text"] == []
    assert blocks[2]["to_do"]["checked"] is False


# ---------------------------------------------------------------------------
# notion_query_database
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_notion_query_database_성공(mock_client):
    mock_client.post = AsyncMock(return_value=_make_response(200, {
        "results": [
            {
                "id": "row-1",
                "url": "https://notion.so/row-1",
                "properties": {
                    "이름": {"type": "title", "title": [{"plain_text": "홍길동"}]},
                    "상태": {"type": "status", "status": {"name": "진행중"}},
                    "점수": {"type": "number", "number": 90},
                    "태그": {"type": "multi_select", "multi_select": [{"name": "A"}, {"name": "B"}]},
                    "완료": {"type": "checkbox", "checkbox": False},
                },
            }
        ]
    }))

    with patch("tools.notion.get_http_client", return_value=mock_client):
        from tools.notion import notion_query_database
        result = json.loads(await notion_query_database(
            token="secret_test",
            database_id="db-123",
            filter_json='{"property":"상태","status":{"equals":"진행중"}}',
        ))

    assert result["success"] is True
    assert result["total"] == 1
    row = result["rows"][0]
    assert row["id"] == "row-1"
    assert row["properties"]["이름"] == "홍길동"
    assert row["properties"]["상태"] == "진행중"
    assert row["properties"]["점수"] == 90
    assert row["properties"]["태그"] == ["A", "B"]
    assert row["properties"]["완료"] is False


_DS_QUERY = "https://api.notion.com/v1/data_sources/{}/query"


@pytest.mark.asyncio
async def test_notion_query_database_data_source_id는_한번에_조회(mock_client):
    mock_client.post = AsyncMock(return_value=_make_response(200, {"results": []}))
    mock_client.get = AsyncMock()

    with patch("tools.notion.get_http_client", return_value=mock_client):
        from tools.notion import notion_query_database
        result = json.loads(await notion_query_database(token="secret_test", database_id="ds-1"))

    assert result["success"] is True
    assert mock_client.post.call_args.args[0] == _DS_QUERY.format("ds-1")
    assert mock_client.post.call_args.kwargs["headers"]["Notion-Version"] == "2026-03-11"
    mock_client.get.assert_not_called()


@pytest.mark.asyncio
async def test_notion_query_database_옛_database_id는_data_source로_풀어_재시도(mock_client):
    mock_client.post = AsyncMock(side_effect=[
        _make_response(404, {"code": "object_not_found", "message": "Could not find data_source."}),
        _make_response(200, {"results": [{"id": "row-1", "url": "u", "properties": {}}]}),
    ])
    mock_client.get = AsyncMock(return_value=_make_response(200, {
        "object": "database", "id": "db-old",
        "data_sources": [{"id": "ds-9", "name": "업무"}],
    }))

    with patch("tools.notion.get_http_client", return_value=mock_client):
        from tools.notion import notion_query_database
        result = json.loads(await notion_query_database(
            token="secret_test", database_id="db-old",
            filter_json='{"property":"상태","status":{"equals":"진행중"}}', page_size=5,
        ))

    assert result["success"] is True
    assert result["rows"][0]["id"] == "row-1"
    assert mock_client.get.call_args.args[0] == "https://api.notion.com/v1/databases/db-old"
    calls = mock_client.post.call_args_list
    assert [c.args[0] for c in calls] == [_DS_QUERY.format("db-old"), _DS_QUERY.format("ds-9")]
    # 재시도에도 필터·page_size가 그대로 간다
    assert calls[1].kwargs["json"] == {
        "page_size": 5,
        "result_type": "page",
        "filter": {"property": "상태", "status": {"equals": "진행중"}},
    }


@pytest.mark.asyncio
async def test_notion_query_database_다중_data_source는_목록을_에러로(mock_client):
    mock_client.post = AsyncMock(return_value=_make_response(404, {"message": "Could not find data_source."}))
    mock_client.get = AsyncMock(return_value=_make_response(200, {
        "object": "database", "id": "db-old",
        "data_sources": [{"id": "ds-1", "name": "2025"}, {"id": "ds-2", "name": "2026"}],
    }))

    with patch("tools.notion.get_http_client", return_value=mock_client):
        from tools.notion import notion_query_database
        result = json.loads(await notion_query_database(token="secret_test", database_id="db-old"))

    assert "error" in result
    assert result["dataSources"] == [{"id": "ds-1", "name": "2025"}, {"id": "ds-2", "name": "2026"}]
    assert mock_client.post.call_count == 1  # 자동으로 하나를 골라 조회하지 않는다


@pytest.mark.asyncio
async def test_notion_query_database_data_source가_없는_DB는_에러(mock_client):
    mock_client.post = AsyncMock(return_value=_make_response(404, {"message": "Could not find data_source."}))
    mock_client.get = AsyncMock(return_value=_make_response(200, {
        "object": "database", "id": "db-old", "data_sources": [],
    }))

    with patch("tools.notion.get_http_client", return_value=mock_client):
        from tools.notion import notion_query_database
        result = json.loads(await notion_query_database(token="secret_test", database_id="db-old"))

    assert "error" in result
    assert "404" not in result["error"]
    mock_client.get.assert_called_once()
    assert mock_client.post.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"object": "database", "id": "db-old"},
    {"object": "database", "id": "db-old", "data_sources": None},
])
async def test_notion_query_database_data_sources_키_없거나_null이면_원래_404(mock_client, body):
    mock_client.post = AsyncMock(return_value=_make_response(404, {"message": "Could not find data_source."}))
    mock_client.get = AsyncMock(return_value=_make_response(200, body))

    with patch("tools.notion.get_http_client", return_value=mock_client):
        from tools.notion import notion_query_database
        result = json.loads(await notion_query_database(token="secret_test", database_id="db-old"))

    assert "error" in result
    assert "404" in result["error"]
    assert mock_client.post.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("get_status", [404, 500])
async def test_notion_query_database_database로도_못_찾으면_원래_404(mock_client, get_status):
    mock_client.post = AsyncMock(return_value=_make_response(404, {"message": "Could not find data_source."}))
    mock_client.get = AsyncMock(return_value=_make_response(get_status, {"message": "x"}))

    with patch("tools.notion.get_http_client", return_value=mock_client):
        from tools.notion import notion_query_database
        result = json.loads(await notion_query_database(token="secret_test", database_id="db-missing"))

    assert "error" in result
    assert "404" in result["error"]
    mock_client.get.assert_called_once()
    assert mock_client.post.call_count == 1


@pytest.mark.asyncio
async def test_notion_query_database_filter_json_파싱오류(mock_client):
    with patch("tools.notion.get_http_client", return_value=mock_client):
        from tools.notion import notion_query_database
        result = json.loads(await notion_query_database(
            token="secret_test",
            database_id="db-123",
            filter_json="{invalid json}",
        ))

    assert "error" in result
    assert "파싱 오류" in result["error"]


@pytest.mark.asyncio
async def test_notion_query_database_filter_dict_와_빈문자(mock_client):
    """filter가 dict로 오면 그대로 전송되고, 빈/공백 문자열은 무시(파싱 시도 없음)된다."""
    captured = {}

    async def _post(url, **kwargs):
        captured["payload"] = kwargs.get("json")
        return _make_response(200, {"results": []})

    mock_client.post = AsyncMock(side_effect=_post)
    with patch("tools.notion.get_http_client", return_value=mock_client):
        from tools.notion import notion_query_database
        # dict 필터
        r1 = json.loads(await notion_query_database(
            token="secret_test", database_id="db-1",
            filter_json={"property": "상태", "status": {"equals": "진행중"}},
        ))
        assert r1["success"] is True
        assert captured["payload"]["filter"] == {"property": "상태", "status": {"equals": "진행중"}}
        # 공백 문자열 → filter 미포함, 에러 없음
        r2 = json.loads(await notion_query_database(
            token="secret_test", database_id="db-1", filter_json="   ",
        ))
        assert r2["success"] is True
        assert "filter" not in captured["payload"]


@pytest.mark.asyncio
async def test_notion_query_database_결과의_data_source_객체는_건너뜀(mock_client):
    """위키 data source는 query 결과에 data_source 객체가 섞인다 — 스키마 properties라 행이 아니다."""
    mock_client.post = AsyncMock(return_value=_make_response(200, {"results": [
        {"object": "page", "id": "row-1", "url": "https://notion.so/row-1",
         "properties": {"이름": {"type": "title", "title": [{"plain_text": "홍길동"}]}}},
        {"object": "data_source", "id": "ds-x",
         "properties": {"Tags": {"type": "multi_select", "multi_select": {"options": [{"name": "a"}]}}}},
    ]}))

    with patch("tools.notion.get_http_client", return_value=mock_client):
        from tools.notion import notion_query_database
        result = json.loads(await notion_query_database(token="secret_test", database_id="ds-1"))

    assert result["success"] is True
    assert [r["id"] for r in result["rows"]] == ["row-1"]


def test_config_바인딩되면_두_줄_설명도_통째로_빠진다():
    """_make_partial은 바인딩된 파라미터 설명을 첫 줄만 지운다 — 설명이 한 줄이어야 잔여가 없다."""
    from tools import _bind_config
    from tools.notion import notion_query_database, notion_search

    assert "옛 데이터베이스" not in _bind_config(notion_query_database, {"database_id": "x"}).__doc__
    assert "notion_query_database의 database_id로" not in _bind_config(notion_search, {"filter_type": "page"}).__doc__

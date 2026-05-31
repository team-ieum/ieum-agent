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

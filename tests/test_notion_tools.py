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

    with patch("tools.notion.httpx.AsyncClient", return_value=mock_client):
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

    with patch("tools.notion.httpx.AsyncClient", return_value=mock_client):
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

    with patch("tools.notion.httpx.AsyncClient", return_value=mock_client):
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

    with patch("tools.notion.httpx.AsyncClient", return_value=mock_client):
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

    with patch("tools.notion.httpx.AsyncClient", return_value=mock_client):
        from tools.notion import notion_append_block
        result = json.loads(await notion_append_block(
            token="secret_test",
            page_id="page-123",
            content="추가할 내용",
        ))

    assert result["success"] is True
    assert result["appendedCount"] == 2
    assert "block-1" in result["blockIds"]

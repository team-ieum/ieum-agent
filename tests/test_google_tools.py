import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from common.error_code import ToolErrorCode
from tools.google_sheets import google_sheets_read, google_sheets_write
from tools.google_calendar import google_calendar_create, google_calendar_list
from tools.google_drive import google_drive_read, google_drive_upload


def _make_mock_response(status_code: int, json_data: dict) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status_code
    mock.is_success = 200 <= status_code < 300
    mock.json = MagicMock(return_value=json_data)
    mock.content = json.dumps(json_data).encode()
    return mock


def _make_async_client(get_mock=None, post_mock=None, put_mock=None, patch_mock=None):
    mock_client = AsyncMock()
    if get_mock:
        mock_client.get = get_mock
    if post_mock:
        mock_client.post = post_mock
    if put_mock:
        mock_client.put = put_mock
    if patch_mock:
        mock_client.patch = patch_mock
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


# ---------------------------------------------------------------------------
# Google Sheets Read
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_sheets_read_success():
    resp = _make_mock_response(200, {
        "range": "Sheet1!A1:B2",
        "values": [["이름", "점수"], ["홍길동", "100"]],
    })
    client = _make_async_client(get_mock=AsyncMock(return_value=resp))

    with patch("tools.google_sheets.get_http_client", return_value=client):
        result = await google_sheets_read(
            access_token="token",
            spreadsheet_id="spread-1",
            cell_range="Sheet1!A1:B2",
        )

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert len(parsed["values"]) == 2


@pytest.mark.asyncio
async def test_google_sheets_read_api_error():
    resp = _make_mock_response(401, {"error": {"message": "Unauthorized"}})
    client = _make_async_client(get_mock=AsyncMock(return_value=resp))

    with patch("tools.google_sheets.get_http_client", return_value=client):
        result = await google_sheets_read(
            access_token="bad-token",
            spreadsheet_id="spread-1",
            cell_range="Sheet1!A1:B2",
        )

    parsed = json.loads(result)
    assert "error" in parsed


# ---------------------------------------------------------------------------
# Google Sheets Write
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_sheets_write_success():
    resp = _make_mock_response(200, {
        "updatedRange": "Sheet1!A1:B2",
        "updatedCells": 4,
    })
    client = _make_async_client(put_mock=AsyncMock(return_value=resp))

    with patch("tools.google_sheets.get_http_client", return_value=client):
        result = await google_sheets_write(
            access_token="token",
            spreadsheet_id="spread-1",
            cell_range="Sheet1!A1",
            values='[["이름","점수"],["홍길동","100"]]',
        )

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["updatedCells"] == 4


@pytest.mark.asyncio
async def test_google_sheets_write_invalid_json():
    result = await google_sheets_write(
        access_token="token",
        spreadsheet_id="spread-1",
        cell_range="Sheet1!A1",
        values="not-valid-json",
    )

    parsed = json.loads(result)
    assert "error" in parsed
    assert ToolErrorCode.EXECUTION_FAILED.message in parsed["error"]


# ---------------------------------------------------------------------------
# Google Calendar Create
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_calendar_create_success():
    resp = _make_mock_response(200, {
        "id": "event-123",
        "htmlLink": "https://calendar.google.com/event?eid=abc",
    })
    client = _make_async_client(post_mock=AsyncMock(return_value=resp))

    with patch("tools.google_calendar.get_http_client", return_value=client):
        result = await google_calendar_create(
            access_token="token",
            summary="회의",
            start_datetime="2026-05-14T09:00:00+09:00",
            end_datetime="2026-05-14T10:00:00+09:00",
        )

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["eventId"] == "event-123"


@pytest.mark.asyncio
async def test_google_calendar_create_201_success():
    resp = _make_mock_response(201, {
        "id": "event-456",
        "htmlLink": "https://calendar.google.com/event?eid=def",
    })
    client = _make_async_client(post_mock=AsyncMock(return_value=resp))

    with patch("tools.google_calendar.get_http_client", return_value=client):
        result = await google_calendar_create(
            access_token="token",
            summary="세미나",
            start_datetime="2026-05-15T14:00:00+09:00",
            end_datetime="2026-05-15T16:00:00+09:00",
        )

    parsed = json.loads(result)
    assert parsed["success"] is True


# ---------------------------------------------------------------------------
# Google Calendar List
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_calendar_list_success():
    resp = _make_mock_response(200, {
        "items": [{
            "id": "e1",
            "summary": "회의",
            "start": {"dateTime": "2026-05-14T09:00:00+09:00"},
            "end": {"dateTime": "2026-05-14T10:00:00+09:00"},
        }],
    })
    client = _make_async_client(get_mock=AsyncMock(return_value=resp))

    with patch("tools.google_calendar.get_http_client", return_value=client):
        result = await google_calendar_list(
            access_token="token",
            time_min="2026-05-14T00:00:00+09:00",
            time_max="2026-05-14T23:59:59+09:00",
        )

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["total"] == 1
    assert parsed["events"][0]["summary"] == "회의"


@pytest.mark.asyncio
async def test_google_calendar_list_exception():
    client = _make_async_client(
        get_mock=AsyncMock(side_effect=httpx.TimeoutException("timeout")),
    )

    with patch("tools.google_calendar.get_http_client", return_value=client):
        result = await google_calendar_list(
            access_token="token",
            time_min="2026-05-14T00:00:00+09:00",
            time_max="2026-05-14T23:59:59+09:00",
        )

    parsed = json.loads(result)
    assert "error" in parsed
    assert ToolErrorCode.EXECUTION_FAILED.message in parsed["error"]


# ---------------------------------------------------------------------------
# Google Drive Read
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_drive_read_plain_text_success():
    meta_resp = _make_mock_response(200, {"name": "test.txt", "mimeType": "text/plain"})
    content_resp = MagicMock()
    content_resp.status_code = 200
    content_resp.is_success = True
    content_resp.content = b"hello world"

    get_mock = AsyncMock(side_effect=[meta_resp, content_resp])
    client = _make_async_client(get_mock=get_mock)

    with patch("tools.google_drive.get_http_client", return_value=client):
        result = await google_drive_read(access_token="token", file_id="file-1")

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["content"] == "hello world"


@pytest.mark.asyncio
async def test_google_drive_read_google_doc_export():
    meta_resp = _make_mock_response(200, {
        "name": "doc.gdoc",
        "mimeType": "application/vnd.google-apps.document",
    })
    export_resp = MagicMock()
    export_resp.status_code = 200
    export_resp.is_success = True
    export_resp.content = b"exported text"

    get_mock = AsyncMock(side_effect=[meta_resp, export_resp])
    client = _make_async_client(get_mock=get_mock)

    with patch("tools.google_drive.get_http_client", return_value=client):
        result = await google_drive_read(access_token="token", file_id="file-2")

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["mimeType"] == "text/plain"


@pytest.mark.asyncio
async def test_google_drive_read_binary_rejected():
    meta_resp = _make_mock_response(200, {"name": "image.png", "mimeType": "image/png"})
    get_mock = AsyncMock(return_value=meta_resp)
    client = _make_async_client(get_mock=get_mock)

    with patch("tools.google_drive.get_http_client", return_value=client):
        result = await google_drive_read(access_token="token", file_id="file-3")

    parsed = json.loads(result)
    assert "error" in parsed
    assert "지원하지 않는 파일 형식" in parsed["error"]


# ---------------------------------------------------------------------------
# Google Drive Upload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_drive_upload_success():
    resp = _make_mock_response(200, {
        "id": "file-123",
        "name": "test.txt",
        "webViewLink": "https://drive.google.com/file/d/file-123/view",
    })
    client = _make_async_client(post_mock=AsyncMock(return_value=resp))

    with patch("tools.google_drive.get_http_client", return_value=client):
        result = await google_drive_upload(
            access_token="token",
            name="test.txt",
            content="hello world",
        )

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["fileId"] == "file-123"


@pytest.mark.asyncio
async def test_google_drive_upload_exception():
    client = _make_async_client(
        post_mock=AsyncMock(side_effect=httpx.TimeoutException("timeout")),
    )

    with patch("tools.google_drive.get_http_client", return_value=client):
        result = await google_drive_upload(
            access_token="token",
            name="test.txt",
            content="hello world",
        )

    parsed = json.loads(result)
    assert "error" in parsed
    assert ToolErrorCode.EXECUTION_FAILED.message in parsed["error"]


# ---------------------------------------------------------------------------
# Google Calendar Update
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_calendar_update_success():
    from tools.google_calendar import google_calendar_update

    resp = _make_mock_response(200, {
        "id": "event-123",
        "htmlLink": "https://calendar.google.com/event?eid=abc",
    })
    patch_mock = AsyncMock(return_value=resp)
    client = _make_async_client(patch_mock=patch_mock)

    with patch("tools.google_calendar.get_http_client", return_value=client):
        result = await google_calendar_update(
            access_token="token",
            event_id="event-123",
            summary="변경된 회의",
            start_datetime="2026-06-11T15:00:00+09:00",
            end_datetime="2026-06-11T16:00:00+09:00",
        )

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["eventId"] == "event-123"
    # 전달하지 않은 description은 payload에 포함되지 않아야 함
    sent_payload = patch_mock.call_args.kwargs["json"]
    assert "description" not in sent_payload
    assert sent_payload["summary"] == "변경된 회의"


@pytest.mark.asyncio
async def test_google_calendar_update_error():
    from tools.google_calendar import google_calendar_update

    resp = _make_mock_response(404, {"error": {"message": "Not Found"}})
    client = _make_async_client(patch_mock=AsyncMock(return_value=resp))

    with patch("tools.google_calendar.get_http_client", return_value=client):
        result = await google_calendar_update(
            access_token="token",
            event_id="missing",
            summary="x",
        )

    parsed = json.loads(result)
    assert "error" in parsed
    assert "404" in parsed["error"]


# ---------------------------------------------------------------------------
# Google Sheets Append
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_sheets_append_success():
    from tools.google_sheets import google_sheets_append

    resp = _make_mock_response(200, {
        "updates": {"updatedRange": "Sheet1!A2:B2", "updatedRows": 1}
    })
    post_mock = AsyncMock(return_value=resp)
    client = _make_async_client(post_mock=post_mock)

    with patch("tools.google_sheets.get_http_client", return_value=client):
        result = await google_sheets_append(
            access_token="token",
            spreadsheet_id="spread-1",
            cell_range="Sheet1!A:B",
            values='[["홍길동","100"]]',
        )

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["updatedRows"] == 1
    # append 엔드포인트로 호출되었는지 확인
    called_url = post_mock.call_args.args[0]
    assert called_url.endswith(":append")


@pytest.mark.asyncio
async def test_google_sheets_append_invalid_json():
    from tools.google_sheets import google_sheets_append

    result = await google_sheets_append(
        access_token="token",
        spreadsheet_id="spread-1",
        cell_range="Sheet1!A:B",
        values="not-valid-json",
    )

    parsed = json.loads(result)
    assert "error" in parsed
    assert ToolErrorCode.EXECUTION_FAILED.message in parsed["error"]


@pytest.mark.asyncio
async def test_google_sheets_append_error():
    from tools.google_sheets import google_sheets_append

    resp = _make_mock_response(403, {"error": {"message": "Permission denied"}})
    client = _make_async_client(post_mock=AsyncMock(return_value=resp))

    with patch("tools.google_sheets.get_http_client", return_value=client):
        result = await google_sheets_append(
            access_token="token",
            spreadsheet_id="spread-1",
            cell_range="Sheet1!A:B",
            values='[["x"]]',
        )

    parsed = json.loads(result)
    assert "error" in parsed
    assert "403" in parsed["error"]

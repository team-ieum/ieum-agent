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


@pytest.mark.asyncio
async def test_google_sheets_write_accepts_list_values():
    """values가 이미 list로 와도 예외 없이 그대로 전송된다(append와 같은 처리)."""
    resp = _make_mock_response(200, {"updatedCells": 2})
    put_mock = AsyncMock(return_value=resp)
    client = _make_async_client(put_mock=put_mock)

    with patch("tools.google_sheets.get_http_client", return_value=client):
        result = await google_sheets_write(
            access_token="token",
            spreadsheet_id="spread-1",
            cell_range="Sheet1!A1",
            values=[["홍길동", "100"]],
        )

    assert json.loads(result)["success"] is True
    assert put_mock.call_args.kwargs["json"]["values"] == [["홍길동", "100"]]


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
    # 공유 드라이브 파일은 supportsAllDrives 없이 404 — 메타 조회·본문 다운로드 둘 다 실어야 한다
    meta_call, media_call = get_mock.call_args_list
    assert meta_call.kwargs["params"]["supportsAllDrives"] == "true"
    assert media_call.kwargs["params"]["supportsAllDrives"] == "true"


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
    # files.export는 supportsAllDrives 파라미터가 없다(mimeType뿐)
    meta_call, export_call = get_mock.call_args_list
    assert meta_call.kwargs["params"]["supportsAllDrives"] == "true"
    assert "supportsAllDrives" not in export_call.kwargs["params"]


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
    post_mock = AsyncMock(return_value=resp)
    client = _make_async_client(post_mock=post_mock)

    with patch("tools.google_drive.get_http_client", return_value=client):
        result = await google_drive_upload(
            access_token="token",
            name="test.txt",
            content="hello world",
        )

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["fileId"] == "file-123"
    # 공유 드라이브 폴더에 올리려면 files.create에도 supportsAllDrives가 필요하다
    assert post_mock.call_args.kwargs["params"]["supportsAllDrives"] == "true"


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
async def test_google_calendar_update_skips_empty_datetime():
    """빈 문자열 start/end는 payload에서 제외되어야 한다(빈 dateTime → 400 방지)."""
    from tools.google_calendar import google_calendar_update

    resp = _make_mock_response(200, {"id": "event-123", "htmlLink": "x"})
    patch_mock = AsyncMock(return_value=resp)
    client = _make_async_client(patch_mock=patch_mock)

    with patch("tools.google_calendar.get_http_client", return_value=client):
        await google_calendar_update(
            access_token="token",
            event_id="event-123",
            summary="제목만 변경",
            start_datetime="",
            end_datetime="   ",
        )

    sent_payload = patch_mock.call_args.kwargs["json"]
    assert "start" not in sent_payload
    assert "end" not in sent_payload
    assert sent_payload["summary"] == "제목만 변경"


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
# Google Calendar — calendar_id 경로 인코딩 (IEUM-AI-62)
# ---------------------------------------------------------------------------

_HOLIDAY_CALENDAR = "ko.south_korea#holiday@group.v.calendar.google.com"
_HOLIDAY_ENCODED = "ko.south_korea%23holiday%40group.v.calendar.google.com"
_CALENDAR_BASE = "https://www.googleapis.com/calendar/v3/calendars/"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, verb, kwargs, suffix", [
    ("google_calendar_create", "post",
     {"summary": "회의", "start_datetime": "2026-10-07T09:00:00+09:00",
      "end_datetime": "2026-10-07T10:00:00+09:00"}, "/events"),
    ("google_calendar_list", "get",
     {"time_min": "2026-10-01T00:00:00+09:00", "time_max": "2026-10-31T23:59:59+09:00"}, "/events"),
    ("google_calendar_update", "patch", {"event_id": "evt-1", "summary": "변경"}, "/events/evt-1"),
])
async def test_google_calendar_calendar_id는_경로에_인코딩(tool, verb, kwargs, suffix):
    """드롭다운이 주는 공휴일 캘린더 id의 #가 fragment로 잘리지 않아야 한다."""
    import tools.google_calendar as gc

    call = AsyncMock(return_value=_make_mock_response(200, {"id": "evt-1", "htmlLink": "x", "items": []}))
    client = _make_async_client(**{f"{verb}_mock": call})

    with patch("tools.google_calendar.get_http_client", return_value=client):
        result = json.loads(await getattr(gc, tool)(
            access_token="token", calendar_id=_HOLIDAY_CALENDAR, **kwargs))

    assert "error" not in result
    assert call.call_args.args[0] == f"{_CALENDAR_BASE}{_HOLIDAY_ENCODED}{suffix}"


@pytest.mark.asyncio
async def test_google_calendar_기본_primary_경로_그대로():
    call = AsyncMock(return_value=_make_mock_response(200, {"items": []}))
    client = _make_async_client(get_mock=call)

    with patch("tools.google_calendar.get_http_client", return_value=client):
        await google_calendar_list(
            access_token="token",
            time_min="2026-10-01T00:00:00+09:00",
            time_max="2026-10-31T23:59:59+09:00",
        )

    assert call.call_args.args[0] == f"{_CALENDAR_BASE}primary/events"


async def _calendar_list_url(calendar_id) -> str:
    call = AsyncMock(return_value=_make_mock_response(200, {"items": []}))
    client = _make_async_client(get_mock=call)
    with patch("tools.google_calendar.get_http_client", return_value=client):
        await google_calendar_list(
            access_token="token",
            time_min="2026-10-01T00:00:00+09:00",
            time_max="2026-10-31T23:59:59+09:00",
            calendar_id=calendar_id,
        )
    return call.call_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("calendar_id", ["", "  ", None])
async def test_google_calendar_빈_calendar_id는_primary(calendar_id):
    """tools[].config에 calendar_id=""가 고정돼도 /calendars//events 404가 아니라 기본 캘린더."""
    assert await _calendar_list_url(calendar_id) == f"{_CALENDAR_BASE}primary/events"


@pytest.mark.asyncio
async def test_google_calendar_이미_인코딩된_calendar_id_이중_인코딩_안함():
    """임베드 링크에서 복사한 %23·%40 id가 %2523으로 두 번 인코딩되면 404."""
    url = await _calendar_list_url(_HOLIDAY_ENCODED)
    assert url == f"{_CALENDAR_BASE}{_HOLIDAY_ENCODED}/events"
    assert "%25" not in url


@pytest.mark.asyncio
async def test_google_calendar_update_event_id_경로_인코딩():
    from tools.google_calendar import google_calendar_update

    call = AsyncMock(return_value=_make_mock_response(200, {"id": "x", "htmlLink": "x"}))
    client = _make_async_client(patch_mock=call)
    with patch("tools.google_calendar.get_http_client", return_value=client):
        await google_calendar_update(access_token="token", event_id="a/b?x=1", summary="변경")

    assert call.call_args.args[0] == f"{_CALENDAR_BASE}primary/events/a%2Fb%3Fx%3D1"


@pytest.mark.asyncio
async def test_google_calendar_숫자_calendar_id도_경로로():
    assert await _calendar_list_url(123) == f"{_CALENDAR_BASE}123/events"


@pytest.mark.asyncio
@pytest.mark.parametrize("event_id", ["", "  "])
async def test_google_calendar_update_빈_event_id는_호출_없이_에러(event_id):
    """빈 event_id면 PATCH .../events/(컬렉션 경로)로 나가 원인 모를 404/405가 난다."""
    from tools.google_calendar import google_calendar_update

    patch_mock = AsyncMock()
    client = _make_async_client(patch_mock=patch_mock)
    with patch("tools.google_calendar.get_http_client", return_value=client):
        result = await google_calendar_update(access_token="token", event_id=event_id, summary="변경")

    assert ToolErrorCode.EXECUTION_FAILED.message in json.loads(result)["error"]
    patch_mock.assert_not_called()


@pytest.mark.asyncio
async def test_google_drive_upload_공백_folder_id는_루트():
    post_mock = AsyncMock(return_value=_make_mock_response(200, {"id": "f", "name": "n", "webViewLink": "x"}))
    client = _make_async_client(post_mock=post_mock)
    with patch("tools.google_drive.get_http_client", return_value=client):
        await google_drive_upload(access_token="token", name="n.txt", content="hi", folder_id="  ")

    metadata = json.loads(post_mock.call_args.kwargs["content"].decode().split("\r\n")[3])
    assert "parents" not in metadata


@pytest.mark.asyncio
async def test_google_drive_read_file_id_경로_인코딩():
    meta_resp = _make_mock_response(200, {"name": "test.txt", "mimeType": "text/plain"})
    content_resp = MagicMock()
    content_resp.is_success = True
    content_resp.content = b"hello"
    get_mock = AsyncMock(side_effect=[meta_resp, content_resp])
    client = _make_async_client(get_mock=get_mock)

    with patch("tools.google_drive.get_http_client", return_value=client):
        await google_drive_read(access_token="token", file_id="a/b")

    meta_call, media_call = get_mock.call_args_list
    assert meta_call.args[0] == "https://www.googleapis.com/drive/v3/files/a%2Fb"
    assert media_call.args[0] == "https://www.googleapis.com/drive/v3/files/a%2Fb"


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
async def test_google_sheets_append_accepts_list_values():
    """values가 이미 list로 전달되어도 TypeError 없이 그대로 전송된다."""
    from tools.google_sheets import google_sheets_append

    resp = _make_mock_response(200, {"updates": {"updatedRange": "Sheet1!A2:B2", "updatedRows": 1}})
    post_mock = AsyncMock(return_value=resp)
    client = _make_async_client(post_mock=post_mock)

    with patch("tools.google_sheets.get_http_client", return_value=client):
        result = await google_sheets_append(
            access_token="token",
            spreadsheet_id="spread-1",
            cell_range="Sheet1!A:B",
            values=[["홍길동", "100"]],
        )

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert post_mock.call_args.kwargs["json"]["values"] == [["홍길동", "100"]]


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


# ---------------------------------------------------------------------------
# Sheets 대상 고정(sheet_name) · 빈 ID 차단 · range 인코딩 (IEUM-AI-60)
# ---------------------------------------------------------------------------
import inspect
from urllib.parse import unquote

from tools import get_tools_for_request
from tools.google_sheets import google_sheets_append


def _range_in_url(url: str) -> str:
    """values/ 뒤의 경로 조각(append면 ':append' 앞)을 디코딩한 A1 범위."""
    tail = url.rsplit("/values/", 1)[1]
    return unquote(tail.removesuffix(":append"))


@pytest.mark.asyncio
@pytest.mark.parametrize("cell_range, sheet_name, expected", [
    ("Sheet1!A1:B2", "Sales", "'Sales'!A1:B2"),   # LLM이 붙인 시트보다 고정값이 이긴다
    ("A1:B2", "Sales", "'Sales'!A1:B2"),
    ("'a!b'!A:C", "Sales", "'Sales'!A:C"),        # 시트 부분에 '!'가 있어도 마지막 '!' 뒤만 범위
    ("Sheet1!", "Sales", "'Sales'"),              # 범위가 비면 시트 전체
    ("", "Sales", "'Sales'"),
    ("A:B", "Bob's", "'Bob''s'!A:B"),             # 작은따옴표 이스케이프
    ("Sheet1!A1:B2", None, "Sheet1!A1:B2"),       # 미지정이면 기존대로
    ("A:B", 2024, "'2024'!A:B"),                  # 숫자로 고정된 연도별 탭도 문자열로 쓴다
])
async def test_sheets_sheet_name_replaces_sheet_part(cell_range, sheet_name, expected):
    resp = _make_mock_response(200, {"values": []})
    get_mock = AsyncMock(return_value=resp)
    client = _make_async_client(get_mock=get_mock)

    with patch("tools.google_sheets.get_http_client", return_value=client):
        result = await google_sheets_read(
            access_token="token", spreadsheet_id="spread-1",
            cell_range=cell_range, sheet_name=sheet_name,
        )

    assert json.loads(result)["success"] is True
    assert _range_in_url(get_mock.call_args.args[0]) == expected


@pytest.mark.asyncio
async def test_sheets_numeric_spreadsheet_id_used_as_string():
    resp = _make_mock_response(200, {"values": []})
    get_mock = AsyncMock(return_value=resp)
    client = _make_async_client(get_mock=get_mock)

    with patch("tools.google_sheets.get_http_client", return_value=client):
        result = await google_sheets_read(
            access_token="token", spreadsheet_id=12345, cell_range="A1:B2",
        )

    assert json.loads(result)["success"] is True
    assert "/spreadsheets/12345/values/" in get_mock.call_args.args[0]

@pytest.mark.asyncio
async def test_sheets_reserved_chars_encoded():
    """탭 이름의 '/', '#', '?'가 경로·프래그먼트·쿼리로 해석되지 않는다."""
    resp = _make_mock_response(200, {"updates": {"updatedRows": 1}})
    post_mock = AsyncMock(return_value=resp)
    client = _make_async_client(post_mock=post_mock)

    with patch("tools.google_sheets.get_http_client", return_value=client):
        await google_sheets_append(
            access_token="token", spreadsheet_id="spread-1",
            cell_range="A1", values='[["x"]]', sheet_name="a/b#c?d",
        )

    url = post_mock.call_args.args[0]
    tail = url.rsplit("/values/", 1)[1]
    assert url.endswith(":append")
    assert not any(ch in tail for ch in "/#?")
    assert _range_in_url(url) == "'a/b#c?d'!A1"


@pytest.mark.asyncio
async def test_sheets_append_payload_range_matches_url():
    resp = _make_mock_response(200, {"updates": {"updatedRows": 1}})
    post_mock = AsyncMock(return_value=resp)
    client = _make_async_client(post_mock=post_mock)

    with patch("tools.google_sheets.get_http_client", return_value=client):
        await google_sheets_append(
            access_token="token", spreadsheet_id="spread-1",
            cell_range="Sheet1!A:B", values='[["x"]]', sheet_name="Sales",
        )

    assert post_mock.call_args.kwargs["json"]["range"] == "'Sales'!A:B"
    assert _range_in_url(post_mock.call_args.args[0]) == "'Sales'!A:B"


@pytest.mark.asyncio
async def test_sheets_write_uses_sheet_name():
    resp = _make_mock_response(200, {"updatedCells": 1})
    put_mock = AsyncMock(return_value=resp)
    client = _make_async_client(put_mock=put_mock)

    with patch("tools.google_sheets.get_http_client", return_value=client):
        await google_sheets_write(
            access_token="token", spreadsheet_id="spread-1",
            cell_range="A1", values='[["x"]]', sheet_name="Sales",
        )

    assert put_mock.call_args.kwargs["json"]["range"] == "'Sales'!A1"
    assert _range_in_url(put_mock.call_args.args[0]) == "'Sales'!A1"


_SHEETS_TOOLS = {
    "read": (google_sheets_read, {}),
    "append": (google_sheets_append, {"values": '[["x"]]'}),
    "write": (google_sheets_write, {"values": '[["x"]]'}),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["read", "append", "write"])
@pytest.mark.parametrize("spreadsheet_id, sheet_name", [
    ("", None), ("   ", None),          # 미해결 참조식은 ""로 치환돼 온다
    ("spread-1", ""), ("spread-1", "  "),
])
async def test_sheets_blank_target_returns_error_without_http(tool, spreadsheet_id, sheet_name):
    fn, extra = _SHEETS_TOOLS[tool]

    with patch("tools.google_sheets.get_http_client") as factory:
        result = await fn(
            access_token="token", spreadsheet_id=spreadsheet_id,
            cell_range="A1", sheet_name=sheet_name, **extra,
        )

    parsed = json.loads(result)
    assert ToolErrorCode.EXECUTION_FAILED.message in parsed["error"]
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("cell_range, sheet_name", [
    ("", "매출"),          # 미해결 참조식 "" + 고정 시트 → 시트 전체를 A1부터 덮어쓰게 된다
    (None, "매출"),        # config의 null도 그대로 바인딩된다
    ("Sheet1!", None),
    ("  ", None),
])
async def test_sheets_write_blank_range_returns_error_without_http(cell_range, sheet_name):
    with patch("tools.google_sheets.get_http_client") as factory:
        result = await google_sheets_write(
            access_token="token", spreadsheet_id="spread-1",
            cell_range=cell_range, values='[["x"]]', sheet_name=sheet_name,
        )

    assert "cell_range가 비어 있습니다" in json.loads(result)["error"]
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_sheets_write_non_str_range_does_not_raise():
    resp = _make_mock_response(200, {"updatedCells": 1})
    client = _make_async_client(put_mock=AsyncMock(return_value=resp))

    with patch("tools.google_sheets.get_http_client", return_value=client):
        result = await google_sheets_write(
            access_token="token", spreadsheet_id="spread-1",
            cell_range=5, values='[["x"]]',
        )

    parsed = json.loads(result)
    assert "error" in parsed or parsed.get("success") is True


@pytest.mark.asyncio
async def test_sheets_non_str_range_with_sheet_name_does_not_raise():
    get_mock = AsyncMock(return_value=_make_mock_response(200, {"values": []}))
    client = _make_async_client(get_mock=get_mock)

    with patch("tools.google_sheets.get_http_client", return_value=client):
        result = await google_sheets_read(
            access_token="token", spreadsheet_id="spread-1",
            cell_range=5, sheet_name="매출",
        )

    assert json.loads(result)["success"] is True
    assert _range_in_url(get_mock.call_args.args[0]) == "'매출'!5"


@pytest.mark.asyncio
async def test_bound_sheet_target_hidden_from_llm():
    """tools[0].config의 spreadsheet_id·sheet_name은 고정 바인딩되고 _names는 무시된다.
    config가 없는 기존 노드는 두 인자가 LLM에 그대로 노출된다."""
    [bound] = get_tools_for_request([{
        "name": "builtin:google_sheets_append",
        "config": {"spreadsheet_id": "abc", "sheet_name": "Sales",
                   "_names": {"spreadsheet_id": "2026 매출 장부"}},
    }])
    params = inspect.signature(bound.func).parameters
    assert "spreadsheet_id" not in params and "sheet_name" not in params
    assert "_names" not in params
    assert {"cell_range", "values"} <= set(params)
    assert "sheet_name:" not in (bound.func.__doc__ or "")

    resp = _make_mock_response(200, {"updates": {"updatedRows": 1}})
    post_mock = AsyncMock(return_value=resp)
    client = _make_async_client(post_mock=post_mock)
    with patch("tools.google_sheets.get_http_client", return_value=client):
        await bound.func(access_token="token", cell_range="Sheet1!A:B", values='[["x"]]')
    url = post_mock.call_args.args[0]
    assert "/spreadsheets/abc/values/" in url
    assert _range_in_url(url) == "'Sales'!A:B"

    [legacy] = get_tools_for_request([{"name": "builtin:google_sheets_append"}])
    assert {"spreadsheet_id", "sheet_name"} <= set(inspect.signature(legacy.func).parameters)

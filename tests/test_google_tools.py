import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from common.error_code import ToolErrorCode
from tools.google_calendar import google_calendar_create, google_calendar_find
from tools.google_drive import google_drive_read, google_drive_upload
from tests.action_outputs import assert_matches_outputs


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

    assert_matches_outputs("google_calendar.create", result)
    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["eventId"] == "event-123"


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

    assert_matches_outputs("google_drive.read", result)
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

    assert_matches_outputs("google_drive.upload", result)
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
    current = _make_mock_response(200, {"id": "event-123",
                                        "start": {"dateTime": "2026-06-11T10:00:00+09:00"},
                                        "end": {"dateTime": "2026-06-11T11:00:00+09:00"}})
    client = _make_async_client(get_mock=AsyncMock(return_value=current), patch_mock=patch_mock)

    with patch("tools.google_calendar.get_http_client", return_value=client):
        result = await google_calendar_update(
            access_token="token",
            event_id="event-123",
            summary="변경된 회의",
            start_datetime="2026-06-11T15:00:00+09:00",
            end_datetime="2026-06-11T16:00:00+09:00",
        )

    assert_matches_outputs("google_calendar.update", result)
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
    current = _make_mock_response(200, {"id": "event-123"})
    client = _make_async_client(get_mock=AsyncMock(return_value=current), patch_mock=patch_mock)

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
    client = _make_async_client(get_mock=AsyncMock(return_value=_make_mock_response(200, {"id": "missing"})),
                                patch_mock=AsyncMock(return_value=resp))

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
    ("google_calendar_find", "get",
     {"time_min": "2026-10-01T00:00:00+09:00", "time_max": "2026-10-31T23:59:59+09:00"}, "/events"),
    ("google_calendar_update", "patch", {"event_id": "evt-1", "summary": "변경"}, "/events/evt-1"),
])
async def test_google_calendar_calendar_id는_경로에_인코딩(tool, verb, kwargs, suffix):
    """드롭다운이 주는 공휴일 캘린더 id의 #가 fragment로 잘리지 않아야 한다."""
    import tools.google_calendar as gc

    call = AsyncMock(return_value=_make_mock_response(200, {"id": "evt-1", "htmlLink": "x", "items": []}))
    client = _make_async_client(**{"get_mock": call, f"{verb}_mock": call})  # update는 대상 GET이 먼저다

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
        await google_calendar_find(
            access_token="token",
            time_min="2026-10-01T00:00:00+09:00",
            time_max="2026-10-31T23:59:59+09:00",
        )

    assert call.call_args.args[0] == f"{_CALENDAR_BASE}primary/events"


async def _calendar_list_url(calendar_id) -> str:
    call = AsyncMock(return_value=_make_mock_response(200, {"items": []}))
    client = _make_async_client(get_mock=call)
    with patch("tools.google_calendar.get_http_client", return_value=client):
        await google_calendar_find(
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
    current = _make_mock_response(200, {"id": "a/b?x=1"})
    client = _make_async_client(get_mock=AsyncMock(return_value=current), patch_mock=call)
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



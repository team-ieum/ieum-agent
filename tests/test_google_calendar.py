"""Google Calendar 찾기·수정·삭제 도구(spec 2026-10-18 §3·§4). HTTP는 목으로 대신한다."""
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from common.error_code import ToolErrorCode
from tests.test_google_tools import _make_async_client, _make_mock_response
from tools.google_calendar import google_calendar_delete, google_calendar_find

BASE = "https://www.googleapis.com/calendar/v3/calendars/primary"


def _gets(routes: dict) -> AsyncMock:
    """URL 끝으로 GET 응답을 고른다 — events.get·원본 get·calendars.get이 client.get 하나를 같이 쓴다.
    값이 dict면 200 본문, 아니면 응답 목 그대로. routes 밖 URL은 AssertionError(도구가 잡아 error JSON이 된다)."""
    async def get(url, **kwargs):
        for suffix, body in routes.items():
            if url.endswith(suffix):
                return _make_mock_response(200, body) if isinstance(body, dict) else body
        raise AssertionError(f"예상 못 한 GET {url}")
    return AsyncMock(side_effect=get)


def _client(get=None, patch_mock=None, delete=None):
    """넘기지 않은 메서드는 불리면 실패한다(호출 없음 단언용)."""
    client = _make_async_client(get_mock=get or AsyncMock(side_effect=AssertionError("GET 없음")),
                                patch_mock=patch_mock or AsyncMock(side_effect=AssertionError("PATCH 없음")))
    client.delete = delete or AsyncMock(side_effect=AssertionError("DELETE 없음"))
    return client


async def _run(fn, client, **kwargs) -> dict:
    with patch("tools.google_calendar.get_http_client", return_value=client):
        return json.loads(await fn(access_token="token", **kwargs))


def _empty_body(status: int):
    """events.delete 성공 응답 — 본문이 비어 json()이 터진다."""
    resp = _make_mock_response(status, {})
    resp.json.side_effect = ValueError("빈 본문")
    return resp


_INSTANCE = {"id": "evt-1_20261020T000000Z", "recurringEventId": "evt-1", "status": "confirmed",
             "start": {"dateTime": "2026-10-20T09:00:00+09:00"}, "end": {"dateTime": "2026-10-20T10:00:00+09:00"}}
_MASTER = {"id": "evt-1", "status": "confirmed", "recurrence": ["RRULE:FREQ=WEEKLY"],
           "start": {"dateTime": "2026-09-01T09:00:00+09:00", "timeZone": "Asia/Seoul"},
           "end": {"dateTime": "2026-09-01T10:00:00+09:00", "timeZone": "Asia/Seoul"}}


# --- delete ---------------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("send_updates, params", [("none", {"sendUpdates": "none"}), ("", {}), (None, {})])
async def test_delete_성공은_빈_본문을_읽지_않는다(send_updates, params):
    delete = AsyncMock(return_value=_empty_body(204))
    client = _client(get=_gets({"/events/evt-1": {"id": "evt-1", "status": "confirmed"}}), delete=delete)

    result = await _run(google_calendar_delete, client, event_id="evt-1", send_updates=send_updates)

    assert result == {"success": True, "eventId": "evt-1", "deleted": True}
    assert delete.call_args.args[0] == f"{BASE}/events/evt-1"
    assert delete.call_args.kwargs["params"] == params


@pytest.mark.asyncio
async def test_delete_410은_이미_삭제된_성공이다():
    delete = AsyncMock(return_value=_make_mock_response(410, {"error": {"message": "Resource has been deleted"}}))
    client = _client(get=_gets({"/events/evt-1": {"id": "evt-1", "status": "confirmed"}}), delete=delete)

    result = await _run(google_calendar_delete, client, event_id="evt-1")

    assert result == {"success": True, "eventId": "evt-1", "deleted": False}


@pytest.mark.asyncio
async def test_delete_조회가_410이어도_이미_삭제된_성공이다():
    gone = _make_mock_response(410, {"error": {"message": "Resource has been deleted"}})
    result = await _run(google_calendar_delete, _client(get=_gets({"/events/evt-1": gone})), event_id="evt-1")

    assert result == {"success": True, "eventId": "evt-1", "deleted": False}


@pytest.mark.asyncio
async def test_delete_404는_실패다():
    """없는 ID·접근 불가 캘린더·캘린더/일정 엇갈림 — 삼키면 일정이 남은 채 성공이 된다."""
    delete = AsyncMock(return_value=_make_mock_response(404, {"error": {"message": "Not Found"}}))
    client = _client(get=_gets({"/events/evt-1": {"id": "evt-1", "status": "confirmed"}}), delete=delete)

    result = await _run(google_calendar_delete, client, event_id="evt-1")

    assert "404" in result["error"]


@pytest.mark.asyncio
async def test_delete_조회_404는_삭제를_부르지_않고_실패다():
    missing = _make_mock_response(404, {"error": {"message": "Not Found"}})
    result = await _run(google_calendar_delete, _client(get=_gets({"/events/evt-1": missing})), event_id="evt-1")

    assert "404" in result["error"]


@pytest.mark.asyncio
async def test_delete_cancelled면_부르지_않고_deleted_false():
    client = _client(get=_gets({"/events/evt-1": {"id": "evt-1", "status": "cancelled"}}))

    result = await _run(google_calendar_delete, client, event_id="evt-1")

    assert result == {"success": True, "eventId": "evt-1", "deleted": False}


@pytest.mark.asyncio
async def test_delete_all은_회차가_아니라_원본을_지운다():
    delete = AsyncMock(return_value=_empty_body(204))
    client = _client(get=_gets({"/events/evt-1_20261020T000000Z": _INSTANCE, "/events/evt-1": _MASTER}),
                     delete=delete)

    result = await _run(google_calendar_delete, client, event_id="evt-1_20261020T000000Z", scope="all")

    assert result == {"success": True, "eventId": "evt-1", "deleted": True}
    assert delete.call_args.args[0] == f"{BASE}/events/evt-1"


@pytest.mark.asyncio
async def test_delete_this는_회차만_지운다():
    delete = AsyncMock(return_value=_empty_body(204))
    client = _client(get=_gets({"/events/evt-1_20261020T000000Z": _INSTANCE}), delete=delete)

    result = await _run(google_calendar_delete, client, event_id="evt-1_20261020T000000Z")

    assert result["eventId"] == "evt-1_20261020T000000Z"
    assert delete.call_args.args[0] == f"{BASE}/events/evt-1_20261020T000000Z"


@pytest.mark.asyncio
@pytest.mark.parametrize("event_id", ["", "  ", None])
async def test_delete_빈_event_id는_호출_없이_실패(event_id):
    client = _client()

    result = await _run(google_calendar_delete, client, event_id=event_id)

    assert ToolErrorCode.EXECUTION_FAILED.message in result["error"]
    # 기본 목의 AssertionError도 도구 except가 같은 메시지로 바꾸므로, 호출 없음은 따로 단언한다.
    client.get.assert_not_called()
    client.delete.assert_not_called()


# --- find -----------------------------------------------------------------------------------

_EVENTS = "/calendars/primary/events"
_SEOUL = {"/calendars/primary": {"timeZone": "Asia/Seoul"}}
_ITEM_TIMED = {
    "id": "e1", "summary": "회의", "status": "confirmed", "htmlLink": "https://calendar.google.com/e1",
    "location": "3층", "description": "주간", "recurringEventId": "r1",
    "start": {"dateTime": "2026-10-20T09:00:00+09:00"}, "end": {"dateTime": "2026-10-20T10:00:00+09:00"},
    "attendees": [{"email": "a@x.com"}, {"email": "b@y.com", "responseStatus": "accepted"}, {"self": True}],
}
_ITEM_ALL_DAY = {"id": "e2", "start": {"date": "2026-10-21"}, "end": {"date": "2026-10-22"}}


@pytest.mark.asyncio
async def test_find_일정을_고정_키로_펼친다():
    get = _gets({_EVENTS: {"items": [_ITEM_TIMED, _ITEM_ALL_DAY]}})

    result = await _run(google_calendar_find, _client(get=get), time_min="2026-10-20T00:00:00+09:00")

    assert (result["found"], result["total"]) == (True, 2)
    assert result["items"] == [
        {"id": "e1", "summary": "회의", "start": "2026-10-20T09:00:00+09:00", "end": "2026-10-20T10:00:00+09:00",
         "allDay": False, "location": "3층", "description": "주간", "htmlLink": "https://calendar.google.com/e1",
         "recurringEventId": "r1", "status": "confirmed", "attendees": ["a@x.com", "b@y.com"]},
        {"id": "e2", "summary": "", "start": "2026-10-21", "end": "2026-10-22", "allDay": True, "location": "",
         "description": "", "htmlLink": "", "recurringEventId": "", "status": "", "attendees": []},
    ]
    assert get.call_args.args[0] == f"{BASE}/events"
    assert get.call_args.kwargs["params"] == {"singleEvents": "true", "orderBy": "startTime",
                                              "timeMin": "2026-10-20T00:00:00+09:00", "maxResults": 50}


@pytest.mark.asyncio
async def test_find_0건은_성공이다():
    get = _gets({_EVENTS: {"items": []}})

    result = await _run(google_calendar_find, _client(get=get), time_min="2026-10-20T00:00:00+09:00")

    assert result == {"success": True, "found": False, "total": 0, "items": []}


@pytest.mark.asyncio
async def test_find_페이지를_넘겨_max_results까지_모은다():
    get = AsyncMock(side_effect=[
        _make_mock_response(200, {"items": [{"id": "a"}, {"id": "b"}], "nextPageToken": "t2"}),
        _make_mock_response(200, {"items": [{"id": "c"}, {"id": "d"}], "nextPageToken": "t3"}),
    ])

    result = await _run(google_calendar_find, _client(get=get), time_min="2026-10-20T00:00:00Z", max_results=3)

    assert [i["id"] for i in result["items"]] == ["a", "b", "c"] and result["total"] == 3
    first, second = (c.kwargs["params"] for c in get.call_args_list)
    assert (first["maxResults"], "pageToken" in first) == (3, False)
    assert (second["maxResults"], second["pageToken"]) == (1, "t2")


@pytest.mark.asyncio
async def test_find_time_min_비우면_지금부터():
    get = _gets({_EVENTS: {"items": []}})

    with patch("tools.google_calendar._now", return_value=datetime(2026, 10, 18, 3, 0, tzinfo=timezone.utc)):
        await _run(google_calendar_find, _client(get=get), time_min="", time_max="  ", query="")

    params = get.call_args.kwargs["params"]
    assert params["timeMin"] == "2026-10-18T03:00:00+00:00"
    assert "timeMax" not in params and "q" not in params


@pytest.mark.asyncio
async def test_find_오프셋_없는_시각은_캘린더_시간대로_해석한다():
    get = _gets({_EVENTS: {"items": []}, **_SEOUL})

    await _run(google_calendar_find, _client(get=get), time_min="2026-10-20T09:00:00", time_max="2026-10-21",
               query=" 회의 ")

    params = get.call_args.kwargs["params"]
    assert (params["timeMin"], params["timeMax"], params["q"]) == (
        "2026-10-20T09:00:00+09:00", "2026-10-21T00:00:00+09:00", "회의")
    assert sum(c.args[0] == BASE for c in get.call_args_list) == 1  # calendars.get은 한 번만


@pytest.mark.asyncio
@pytest.mark.parametrize("given, sent", [("", 50), (None, 50), ("20", 20), (999, 250), (0, 1)])
async def test_find_max_results는_문자열_빈값도_받고_1에서_250으로_자른다(given, sent):
    get = _gets({_EVENTS: {"items": []}})

    result = await _run(google_calendar_find, _client(get=get), time_min="2026-10-20T00:00:00Z",
                        max_results=given)

    assert "error" not in result
    assert get.call_args.kwargs["params"]["maxResults"] == sent


@pytest.mark.asyncio
async def test_find_API_오류는_실패다():
    get = _gets({_EVENTS: _make_mock_response(403, {"error": {"message": "Forbidden"}})})

    result = await _run(google_calendar_find, _client(get=get), time_min="2026-10-20T00:00:00Z")

    assert "403" in result["error"]

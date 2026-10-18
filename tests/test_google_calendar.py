"""Google Calendar 찾기·수정·삭제 도구(spec 2026-10-18 §3·§4). HTTP는 목으로 대신한다."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from common.error_code import ToolErrorCode
from tests.action_outputs import assert_matches_outputs
from tests.test_google_tools import _make_async_client, _make_mock_response
from tools.google_calendar import google_calendar_delete, google_calendar_find, google_calendar_update

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
    assert_matches_outputs("google_calendar.delete", result)


@pytest.mark.asyncio
async def test_delete_410은_이미_삭제된_성공이다():
    delete = AsyncMock(return_value=_make_mock_response(410, {"error": {"message": "Resource has been deleted"}}))
    client = _client(get=_gets({"/events/evt-1": {"id": "evt-1", "status": "confirmed"}}), delete=delete)

    result = await _run(google_calendar_delete, client, event_id="evt-1")

    assert result == {"success": True, "eventId": "evt-1", "deleted": False}
    assert_matches_outputs("google_calendar.delete", result)


@pytest.mark.asyncio
async def test_delete_조회가_410이어도_이미_삭제된_성공이다():
    gone = _make_mock_response(410, {"error": {"message": "Resource has been deleted"}})
    result = await _run(google_calendar_delete, _client(get=_gets({"/events/evt-1": gone})), event_id="evt-1")

    assert result == {"success": True, "eventId": "evt-1", "deleted": False}
    assert_matches_outputs("google_calendar.delete", result)


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
    assert_matches_outputs("google_calendar.delete", result)


@pytest.mark.asyncio
async def test_delete_all은_회차가_아니라_원본을_지운다():
    delete = AsyncMock(return_value=_empty_body(204))
    client = _client(get=_gets({"/events/evt-1_20261020T000000Z": _INSTANCE, "/events/evt-1": _MASTER}),
                     delete=delete)

    result = await _run(google_calendar_delete, client, event_id="evt-1_20261020T000000Z", scope="all")

    assert result == {"success": True, "eventId": "evt-1", "deleted": True}
    assert delete.call_args.args[0] == f"{BASE}/events/evt-1"
    assert_matches_outputs("google_calendar.delete", result)


@pytest.mark.asyncio
async def test_delete_this는_회차만_지운다():
    delete = AsyncMock(return_value=_empty_body(204))
    client = _client(get=_gets({"/events/evt-1_20261020T000000Z": _INSTANCE}), delete=delete)

    result = await _run(google_calendar_delete, client, event_id="evt-1_20261020T000000Z")

    assert result["eventId"] == "evt-1_20261020T000000Z"
    assert delete.call_args.args[0] == f"{BASE}/events/evt-1_20261020T000000Z"
    assert_matches_outputs("google_calendar.delete", result)


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
    assert_matches_outputs("google_calendar.find", result)


@pytest.mark.asyncio
async def test_find_0건은_성공이다():
    get = _gets({_EVENTS: {"items": []}})

    result = await _run(google_calendar_find, _client(get=get), time_min="2026-10-20T00:00:00+09:00")

    assert result == {"success": True, "found": False, "total": 0, "items": []}
    assert_matches_outputs("google_calendar.find", result)


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
    assert_matches_outputs("google_calendar.find", result)


@pytest.mark.asyncio
async def test_find_time_min_비우면_지금부터():
    get = _gets({_EVENTS: {"items": []}})

    with patch("tools.google_calendar._now", return_value=datetime(2026, 10, 18, 3, 0, tzinfo=timezone.utc)):
        result = await _run(google_calendar_find, _client(get=get), time_min="", time_max="  ", query="")

    params = get.call_args.kwargs["params"]
    assert params["timeMin"] == "2026-10-18T03:00:00+00:00"
    assert "timeMax" not in params and "q" not in params
    assert_matches_outputs("google_calendar.find", result)


@pytest.mark.asyncio
async def test_find_time_min_비우면_지금을_오프셋_있는_UTC로_보낸다():
    """오프셋 없는 timeMin은 Google이 400으로 거절한다 — _now를 패치하지 않고 실제 값을 본다."""
    get = _gets({_EVENTS: {"items": []}})

    await _run(google_calendar_find, _client(get=get))

    sent = datetime.fromisoformat(get.call_args.kwargs["params"]["timeMin"])
    assert sent.utcoffset() == timedelta(0)
    assert abs(datetime.now(timezone.utc) - sent) < timedelta(minutes=1)


@pytest.mark.asyncio
async def test_find_오프셋_없는_시각은_캘린더_시간대로_해석한다():
    get = _gets({_EVENTS: {"items": []}, **_SEOUL})

    result = await _run(google_calendar_find, _client(get=get), time_min="2026-10-20T09:00:00",
                        time_max="2026-10-21", query=" 회의 ")

    params = get.call_args.kwargs["params"]
    assert (params["timeMin"], params["timeMax"], params["q"]) == (
        "2026-10-20T09:00:00+09:00", "2026-10-21T00:00:00+09:00", "회의")
    assert sum(c.args[0] == BASE for c in get.call_args_list) == 1  # calendars.get은 한 번만
    assert_matches_outputs("google_calendar.find", result)


@pytest.mark.asyncio
@pytest.mark.parametrize("given, sent", [("", 50), (None, 50), ("20", 20), (999, 250), (0, 1)])
async def test_find_max_results는_문자열_빈값도_받고_1에서_250으로_자른다(given, sent):
    get = _gets({_EVENTS: {"items": []}})

    result = await _run(google_calendar_find, _client(get=get), time_min="2026-10-20T00:00:00Z",
                        max_results=given)

    assert "error" not in result
    assert get.call_args.kwargs["params"]["maxResults"] == sent
    assert_matches_outputs("google_calendar.find", result)


@pytest.mark.asyncio
async def test_find_API_오류는_실패다():
    get = _gets({_EVENTS: _make_mock_response(403, {"error": {"message": "Forbidden"}})})

    result = await _run(google_calendar_find, _client(get=get), time_min="2026-10-20T00:00:00Z")

    assert "403" in result["error"]


_NOT_FOUND = {"/calendars/primary": _make_mock_response(404, {"error": {"message": "Not Found"}})}


@pytest.mark.asyncio
async def test_find_캘린더_시간대_조회_실패는_API_오류다():
    result = await _run(google_calendar_find, _client(get=_gets(_NOT_FOUND)), time_min="2026-10-20T09:00:00")

    assert "Google Calendar API 오류 (404)" in result["error"]


# --- update ---------------------------------------------------------------------------------

_TIMED = {"id": "evt-1", "start": {"dateTime": "2026-10-20T09:00:00+09:00"},
          "end": {"dateTime": "2026-10-20T10:30:00+09:00"}}
_ALL_DAY = {"id": "evt-1", "start": {"date": "2026-10-20"}, "end": {"date": "2026-10-22"}}
_INSTANCE_ID = "evt-1_20261020T000000Z"


def _series(master_start: dict, master_end: dict) -> dict:
    """회차 GET → 원본 GET 라우트."""
    return {f"/events/{_INSTANCE_ID}": _INSTANCE,
            "/events/evt-1": {"id": "evt-1", "recurrence": ["RRULE:FREQ=WEEKLY"],
                              "start": master_start, "end": master_end}}


def _at(dt: str, tz: str | None = None) -> dict:
    piece = {"dateTime": dt, "date": None}
    if tz:
        piece["timeZone"] = tz
    return piece


def _day(d: str) -> dict:
    return {"date": d, "dateTime": None, "timeZone": None}


async def _update(routes: dict, **kwargs):
    """PATCH까지 가면 (결과, 보낸 본문, PATCH URL, 쿼리), 안 가면 (결과, None, None, None)."""
    patch_mock = AsyncMock(return_value=_make_mock_response(200, {"id": "evt-1", "htmlLink": "x"}))
    result = await _run(google_calendar_update, _client(get=_gets(routes), patch_mock=patch_mock), **kwargs)
    if not patch_mock.called:
        return result, None, None, None
    assert_matches_outputs("google_calendar.update", result)
    call = patch_mock.call_args
    return result, call.kwargs["json"], call.args[0], call.kwargs["params"]


@pytest.mark.asyncio
async def test_update_시작만_오면_기존_길이를_유지한다():
    result, body, url, params = await _update({"/events/evt-1": _TIMED}, event_id="evt-1",
                                              start_datetime="2026-10-21T14:00:00+09:00")

    assert result == {"success": True, "eventId": "evt-1", "htmlLink": "x"}
    assert body == {"start": _at("2026-10-21T14:00:00+09:00"), "end": _at("2026-10-21T15:30:00+09:00")}
    assert (url, params) == (f"{BASE}/events/evt-1", {})


@pytest.mark.asyncio
async def test_update_종일_시작만_오면_일수를_유지한다():
    _, body, _, _ = await _update({"/events/evt-1": _ALL_DAY}, event_id="evt-1", start_datetime="2026-10-25")

    assert body == {"start": _day("2026-10-25"), "end": _day("2026-10-27")}


@pytest.mark.asyncio
async def test_update_종일_종료일은_포함으로_받아_다음_날로_보낸다():
    _, body, _, _ = await _update({"/events/evt-1": _ALL_DAY}, event_id="evt-1",
                                  start_datetime="2026-10-20", end_datetime="2026-10-22")

    assert body == {"start": _day("2026-10-20"), "end": _day("2026-10-23")}


@pytest.mark.asyncio
async def test_update_시각에서_종일로_바꾸면_시각_칸을_지우고_UTC는_캘린더_날짜로_읽는다():
    _, body, _, _ = await _update({"/events/evt-1": _TIMED, **_SEOUL}, event_id="evt-1", all_day=True,
                                  start_datetime="2026-10-19T15:00:00Z")

    assert body == {"start": _day("2026-10-20"), "end": _day("2026-10-21")}  # 종료가 비면 시작일 하루


@pytest.mark.asyncio
async def test_update_종일에서_시각으로_바꾸면_date를_지운다():
    _, body, _, _ = await _update({"/events/evt-1": _ALL_DAY}, event_id="evt-1", all_day=False,
                                  start_datetime="2026-10-20T09:00:00+09:00", end_datetime="2026-10-20T10:00:00+09:00")

    assert body == {"start": _at("2026-10-20T09:00:00+09:00"), "end": _at("2026-10-20T10:00:00+09:00")}


@pytest.mark.asyncio
@pytest.mark.parametrize("times", [{}, {"start_datetime": "2026-10-20T09:00:00+09:00"}])
async def test_update_종일을_시각으로_바꾸는데_시각이_빠지면_실패한다(times):
    result, body, _, _ = await _update({"/events/evt-1": _ALL_DAY}, event_id="evt-1", all_day=False, **times)

    assert "시작과 종료" in result["error"] and body is None


@pytest.mark.asyncio
async def test_update_오프셋_없는_시각은_캘린더_시간대로_해석한다():
    _, body, _, _ = await _update({"/events/evt-1": _TIMED, **_SEOUL}, event_id="evt-1",
                                  start_datetime="2026-10-21T14:00:00", end_datetime="2026-10-21T15:00:00")

    assert body == {"start": _at("2026-10-21T14:00:00+09:00"), "end": _at("2026-10-21T15:00:00+09:00")}


@pytest.mark.asyncio
async def test_update_캘린더_시간대_조회_실패는_API_오류다():
    result, body, _, _ = await _update({"/events/evt-1": _TIMED, **_NOT_FOUND}, event_id="evt-1",
                                       start_datetime="2026-10-21T14:00:00")

    assert "Google Calendar API 오류 (404)" in result["error"] and body is None


@pytest.mark.asyncio
async def test_update_time_zone이_있으면_캘린더를_조회하지_않고_함께_보낸다():
    _, body, _, _ = await _update({"/events/evt-1": _TIMED}, event_id="evt-1", time_zone="America/New_York",
                                  start_datetime="2026-10-21T14:00:00", end_datetime="2026-10-21T15:00:00")

    assert body == {"start": _at("2026-10-21T14:00:00-04:00", "America/New_York"),
                    "end": _at("2026-10-21T15:00:00-04:00", "America/New_York")}


@pytest.mark.asyncio
async def test_update_all은_원본_날짜에_시각만_바꾼다():
    routes = _series({"dateTime": "2026-09-01T09:00:00+09:00", "timeZone": "Asia/Seoul"},
                     {"dateTime": "2026-09-01T10:00:00+09:00", "timeZone": "Asia/Seoul"})

    _, body, url, _ = await _update(routes, event_id=_INSTANCE_ID, scope="all",
                                    start_datetime="2026-10-20T14:00:00+09:00", end_datetime="2026-10-20T15:30:00+09:00")

    assert url == f"{BASE}/events/evt-1"
    assert body == {"start": _at("2026-09-01T14:00:00+09:00", "Asia/Seoul"),
                    "end": _at("2026-09-01T15:30:00+09:00", "Asia/Seoul")}


@pytest.mark.asyncio
async def test_update_all_원본_시간대가_달라도_원본_벽시계로_옮긴다():
    routes = _series({"dateTime": "2026-09-01T09:00:00-04:00", "timeZone": "America/New_York"},
                     {"dateTime": "2026-09-01T10:00:00-04:00", "timeZone": "America/New_York"})

    _, body, _, _ = await _update(routes, event_id=_INSTANCE_ID, scope="all",
                                  start_datetime="2026-10-20T10:00:00+09:00")  # 뉴욕 10-19 21:00, 길이 1시간 유지

    assert body == {"start": _at("2026-09-01T21:00:00-04:00", "America/New_York"),
                    "end": _at("2026-09-01T22:00:00-04:00", "America/New_York")}


@pytest.mark.asyncio
async def test_update_all_자정을_넘겨도_종료가_시작보다_뒤다():
    routes = _series({"dateTime": "2026-09-01T09:00:00+09:00", "timeZone": "Asia/Seoul"},
                     {"dateTime": "2026-09-01T10:00:00+09:00", "timeZone": "Asia/Seoul"})

    _, body, _, _ = await _update(routes, event_id=_INSTANCE_ID, scope="all",
                                  start_datetime="2026-10-20T23:00:00+09:00", end_datetime="2026-10-21T01:00:00+09:00")

    assert body == {"start": _at("2026-09-01T23:00:00+09:00", "Asia/Seoul"),
                    "end": _at("2026-09-02T01:00:00+09:00", "Asia/Seoul")}


@pytest.mark.asyncio
async def test_update_all_종료만_오면_원본_시작을_두고_시작_이하면_다음_날이다():
    routes = _series({"dateTime": "2026-09-01T09:00:00+09:00", "timeZone": "Asia/Seoul"},
                     {"dateTime": "2026-09-01T10:00:00+09:00", "timeZone": "Asia/Seoul"})

    _, body, _, _ = await _update(routes, event_id=_INSTANCE_ID, scope="all", end_datetime="2026-10-20T08:30:00+09:00")

    assert body == {"start": _at("2026-09-01T09:00:00+09:00", "Asia/Seoul"),
                    "end": _at("2026-09-02T08:30:00+09:00", "Asia/Seoul")}


@pytest.mark.asyncio
async def test_update_all_반복_전체의_종일_전환은_거절한다():
    routes = _series({"dateTime": "2026-09-01T09:00:00+09:00", "timeZone": "Asia/Seoul"},
                     {"dateTime": "2026-09-01T10:00:00+09:00", "timeZone": "Asia/Seoul"})

    result, body, _, _ = await _update(routes, event_id=_INSTANCE_ID, scope="all", all_day=True,
                                       start_datetime="2026-10-21")

    assert "종일 여부는 바꿀 수 없어요" in result["error"] and body is None


@pytest.mark.asyncio
async def test_update_종료만_오면_시작은_그대로다():
    _, body, _, _ = await _update({"/events/evt-1": _TIMED}, event_id="evt-1",
                                  end_datetime="2026-10-20T11:00:00+09:00")

    assert body == {"start": _at("2026-10-20T09:00:00+09:00"), "end": _at("2026-10-20T11:00:00+09:00")}


@pytest.mark.asyncio
async def test_update_all인데_반복이_아니면_그_일정을_그대로_고친다():
    _, body, url, _ = await _update({"/events/evt-1": _TIMED}, event_id="evt-1", scope="all",
                                    start_datetime="2026-10-25T09:00:00+09:00")

    assert url == f"{BASE}/events/evt-1"
    assert body == {"start": _at("2026-10-25T09:00:00+09:00"), "end": _at("2026-10-25T10:30:00+09:00")}


@pytest.mark.asyncio
async def test_update_all_제목만_바꿔도_원본을_고친다():
    routes = _series({"dateTime": "2026-09-01T09:00:00+09:00", "timeZone": "Asia/Seoul"},
                     {"dateTime": "2026-09-01T10:00:00+09:00", "timeZone": "Asia/Seoul"})

    _, body, url, _ = await _update(routes, event_id=_INSTANCE_ID, scope="all", summary="새 제목")

    assert (url, body) == (f"{BASE}/events/evt-1", {"summary": "새 제목"})


@pytest.mark.asyncio
async def test_update_종일_반복_전체의_날짜는_거절한다():
    routes = {f"/events/{_INSTANCE_ID}": {"id": _INSTANCE_ID, "recurringEventId": "evt-1",
                                          "start": {"date": "2026-10-20"}, "end": {"date": "2026-10-21"}},
              "/events/evt-1": {"id": "evt-1", "recurrence": ["RRULE:FREQ=WEEKLY"],
                                "start": {"date": "2026-09-01"}, "end": {"date": "2026-09-02"}}}

    result, body, _, _ = await _update(routes, event_id=_INSTANCE_ID, scope="all",
                                       start_datetime="2026-10-21", end_datetime="2026-10-21")

    assert "반복 전체 종일 일정의 날짜는 바꿀 수 없어요" in result["error"] and body is None


@pytest.mark.asyncio
async def test_update_비운_칸은_보내지_않는다():
    """조회도 하지 않는다 — 시각·종일·반복 전체가 아니면 PATCH 한 번."""
    _, body, url, params = await _update({}, event_id="evt-1", summary="", description="  ", location="",
                                         attendees=[], start_datetime="", end_datetime=" ", all_day="",
                                         send_updates="", time_zone="")

    assert (body, url, params) == ({}, f"{BASE}/events/evt-1", {})


@pytest.mark.asyncio
async def test_update_참석자는_쉼표로_나눠_통째로_바꾼다():
    _, body, _, params = await _update({}, event_id="evt-1", attendees="a@x.com, b@y.com ,,", location="회의실",
                                       send_updates="externalOnly")

    assert body == {"attendees": [{"email": "a@x.com"}, {"email": "b@y.com"}], "location": "회의실"}
    assert params == {"sendUpdates": "externalOnly"}


@pytest.mark.asyncio
async def test_update_쉼표뿐인_참석자는_보내지_않는다():
    """[]로 PATCH하면 기존 참석자가 전부 지워진다."""
    _, body, _, _ = await _update({}, event_id="evt-1", attendees=" , ,", summary="x")

    assert body == {"summary": "x"}


@pytest.mark.asyncio
async def test_update_all_종료가_원본_시작과_같으면_다음_날이다():
    routes = _series({"dateTime": "2026-09-01T09:00:00+09:00", "timeZone": "Asia/Seoul"},
                     {"dateTime": "2026-09-01T10:00:00+09:00", "timeZone": "Asia/Seoul"})

    _, body, _, _ = await _update(routes, event_id=_INSTANCE_ID, scope="all", end_datetime="2026-10-20T09:00:00+09:00")

    assert body["end"] == _at("2026-09-02T09:00:00+09:00", "Asia/Seoul")


@pytest.mark.asyncio
@pytest.mark.parametrize("times", [{"start_datetime": "2026-10-21"}, {"end_datetime": " 2026-10-21 "}])
async def test_update_시각_일정에_날짜만_오면_자정을_지어내지_않고_실패한다(times):
    result, body, _, _ = await _update({"/events/evt-1": _TIMED, **_SEOUL}, event_id="evt-1", **times)

    assert "시각까지" in result["error"] and body is None


@pytest.mark.asyncio
async def test_update_all_시각_일정에_날짜만_오면_시리즈를_옮기지_않고_실패한다():
    routes = _series({"dateTime": "2026-09-01T09:00:00+09:00", "timeZone": "Asia/Seoul"},
                     {"dateTime": "2026-09-01T10:00:00+09:00", "timeZone": "Asia/Seoul"})

    result, body, _, _ = await _update(routes, event_id=_INSTANCE_ID, scope="all", start_datetime="2026-10-21")

    assert "시각까지" in result["error"] and body is None


@pytest.mark.asyncio
async def test_update_this라도_대상이_반복_원본이면_원본_날짜를_유지한다():
    """원본 ID(find의 recurringEventId)를 넣으면 scope=this여도 PATCH 대상은 시리즈 전체다(spec §4.2-3)."""
    routes = _series({"dateTime": "2026-09-01T09:00:00+09:00", "timeZone": "Asia/Seoul"},
                     {"dateTime": "2026-09-01T10:00:00+09:00", "timeZone": "Asia/Seoul"})

    _, body, url, _ = await _update(routes, event_id="evt-1", start_datetime="2026-10-21T14:00:00+09:00")

    assert url == f"{BASE}/events/evt-1"
    assert body == {"start": _at("2026-09-01T14:00:00+09:00", "Asia/Seoul"),
                    "end": _at("2026-09-01T15:00:00+09:00", "Asia/Seoul")}


@pytest.mark.asyncio
@pytest.mark.parametrize("given, body", [
    ("false", {"start": _at("2026-10-21T09:00:00+09:00"), "end": _at("2026-10-21T10:30:00+09:00")}),
    (" FALSE ", {"start": _at("2026-10-21T09:00:00+09:00"), "end": _at("2026-10-21T10:30:00+09:00")}),
    ("True", {"start": _day("2026-10-21"), "end": _day("2026-10-22")}),
])
async def test_update_all_day_문자열은_true_false만_읽는다(given, body):
    _, sent, _, _ = await _update({"/events/evt-1": _TIMED, **_SEOUL}, event_id="evt-1", all_day=given,
                                  start_datetime="2026-10-21T09:00:00+09:00")

    assert sent == body


@pytest.mark.asyncio
@pytest.mark.parametrize("given", ["yes", "0", "참"])
async def test_update_all_day가_true_false가_아닌_문자열이면_호출_없이_실패한다(given):
    result, body, _, _ = await _update({}, event_id="evt-1", all_day=given)

    assert "all_day" in result["error"] and body is None

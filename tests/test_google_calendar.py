"""Google Calendar 찾기·수정·삭제 도구(spec 2026-10-18 §3·§4). HTTP는 목으로 대신한다."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from common.error_code import ToolErrorCode
from tests.action_outputs import assert_matches_outputs
from tests.test_google_tools import _make_async_client, _make_mock_response
from tools.google_calendar import (
    google_calendar_delete,
    google_calendar_find,
    google_calendar_find_busy,
    google_calendar_find_or_create,
    google_calendar_get_calendar,
    google_calendar_get_event,
    google_calendar_list_calendars,
    google_calendar_update,
)

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
async def test_delete_this에_원본_ID면_시리즈를_지우지_않고_실패다():
    """원본 ID엔 회차 정보가 없어 '이 일정만'을 지킬 수 없다 — DELETE를 부르면 시리즈 전체가 지워진다."""
    client = _client(get=_gets({"/events/evt-1": _MASTER}))

    result = await _run(google_calendar_delete, client, event_id="evt-1")

    assert "어느 회차인지 알 수 없어요. 회차를 고르거나 '반복 전체'를 선택하세요" in result["error"]
    client.delete.assert_not_called()


@pytest.mark.asyncio
async def test_delete_all에_원본_ID면_원본을_지운다():
    delete = AsyncMock(return_value=_empty_body(204))
    client = _client(get=_gets({"/events/evt-1": _MASTER}), delete=delete)

    result = await _run(google_calendar_delete, client, event_id="evt-1", scope="all")

    assert result == {"success": True, "eventId": "evt-1", "deleted": True}
    delete.assert_called_once()
    assert delete.call_args.args[0] == f"{BASE}/events/evt-1"


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


_SEOUL = {"/calendars/primary": {"timeZone": "Asia/Seoul"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("period, now, start, end", [
    # 17:00 KST 월요일 — 실행한 날 0시부터
    ("today", datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc), "2026-11-02T00:00:00+09:00", "2026-11-03T00:00:00+09:00"),
    # UTC로는 아직 11-02지만 캘린더 시간대(KST)로는 11-03
    ("today", datetime(2026, 11, 2, 16, 0, tzinfo=timezone.utc), "2026-11-03T00:00:00+09:00", "2026-11-04T00:00:00+09:00"),
    ("tomorrow", datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc), "2026-11-03T00:00:00+09:00", "2026-11-04T00:00:00+09:00"),
    # 수요일 실행 — 이번 주 월요일 0시 ~ 다음 월요일 0시
    ("this_week", datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc), "2026-11-02T00:00:00+09:00", "2026-11-09T00:00:00+09:00"),
    ("next_7_days", datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc), "2026-11-04T00:00:00+09:00", "2026-11-11T00:00:00+09:00"),
])
async def test_find_기간은_캘린더_시간대_기준_실행일로_구간을_정한다(period, now, start, end):
    get = _gets({_EVENTS: {"items": []}, **_SEOUL})

    with patch("tools.google_calendar._now", return_value=now):
        result = await _run(google_calendar_find, _client(get=get), period=period,
                            time_min="2026-01-01T00:00:00+09:00", time_max="2026-01-02T00:00:00+09:00")

    params = get.call_args.kwargs["params"]  # 기간을 고르면 시작·종료 시각 칸은 쓰지 않는다
    assert (params["timeMin"], params["timeMax"]) == (start, end)
    assert_matches_outputs("google_calendar.find", result)


@pytest.mark.asyncio
async def test_find_모르는_기간은_오류다():
    result = await _run(google_calendar_find, _client(), period="yesterday")

    assert "error" in result and "yesterday" in result["error"]


@pytest.mark.asyncio
async def test_find_time_max만_과거면_GET_없이_0건이다():
    """비운 time_min은 '지금'이라 timeMin >= timeMax가 되고, Google은 빈 구간을 400으로 거절한다."""
    with patch("tools.google_calendar._now", return_value=datetime(2026, 10, 18, 3, 0, tzinfo=timezone.utc)):
        result = await _run(google_calendar_find, _client(), time_max="2026-10-01T00:00:00+09:00")

    assert result == {"success": True, "found": False, "total": 0, "items": []}


@pytest.mark.asyncio
async def test_find_숫자_검색어도_문자열로_보낸다():
    get = _gets({_EVENTS: {"items": []}})

    await _run(google_calendar_find, _client(get=get), time_min="2026-10-20T00:00:00Z", query=123)

    assert get.call_args.kwargs["params"]["q"] == "123"


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
    """바꿀 칸이 없으면 PATCH(와 알림 메일)를 보내지 않고 조회한 일정으로 성공한다 — send_updates만 있어도 같다."""
    result, body, _, _ = await _update({"/events/evt-1": dict(_TIMED, htmlLink="link-1")}, event_id="evt-1",
                                       summary="", description="  ", location="", attendees=[], start_datetime="",
                                       end_datetime=" ", all_day="", send_updates="all", time_zone="Asia/Seoul")

    assert body is None
    assert result == {"success": True, "eventId": "evt-1", "htmlLink": "link-1"}
    assert_matches_outputs("google_calendar.update", result)


_CANCELLED = "삭제된 일정은 수정할 수 없어요"


@pytest.mark.asyncio
async def test_update_삭제된_일정은_PATCH하지_않고_실패다():
    result, body, _, _ = await _update({"/events/evt-1": dict(_TIMED, status="cancelled")}, event_id="evt-1",
                                       summary="새 제목")

    assert _CANCELLED in result["error"] and body is None


@pytest.mark.asyncio
async def test_update_삭제된_반복_원본은_회차_안내보다_삭제를_먼저_알린다():
    result, body, _, _ = await _update({"/events/evt-1": dict(_MASTER, status="cancelled")}, event_id="evt-1",
                                       summary="새 제목")

    assert _CANCELLED in result["error"] and body is None


@pytest.mark.asyncio
async def test_update_all에_삭제된_회차라도_원본이_살아_있으면_고친다():
    routes = {f"/events/{_INSTANCE_ID}": dict(_INSTANCE, status="cancelled"), "/events/evt-1": _MASTER}

    _, body, url, _ = await _update(routes, event_id=_INSTANCE_ID, scope="all", summary="새 제목")

    assert (url, body) == (f"{BASE}/events/evt-1", {"summary": "새 제목"})


@pytest.mark.asyncio
async def test_update_all_여러_날_반복_일정에_종료만_오면_실패다():
    routes = _series({"dateTime": "2026-09-07T09:00:00+09:00", "timeZone": "Asia/Seoul"},
                     {"dateTime": "2026-09-09T18:00:00+09:00", "timeZone": "Asia/Seoul"})  # 월 09:00 ~ 수 18:00

    result, body, _, _ = await _update(routes, event_id=_INSTANCE_ID, scope="all",
                                       end_datetime="2026-10-21T17:00:00+09:00")

    assert "여러 날에 걸친 반복 일정은 시작과 종료를 함께 입력하세요" in result["error"] and body is None


@pytest.mark.asyncio
async def test_update_all_자정에_끝나는_반복_일정은_종료만_와도_고친다():
    routes = _series({"dateTime": "2026-09-01T23:00:00+09:00", "timeZone": "Asia/Seoul"},
                     {"dateTime": "2026-09-02T00:00:00+09:00", "timeZone": "Asia/Seoul"})  # 23:00 ~ 다음 날 00:00

    _, body, _, _ = await _update(routes, event_id=_INSTANCE_ID, scope="all", end_datetime="2026-10-21T23:30:00+09:00")

    assert body == {"start": _at("2026-09-01T23:00:00+09:00", "Asia/Seoul"),
                    "end": _at("2026-09-01T23:30:00+09:00", "Asia/Seoul")}


@pytest.mark.asyncio
async def test_update_참석자는_쉼표로_나눠_통째로_바꾼다():
    _, body, _, params = await _update({"/events/evt-1": _TIMED}, event_id="evt-1", attendees="a@x.com, b@y.com ,,", location="회의실",
                                       send_updates="externalOnly")

    assert body == {"attendees": [{"email": "a@x.com"}, {"email": "b@y.com"}], "location": "회의실"}
    assert params == {"sendUpdates": "externalOnly"}


@pytest.mark.asyncio
async def test_update_쉼표뿐인_참석자는_보내지_않는다():
    """[]로 PATCH하면 기존 참석자가 전부 지워진다."""
    _, body, _, _ = await _update({"/events/evt-1": _TIMED}, event_id="evt-1", attendees=" , ,", summary="x")

    assert body == {"summary": "x"}


@pytest.mark.asyncio
@pytest.mark.parametrize("given, sent", [
    ('["a@x.com","b@y.com"]', [{"email": "a@x.com"}, {"email": "b@y.com"}]),  # BE가 목록 참조를 치환한 모양
    (["a@x.com", "b@y.com"], [{"email": "a@x.com"}, {"email": "b@y.com"}]),
    ("[]", None),
])
async def test_update_참석자_목록은_원소_그대로_쓴다(given, sent):
    _, body, _, _ = await _update({"/events/evt-1": _TIMED}, event_id="evt-1", attendees=given, summary="x")

    assert body.get("attendees") == sent


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


_NO_OCCURRENCE = "어느 회차인지 알 수 없어요. 회차를 고르거나 '반복 전체'를 선택하세요"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"summary": "새 제목"},
    {"start_datetime": "2026-10-21T14:00:00+09:00"},
    {"end_datetime": "2026-10-20T11:00:00+09:00"},
    {"all_day": True, "start_datetime": "2026-10-21"},
])
async def test_update_this에_원본_ID면_시리즈를_고치지_않고_실패다(change):
    """원본 ID(find의 recurringEventId)엔 회차 정보가 없어 '이 일정만'을 지킬 수 없다 — PATCH하면 시리즈 전체가 바뀐다."""
    routes = _series({"dateTime": "2026-09-01T09:00:00+09:00", "timeZone": "Asia/Seoul"},
                     {"dateTime": "2026-09-01T10:00:00+09:00", "timeZone": "Asia/Seoul"})

    result, body, _, _ = await _update(routes, event_id="evt-1", **change)

    assert _NO_OCCURRENCE in result["error"] and body is None


@pytest.mark.asyncio
async def test_update_all에_원본_ID면_제목만_바꿔도_원본을_고친다():
    _, body, url, _ = await _update({"/events/evt-1": _MASTER}, event_id="evt-1", scope="all", summary="새 제목")

    assert (url, body) == (f"{BASE}/events/evt-1", {"summary": "새 제목"})


@pytest.mark.asyncio
async def test_update_this에_회차_ID면_제목만_그_회차를_고친다():
    get = _gets({f"/events/{_INSTANCE_ID}": _INSTANCE})
    patch_mock = AsyncMock(return_value=_make_mock_response(200, {"id": _INSTANCE_ID, "htmlLink": "x"}))

    await _run(google_calendar_update, _client(get=get, patch_mock=patch_mock), event_id=_INSTANCE_ID, summary="새 제목")

    assert get.call_count == 1 and patch_mock.call_count == 1
    assert (patch_mock.call_args.args[0], patch_mock.call_args.kwargs["json"]) == \
        (f"{BASE}/events/{_INSTANCE_ID}", {"summary": "새 제목"})


@pytest.mark.asyncio
async def test_AI_캘린더_수정에_원본_ID가_오면_시리즈를_고치지_않는다():
    """AI 경로는 scope=this로 고정된다 — LLM이 찾기 결과의 원본 ID를 넘겨도 시리즈 PATCH로 새지 않는다."""
    from tools import get_tools_for_request
    [tool] = get_tools_for_request([{"name": "builtin:google_calendar_update"}])
    client = _client(get=_gets({"/events/evt-1": _MASTER}))

    with patch("tools.google_calendar.get_http_client", return_value=client):
        result = json.loads(await tool.func(access_token="token", event_id="evt-1", summary="새 제목"))

    assert _NO_OCCURRENCE in result["error"]
    client.patch.assert_not_called()


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


# --- 찾기 묶음: get_event·find_busy·list_calendars·get_calendar·find_or_create ------------------

_EVENT = {"id": "evt-1", "summary": "회의", "status": "confirmed", "htmlLink": "https://cal/evt-1",
          "start": {"dateTime": "2026-11-12T09:00:00+09:00"}, "end": {"dateTime": "2026-11-12T10:00:00+09:00"}}


def _with_post(get=None, post=None):
    client = _client(get=get)
    client.post = post or AsyncMock(side_effect=AssertionError("POST 없음"))
    return client


@pytest.mark.asyncio
async def test_get_event_일정을_찾기와_같은_모양으로_돌려준다():
    result = await _run(google_calendar_get_event, _client(get=_gets({"/events/evt-1": _EVENT})), event_id="evt-1")
    assert_matches_outputs("google_calendar.get_event", result)
    assert (result["found"], result["total"], result["summary"], result["htmlLink"]) == (True, 1, "회의", "https://cal/evt-1")
    assert result["items"][0]["start"] == "2026-11-12T09:00:00+09:00"


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [_make_mock_response(404, {}), _make_mock_response(410, {}),
                                      _make_mock_response(200, {**_EVENT, "status": "cancelled"})])
async def test_get_event_없거나_지운_일정은_found_false_성공(response):
    result = await _run(google_calendar_get_event, _client(get=AsyncMock(return_value=response)), event_id="evt-1")
    assert_matches_outputs("google_calendar.get_event", result)
    assert (result["found"], result["items"], result["summary"]) == (False, [], "")


@pytest.mark.asyncio
async def test_get_event_권한_오류는_실패다():
    result = await _run(google_calendar_get_event,
                        _client(get=AsyncMock(return_value=_make_mock_response(403, {"error": {"message": "x"}}))),
                        event_id="evt-1")
    assert "403" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("event_id", ["", "  ", None])
async def test_get_event_빈_ID는_호출_없이_실패(event_id):
    result = await _run(google_calendar_get_event, _client(), event_id=event_id)
    assert "error" in result


def _busy(calendars: dict):
    return AsyncMock(return_value=_make_mock_response(200, {"calendars": calendars}))


_DAY = dict(time_min="2026-11-12T00:00:00Z", time_max="2026-11-13T00:00:00Z")


@pytest.mark.asyncio
async def test_find_busy_여러_캘린더를_시작순으로_합친다():
    post = _busy({"a@x.com": {"busy": [{"start": "2026-11-12T03:00:00Z", "end": "2026-11-12T04:00:00Z"}]},
                  "primary": {"busy": [{"start": "2026-11-12T01:00:00Z", "end": "2026-11-12T02:00:00Z"}]}})
    result = await _run(google_calendar_find_busy, _with_post(post=post), calendar_ids=" primary, a%40x.com ,primary",
                        **_DAY)
    assert_matches_outputs("google_calendar.find_busy", result)
    assert [(i["calendarId"], i["start"]) for i in result["items"]] == [
        ("primary", "2026-11-12T01:00:00Z"), ("a@x.com", "2026-11-12T03:00:00Z")]
    assert result["errors"] == []
    body = post.call_args.kwargs["json"]
    assert body["items"] == [{"id": "primary"}, {"id": "a@x.com"}]  # 중복은 한 번만
    assert post.call_args.args[0].endswith("/freeBusy")


@pytest.mark.asyncio
async def test_find_busy_읽지_못한_캘린더는_errors에_담고_나머지는_돌려준다():
    post = _busy({"primary": {"busy": [{"start": "2026-11-12T01:00:00Z", "end": "2026-11-12T02:00:00Z"}]},
                  "b@x.com": {"busy": [], "errors": [{"domain": "global", "reason": "notFound"}]}})
    result = await _run(google_calendar_find_busy, _with_post(post=post), calendar_ids="primary,b@x.com", **_DAY)
    assert_matches_outputs("google_calendar.find_busy", result)
    assert result["total"] == 1
    assert result["errors"] == [{"calendarId": "b@x.com", "reason": "notFound"}]


@pytest.mark.asyncio
async def test_find_busy_응답에_키가_없는_캘린더는_notReturned로_알린다():
    # 실호출(2026-11-12): primary + 다른 ID를 보내도 calendars엔 "primary" 하나만 올 수 있다.
    post = _busy({"primary": {"busy": [{"start": "2026-11-12T01:00:00Z", "end": "2026-11-12T02:00:00Z"}]}})
    result = await _run(google_calendar_find_busy, _with_post(post=post), calendar_ids="primary,someone@x.com", **_DAY)
    assert_matches_outputs("google_calendar.find_busy", result)
    assert result["total"] == 1
    assert result["errors"] == [{"calendarId": "someone@x.com", "reason": "notReturned"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"calendar_ids": "", **_DAY}, {"calendar_ids": " , ", **_DAY},
    {"calendar_ids": "primary", "time_min": "", "time_max": "2026-11-13T00:00:00Z"},
    {"calendar_ids": "primary", "time_min": "2026-11-12T00:00:00Z", "time_max": " "},
    {"calendar_ids": ",".join(f"c{i}@x.com" for i in range(51)), **_DAY},
])
async def test_find_busy_필수칸이_비거나_캘린더_50개_초과면_호출_없이_실패(kwargs):
    assert "error" in await _run(google_calendar_find_busy, _with_post(), **kwargs)


@pytest.mark.asyncio
async def test_find_busy_JSON_목록_문자열도_받는다():
    post = _busy({"primary": {"busy": []}, "a@x.com": {"busy": []}})
    await _run(google_calendar_find_busy, _with_post(post=post), calendar_ids='["primary", "a@x.com"]', **_DAY)
    assert post.call_args.kwargs["json"]["items"] == [{"id": "primary"}, {"id": "a@x.com"}]


@pytest.mark.asyncio
async def test_find_busy_종료가_시작보다_앞이면_호출_없이_0건():
    result = await _run(google_calendar_find_busy, _with_post(), calendar_ids="primary",
                        time_min="2026-11-13T00:00:00Z", time_max="2026-11-12T00:00:00Z")
    assert (result["found"], result["items"], result["errors"]) == (False, [], [])


@pytest.mark.asyncio
@pytest.mark.parametrize("time_zone, offset, sent", [("Asia/Seoul", "+09:00", "Asia/Seoul"), ("", "+00:00", "UTC")])
async def test_find_busy_오프셋_없는_시각은_시간대로_읽고_캘린더를_조회하지_않는다(time_zone, offset, sent):
    post = _busy({"primary": {"busy": []}})
    await _run(google_calendar_find_busy, _with_post(post=post), calendar_ids="primary", time_zone=time_zone,
               time_min="2026-11-12T09:00:00", time_max="2026-11-12T18:00:00")
    body = post.call_args.kwargs["json"]
    assert (body["timeMin"], body["timeMax"], body["timeZone"]) == (
        f"2026-11-12T09:00:00{offset}", f"2026-11-12T18:00:00{offset}", sent)


def _cal(id_, role, **extra):
    return {"id": id_, "summary": id_, "accessRole": role, "timeZone": "Asia/Seoul", **extra}


@pytest.mark.asyncio
async def test_list_calendars_페이지를_넘기고_바꾼_이름을_쓴다():
    pages = [_make_mock_response(200, {"items": [_cal("me@x.com", "owner", primary=True, summaryOverride="내 일정")],
                                       "nextPageToken": "p2"}),
             _make_mock_response(200, {"items": [_cal("holiday", "reader", hidden=True)]})]
    get = AsyncMock(side_effect=pages)
    result = await _run(google_calendar_list_calendars, _client(get=get))
    assert_matches_outputs("google_calendar.list_calendars", result)
    assert [(i["id"], i["summary"], i["primary"], i["hidden"]) for i in result["items"]] == [
        ("me@x.com", "내 일정", True, False), ("holiday", "holiday", False, True)]
    assert [c.kwargs["params"].get("pageToken") for c in get.call_args_list] == [None, "p2"]
    assert (get.call_args_list[0].kwargs["params"]["showHidden"],
            get.call_args_list[0].kwargs["params"]["showDeleted"]) == ("false", "false")


@pytest.mark.asyncio
@pytest.mark.parametrize("role, ids", [
    ("writer", ["a", "e"]), ("writerWithoutPrivateAccess", ["a", "c", "e"]), ("reader", ["a", "b", "c", "e"]),
    ("", ["a", "b", "c", "d", "e"]),
])
async def test_list_calendars_최소_권한_이상만_남긴다(role, ids):
    body = {"items": [_cal("a", "owner"), _cal("b", "reader"), _cal("c", "writerWithoutPrivateAccess"),
                      _cal("d", "freeBusyReader"), _cal("e", "writer")]}
    result = await _run(google_calendar_list_calendars, _client(get=AsyncMock(return_value=_make_mock_response(200, body))),
                        min_access_role=role)
    assert [i["id"] for i in result["items"]] == ids


@pytest.mark.asyncio
@pytest.mark.parametrize("given, sent", [(True, "true"), ("true", "true"), ("false", "false"), (False, "false")])
async def test_list_calendars_토글은_문자열_false를_참으로_읽지_않는다(given, sent):
    get = AsyncMock(return_value=_make_mock_response(200, {"items": []}))
    await _run(google_calendar_list_calendars, _client(get=get), show_hidden=given, show_deleted=given)
    params = get.call_args.kwargs["params"]
    assert (params["showHidden"], params["showDeleted"]) == (sent, sent)


@pytest.mark.asyncio
async def test_list_calendars_모르는_권한과_API_오류는_실패다():
    assert "error" in await _run(google_calendar_list_calendars, _client(), min_access_role="admin")
    get = AsyncMock(return_value=_make_mock_response(401, {"error": {"message": "만료"}}))
    assert "401" in (await _run(google_calendar_list_calendars, _client(get=get)))["error"]


@pytest.mark.asyncio
async def test_get_calendar_내_목록_항목으로_권한_기본알림_Meet을_돌려준다():
    entry = {"id": "me@x.com", "summary": "나", "timeZone": "Asia/Seoul", "accessRole": "owner", "primary": True,
             "defaultReminders": [{"method": "popup", "minutes": 10}],
             "conferenceProperties": {"allowedConferenceSolutionTypes": ["hangoutsMeet"]}}
    get = _gets({"/users/me/calendarList/primary": entry})
    result = await _run(google_calendar_get_calendar, _client(get=get), calendar_id="")
    assert_matches_outputs("google_calendar.get_calendar", result)
    item = result["items"][0]
    assert (result["summary"], result["timeZone"], result["accessRole"]) == ("나", "Asia/Seoul", "owner")
    assert (item["defaultReminders"], item["meetSupported"]) == ([{"method": "popup", "minutes": 10}], True)


@pytest.mark.asyncio
async def test_get_calendar_목록에_없으면_found_false_성공():
    result = await _run(google_calendar_get_calendar, _client(get=AsyncMock(return_value=_make_mock_response(404, {}))),
                        calendar_id="nope@x.com")
    assert_matches_outputs("google_calendar.get_calendar", result)
    assert (result["found"], result["summary"], result["accessRole"]) == (False, "", "")


_NEW = dict(summary="주간 회의", start_datetime="2026-11-12T09:00:00+09:00", end_datetime="2026-11-12T10:00:00+09:00")


@pytest.mark.asyncio
async def test_find_or_create_찾으면_만들지_않는다():
    get = AsyncMock(return_value=_make_mock_response(200, {"items": [_EVENT]}))
    result = await _run(google_calendar_find_or_create, _with_post(get=get), time_min="2026-11-01T00:00:00Z", **_NEW)
    assert_matches_outputs("google_calendar.find_or_create", result)
    assert (result["created"], result["eventId"]) == (False, "evt-1")
    assert get.call_args.kwargs["params"]["q"] == "주간 회의"  # 검색어를 비우면 제목으로 찾는다


@pytest.mark.asyncio
async def test_find_or_create_없으면_만든다():
    get = AsyncMock(return_value=_make_mock_response(200, {"items": []}))
    post = AsyncMock(return_value=_make_mock_response(200, {"id": "new-1", "htmlLink": "https://cal/new-1"}))
    result = await _run(google_calendar_find_or_create, _with_post(get=get, post=post), query="회의",
                        time_min="2026-11-01T00:00:00Z", **_NEW)
    assert_matches_outputs("google_calendar.find_or_create", result)
    assert (result["created"], result["eventId"]) == (True, "new-1")
    assert get.call_args.kwargs["params"]["q"] == "회의"
    assert post.call_args.kwargs["json"]["summary"] == "주간 회의"


@pytest.mark.asyncio
async def test_find_or_create_기간을_고르면_찾기에_넘긴다():
    get = _gets({"/calendars/primary": {"timeZone": "Asia/Seoul"},
                 "/calendars/primary/events": {"items": [_EVENT]}})
    with patch("tools.google_calendar._now", return_value=datetime(2026, 11, 12, 3, 0, tzinfo=timezone.utc)):
        result = await _run(google_calendar_find_or_create, _with_post(get=get), period="today", **_NEW)
    assert result["created"] is False
    params = get.call_args.kwargs["params"]
    assert (params["timeMin"], params["timeMax"]) == ("2026-11-12T00:00:00+09:00", "2026-11-13T00:00:00+09:00")


@pytest.mark.asyncio
async def test_find_or_create_찾기_실패면_만들지_않는다():
    get = AsyncMock(return_value=_make_mock_response(403, {"error": {"message": "권한"}}))
    result = await _run(google_calendar_find_or_create, _with_post(get=get), time_min="2026-11-01T00:00:00Z", **_NEW)
    assert "403" in result["error"]


@pytest.mark.asyncio
async def test_find_or_create_제목과_검색어가_모두_비면_호출_없이_실패():
    result = await _run(google_calendar_find_or_create, _with_post(),
                        **{**_NEW, "summary": " "}, query="")
    assert "error" in result



@pytest.mark.asyncio
@pytest.mark.parametrize("fn, kwargs", [
    (google_calendar_get_event, {"event_id": ".."}),
    (google_calendar_get_event, {"event_id": "evt-1", "calendar_id": ".."}),
    (google_calendar_get_calendar, {"calendar_id": "%2E%2E"}),
    (google_calendar_delete, {"event_id": "."}),
])
async def test_점_경로_ID는_호출_없이_실패(fn, kwargs):
    result = await _run(fn, _with_post(), **kwargs)
    assert "error" in result


@pytest.mark.asyncio
async def test_create_HTML_5xx도_상태_코드를_남긴다():
    resp = _make_mock_response(502, {})
    resp.json.side_effect = ValueError("HTML")
    with patch("tools.google_calendar.get_http_client", return_value=_with_post(post=AsyncMock(return_value=resp))):
        from tools.google_calendar import google_calendar_create
        result = json.loads(await google_calendar_create("token", summary="a", start_datetime="2026-11-12T09:00:00+09:00",
                                                          end_datetime="2026-11-12T10:00:00+09:00"))
    assert "502" in result["error"]

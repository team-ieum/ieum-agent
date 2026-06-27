import pytest
from unittest.mock import AsyncMock, MagicMock

from google.adk.sessions import Session
from google.adk.events import Event

from db.session_service import MongoSessionService


@pytest.mark.asyncio
async def test_append_event_uses_push_not_full_dump():
    """append_event는 신규 이벤트만 $push 증분 추가하고 state만 $set 한다.
    (events 배열 전체 재덤프 → O(n²) 쓰기/16MB 한계 회피)"""
    svc = MongoSessionService()
    svc.collection = MagicMock()
    svc.collection.update_one = AsyncMock()

    session = Session(id="s1", app_name="ieum-agent", user_id="u1", state={}, events=[])
    event = Event(author="user")

    await svc.append_event(session, event)

    args, _ = svc.collection.update_one.call_args
    update = args[1]

    assert "$push" in update
    assert "events" in update["$push"]
    assert "$set" in update
    assert "state" in update["$set"]
    # events 전체를 $set으로 재기록하지 않아야 한다
    assert "events" not in update["$set"]


@pytest.mark.asyncio
async def test_get_user_state_returns_empty_dict():
    """IEUM은 유저 스코프 상태를 쓰지 않으므로 get_user_state는 빈 dict를 반환한다.
    (ADK 2.x 기본 구현의 NotImplementedError를 던지지 않는 forward-safe stub)"""
    svc = MongoSessionService()
    # DB를 건드리지 않는 순수 stub이어야 한다 — collection 접근 자체가 없어야 함.
    # None으로 두면 .find_one/.find 등 어떤 속성 접근도 AttributeError로 즉시 터져,
    # 의도치 않은 DB 접근을 확실히 잡는다. (MagicMock(side_effect=...)는 호출 시에만
    # 발동해 속성 접근을 못 막으므로 가드가 무력해진다.)
    svc.collection = None

    assert await svc.get_user_state(app_name="ieum-agent", user_id="u1") == {}

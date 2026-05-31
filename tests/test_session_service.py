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

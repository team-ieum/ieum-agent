from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
from google.adk.sessions import BaseSessionService, Session
from google.adk.sessions.base_session_service import ListSessionsResponse
from google.adk.events import Event
from db.mongodb import db

def _make_bson_safe(val: Any) -> Any:
    if isinstance(val, set):
        return list(val)
    if isinstance(val, dict):
        return {k: _make_bson_safe(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_make_bson_safe(v) for v in val]
    if hasattr(val, "model_dump"):
        return _make_bson_safe(val.model_dump(mode="json"))
    return val

class MongoSessionService(BaseSessionService):
    """MongoDB를 백엔드로 사용하는 Google ADK SessionService 구현체"""

    def __init__(self):
        super().__init__()
        self.collection = db["agent_sessions"]

    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> Session:
        sid = session_id or str(uuid4())
        doc = await self.collection.find_one({
            "id": sid,
            "app_name": app_name,
            "user_id": user_id
        })
        if doc:
            events = [Event(**e) for e in (doc.get("events") or [])]
            return Session(
                id=sid,
                app_name=app_name,
                user_id=user_id,
                state=doc.get("state", {}),
                events=events
            )

        session = Session(
            id=sid,
            app_name=app_name,
            user_id=user_id,
            state=state or {},
            events=[]
        )
        doc = session.model_dump(mode="json")
        # TTL 인덱스용 갱신 시각(BSON Date). 마지막 활동 후 일정 기간이 지나면 자동 만료된다.
        doc["updatedAt"] = datetime.now(timezone.utc)
        await self.collection.insert_one(doc)
        return session

    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: Any | None = None,
    ) -> Session | None:
        doc = await self.collection.find_one({
            "id": session_id,
            "app_name": app_name,
            "user_id": user_id
        })
        if doc:
            events = [Event(**e) for e in (doc.get("events") or [])]
            return Session(
                id=session_id,
                app_name=app_name,
                user_id=user_id,
                state=doc.get("state", {}),
                events=events
            )
        return None

    async def append_event(self, session: Session, event: Event) -> Event:
        # 상위 클래스의 이벤트 처리 (상태 델타 병합 및 임시 상태 정리)
        event = await super().append_event(session, event)

        state_dump = _make_bson_safe(session.state)
        event_dump = _make_bson_safe(event.model_dump(mode="json"))
        # 신규 이벤트만 $push 증분 추가하고 state만 $set 갱신한다.
        # (매번 events 배열 전체를 재덤프·재기록하면 긴 멀티턴에서 O(n²) 쓰기가 발생한다)
        await self.collection.update_one(
            {
                "id": session.id,
                "app_name": session.app_name,
                "user_id": session.user_id
            },
            {
                "$set": {"state": state_dump, "updatedAt": datetime.now(timezone.utc)},
                "$push": {"events": event_dump}
            }
        )
        return event

    async def list_sessions(
        self, *, app_name: str, user_id: str | None = None
    ) -> ListSessionsResponse:
        query = {"app_name": app_name}
        if user_id:
            query["user_id"] = user_id

        cursor = self.collection.find(query)
        sessions = []
        async for doc in cursor:
            events = [Event(**e) for e in (doc.get("events") or [])]
            sessions.append(Session(
                id=doc["id"],
                app_name=doc["app_name"],
                user_id=doc["user_id"],
                state=doc.get("state", {}),
                events=events
            ))
        return ListSessionsResponse(sessions=sessions)

    async def delete_session(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> None:
        await self.collection.delete_one({
            "id": session_id,
            "app_name": app_name,
            "user_id": user_id
        })

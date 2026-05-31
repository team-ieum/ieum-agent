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
        await self.collection.insert_one(session.model_dump(mode="json"))
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

        events_dump = [e.model_dump(mode="json") for e in session.events]
        state_dump = _make_bson_safe(session.state)
        # MongoDB에 최종 상태 업데이트
        await self.collection.update_one(
            {
                "id": session.id,
                "app_name": session.app_name,
                "user_id": session.user_id
            },
            {
                "$set": {
                    "state": state_dump,
                    "events": events_dump
                }
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

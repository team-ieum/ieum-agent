from motor.motor_asyncio import AsyncIOMotorClient
from core.config import settings

client = AsyncIOMotorClient(settings.MONGODB_URL)
db = client[settings.MONGODB_DB_NAME]

execution_logs = db["execution_logs"]
generate_workflow_logs = db["generate_workflow_logs"]
chat_logs = db["chat_logs"]
agent_sessions = db["agent_sessions"]
idempotency_records = db["idempotency_records"]

# 멀티턴 대화 세션의 보관 기간(초). 마지막 활동(updatedAt) 후 이 시간이 지나면 자동 만료된다.
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60  # 7일


async def ensure_indexes() -> None:
    """앱 시작 시 호출. TTL 인덱스를 생성한다(멱등).

    멀티턴 채팅 세션이 무한 누적되는 것을 방지한다. create_index는 이미 존재하면 무시되므로
    매 시작 호출해도 안전하다."""
    await agent_sessions.create_index("updatedAt", expireAfterSeconds=SESSION_TTL_SECONDS)
    # 세션 조회·갱신 키(MongoSessionService가 매 이벤트 이 셋으로 찾는다) — TTL 인덱스뿐이라 전체 스캔이었다
    await agent_sessions.create_index([("id", 1), ("app_name", 1), ("user_id", 1)])
    # 멱등 레코드는 상태별로 만료 시점이 달라(진행중 10분 / 완료 1시간) 문서의 expiresAt을
    # 그대로 만료 시각으로 쓴다. expireAfterSeconds=0 + 문서별 날짜 필드가 그 표준 방식이다.
    await idempotency_records.create_index("expiresAt", expireAfterSeconds=0)

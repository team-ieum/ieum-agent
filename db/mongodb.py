from motor.motor_asyncio import AsyncIOMotorClient
from core.config import settings

client = AsyncIOMotorClient(settings.MONGODB_URL)
db = client[settings.MONGODB_DB_NAME]

execution_logs = db["execution_logs"]
generate_workflow_logs = db["generate_workflow_logs"]
modify_workflow_logs = db["modify_workflow_logs"]
chat_logs = db["chat_logs"]
agent_sessions = db["agent_sessions"]

# 멀티턴 대화 세션의 보관 기간(초). 마지막 활동(updatedAt) 후 이 시간이 지나면 자동 만료된다.
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60  # 7일


async def ensure_indexes() -> None:
    """앱 시작 시 호출. agent_sessions에 updatedAt 기준 TTL 인덱스를 생성한다(멱등).

    멀티턴 채팅 세션이 무한 누적되는 것을 방지한다. create_index는 이미 존재하면 무시되므로
    매 시작 호출해도 안전하다."""
    await agent_sessions.create_index("updatedAt", expireAfterSeconds=SESSION_TTL_SECONDS)

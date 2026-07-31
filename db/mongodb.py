import logging

from motor.motor_asyncio import AsyncIOMotorClient
from core.config import settings

logger = logging.getLogger(__name__)

client = AsyncIOMotorClient(settings.MONGODB_URL)
db = client[settings.MONGODB_DB_NAME]

execution_logs = db["execution_logs"]
generate_workflow_logs = db["generate_workflow_logs"]
modify_workflow_logs = db["modify_workflow_logs"]
chat_logs = db["chat_logs"]
agent_sessions = db["agent_sessions"]
node_templates = db["node_templates"]
idempotency_records = db["idempotency_records"]

# 멀티턴 대화 세션의 보관 기간(초). 마지막 활동(updatedAt) 후 이 시간이 지나면 자동 만료된다.
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60  # 7일


async def ensure_indexes() -> None:
    """앱 시작 시 호출. TTL 인덱스를 생성한다(멱등).

    멀티턴 채팅 세션이 무한 누적되는 것을 방지한다. create_index는 이미 존재하면 무시되므로
    매 시작 호출해도 안전하다."""
    await agent_sessions.create_index("updatedAt", expireAfterSeconds=SESSION_TTL_SECONDS)
    # 멱등 레코드는 상태별로 만료 시점이 달라(진행중 10분 / 완료 1시간) 문서의 expiresAt을
    # 그대로 만료 시각으로 쓴다. expireAfterSeconds=0 + 문서별 날짜 필드가 그 표준 방식이다.
    await idempotency_records.create_index("expiresAt", expireAfterSeconds=0)


async def seed_node_templates(collection=None) -> dict:
    """파일 SSOT(노드 템플릿 레지스트리)를 node_templates 컬렉션에 멱등 동기화한다.

    파일이 진실의 원천이므로:
    - 스키마/드리프트 검증(validate_registry) 통과 후에만 동기화한다.
    - 파일의 모든 템플릿을 id 기준 upsert 한다.
    - 파일에 더 이상 없는(stale) 문서는 삭제한다.

    collection 인자로 컬렉션을 주입할 수 있다(테스트용). 반환: {upserted, deleted, total}."""
    from core.template_registry import all_templates, validate_registry

    validate_registry()  # 드리프트/스키마 게이트 — 실패 시 동기화 중단

    col = collection if collection is not None else node_templates
    templates = all_templates()
    if not templates:
        raise ValueError("동기화할 템플릿이 존재하지 않습니다. 템플릿 디렉토리 경로를 확인하세요.")
    ids = [t["id"] for t in templates]

    await col.create_index("id", unique=True)

    upserted = 0
    for tpl in templates:
        await col.update_one({"id": tpl["id"]}, {"$set": tpl}, upsert=True)
        upserted += 1

    res = await col.delete_many({"id": {"$nin": ids}})
    deleted = getattr(res, "deleted_count", 0)

    logger.info("node_templates 동기화 완료: upserted=%d deleted=%d total=%d", upserted, deleted, len(ids))
    return {"upserted": upserted, "deleted": deleted, "total": len(ids)}

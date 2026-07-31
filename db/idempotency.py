"""X-Idempotency-Key 기반 중복 실행 가드 (IEUM-AI-52).

BE는 재시도 대상 노드에 sha256(executionId+nodeId) 앞 32자를 X-Idempotency-Key로 실어 보낸다.
키에 attempt 번호가 없어 같은 노드의 1·2·3회차가 전부 같은 키다 — 이 성질이 중복 차단의 근거다.

동시성은 _id 유니크 제약을 이용한 insert 경합으로 처리한다("조회 후 없으면 insert"는 깨진다).
Mongo 장애 시에는 가드를 건너뛰고 정상 실행한다(중복 위험 < 전면 장애).
"""
import logging
from datetime import datetime, timedelta, timezone

from pymongo.errors import DuplicateKeyError, PyMongoError

from api.schemas.response import AgentExecutionResult
from common.error_code import ErrorCode
from db.mongodb import idempotency_records

logger = logging.getLogger(__name__)

# 진행중 레코드는 짧게 잡는다. 프로세스가 죽으면 레코드를 지울 주체가 없어
# 만료 전까지 그 노드의 재시도가 전부 막히기 때문이다(BE 호출 타임아웃 120초 기준).
IN_FLIGHT_TTL_SECONDS = 10 * 60
# 완료 응답 캐시는 재시도 창을 덮을 만큼만 보관한다.
COMPLETED_TTL_SECONDS = 60 * 60


def _expires_at(seconds: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _in_progress_result() -> AgentExecutionResult:
    """앞선 요청이 아직 실행 중일 때의 응답.

    재시도 대상이 아닌 errorCode를 쓴다 — 중복 방지를 재시도로 뚫으면 의미가 없다."""
    return AgentExecutionResult(
        success=False,
        status="ERROR",
        errorMessage=ErrorCode.DUPLICATE_REQUEST.message,
        errorCode=ErrorCode.DUPLICATE_REQUEST.name,
    )


async def claim(key: str | None) -> tuple[bool, AgentExecutionResult | None]:
    """실행 권한을 선점한다.

    반환: (선점 성공 여부, 즉시 반환할 응답)
    - (True, None)   : 이 요청이 실제로 실행해야 한다. 종료 후 complete()/release() 필수.
    - (False, 응답)  : 중복 요청. 저장된 응답이나 진행중 실패 응답을 그대로 반환한다.
    - (False, None)  : 가드 비활성(키 없음 / Mongo 장애). 기존 동작대로 실행한다.
    """
    if not key:
        return False, None

    try:
        await idempotency_records.insert_one({
            "_id": key,
            "status": "IN_PROGRESS",
            "createdAt": datetime.now(timezone.utc),
            "expiresAt": _expires_at(IN_FLIGHT_TTL_SECONDS),
        })
        return True, None
    except DuplicateKeyError:
        pass
    except PyMongoError:
        # 가드 실패로 요청 자체를 죽이지 않는다. 중복 부작용 위험을 감수하고 실행한다.
        logger.warning("멱등 가드 선점 실패 — 가드를 건너뛰고 실행한다.", exc_info=True)
        return False, None

    try:
        doc = await idempotency_records.find_one({"_id": key})
    except PyMongoError:
        logger.warning("멱등 레코드 조회 실패 — 가드를 건너뛰고 실행한다.", exc_info=True)
        return False, None

    if doc is None:
        # insert와 조회 사이에 TTL로 만료된 경우. 가드 없이 실행한다.
        return False, None
    if doc.get("status") == "COMPLETED" and doc.get("response") is not None:
        logger.info("멱등 키 중복 — 저장된 응답을 재사용한다.")
        return False, AgentExecutionResult(**doc["response"])
    logger.info("멱등 키 중복 — 앞선 요청이 아직 실행 중이라 재실행하지 않는다.")
    return False, _in_progress_result()


async def complete(key: str, result: AgentExecutionResult) -> None:
    """성공 응답을 저장해 이후 같은 키의 요청이 재실행 없이 받아가게 한다."""
    try:
        await idempotency_records.update_one(
            {"_id": key},
            {"$set": {
                "status": "COMPLETED",
                "response": result.model_dump(),
                "expiresAt": _expires_at(COMPLETED_TTL_SECONDS),
            }},
        )
    except PyMongoError:
        logger.warning("멱등 응답 저장 실패 — 이후 동일 키 요청은 재실행된다.", exc_info=True)


async def release(key: str) -> None:
    """레코드를 지워 다음 재시도가 실제로 재실행되게 한다.

    실패한 실행은 캐싱하지 않는다. 키에 attempt가 없으므로 실패를 캐싱하면
    그 노드의 재시도가 영구히 같은 실패를 돌려받는다."""
    try:
        await idempotency_records.delete_one({"_id": key})
    except PyMongoError:
        logger.warning("멱등 레코드 해제 실패 — TTL 만료까지 재시도가 막힌다.", exc_info=True)

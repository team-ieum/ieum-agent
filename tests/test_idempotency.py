"""
tests/test_idempotency.py

IEUM-AI-52 회귀 테스트. X-Idempotency-Key가 붙은 재요청이 도구를 재실행하지 않고
저장된 응답을 재사용하는지, 헤더가 없으면 기존 동작이 유지되는지 검증한다.

Mongo는 인메모리 fake 컬렉션으로 대체한다(실 DB/실 API 키 불필요).
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError, PyMongoError

from api.schemas.response import AgentExecutionResult
from db import idempotency
from main import app

client = TestClient(app)

HEADERS = {
    "X-LLM-Provider": "CLAUDE",
    "X-LLM-Api-Key": "test-key",
    "X-User-Id": "test-user",
}

PAYLOAD = {
    "nodeId": "node-1",
    "workflowExecutionId": "exec-uuid-123",
    "renderedPrompt": "메일 보내줘",
    "agentType": "react",
}

KEY = "a" * 32


class FakeCollection:
    """_id 유니크 제약만 흉내낸 인메모리 컬렉션."""

    def __init__(self):
        self.docs: dict = {}

    async def insert_one(self, doc):
        if doc["_id"] in self.docs:
            raise DuplicateKeyError("duplicate key")
        self.docs[doc["_id"]] = dict(doc)

    async def find_one(self, filt):
        doc = self.docs.get(filt["_id"])
        return dict(doc) if doc else None

    async def update_one(self, filt, update):
        doc = self.docs.get(filt["_id"])
        if doc is not None:
            doc.update(update["$set"])

    async def delete_one(self, filt):
        self.docs.pop(filt["_id"], None)


@pytest.fixture
def fake_records():
    col = FakeCollection()
    with patch.object(idempotency, "idempotency_records", col):
        yield col


def _post(headers: dict, run_agent_mock):
    with patch("api.routes.execute.run_agent", new=run_agent_mock):
        return client.post("/v1/execute", json=PAYLOAD, headers=headers)


# ---------------------------------------------------------------------------
# 헤더 없음 → 기존 동작
# ---------------------------------------------------------------------------

def test_헤더_없으면_기존_동작_그대로(fake_records):
    run_agent = AsyncMock(return_value=AgentExecutionResult(success=True, output="발송 완료"))

    first = _post(HEADERS, run_agent)
    second = _post(HEADERS, run_agent)

    assert first.status_code == 200 and second.status_code == 200
    assert run_agent.await_count == 2      # 중복 제거 없음
    assert fake_records.docs == {}         # 레코드도 남기지 않는다


# ---------------------------------------------------------------------------
# 같은 키 재요청 → 도구 미재실행 + 응답 재사용
# ---------------------------------------------------------------------------

def test_같은_키_재요청은_재실행하지_않고_응답을_재사용한다(fake_records):
    run_agent = AsyncMock(return_value=AgentExecutionResult(
        success=True, status="COMPLETED", output="메일 1건 발송",
    ))
    headers = {**HEADERS, "X-Idempotency-Key": KEY}

    first = _post(headers, run_agent)
    second = _post(headers, run_agent)

    assert run_agent.await_count == 1      # 두 번째 요청은 도구를 다시 돌리지 않았다
    assert second.status_code == 200
    assert second.json() == first.json()
    assert second.json()["output"] == "메일 1건 발송"


def test_다른_키는_각각_실행된다(fake_records):
    run_agent = AsyncMock(return_value=AgentExecutionResult(success=True, output="ok"))

    _post({**HEADERS, "X-Idempotency-Key": KEY}, run_agent)
    _post({**HEADERS, "X-Idempotency-Key": "b" * 32}, run_agent)

    assert run_agent.await_count == 2


# ---------------------------------------------------------------------------
# 진행중 케이스
# ---------------------------------------------------------------------------

def test_진행중_재요청은_DUPLICATE_REQUEST로_거절된다(fake_records):
    run_agent = AsyncMock(return_value=AgentExecutionResult(success=True, output="ok"))
    headers = {**HEADERS, "X-Idempotency-Key": KEY}

    # 앞선 요청이 아직 실행 중인 상태를 재현한다.
    fake_records.docs[KEY] = {"_id": KEY, "status": "IN_PROGRESS"}

    response = _post(headers, run_agent)

    assert run_agent.await_count == 0
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["errorCode"] == "DUPLICATE_REQUEST"


def test_진행중_errorCode는_BE_재시도_대상이_아니다():
    """재시도되면 중복 방지를 재시도로 뚫는 셈이라 의미가 없다."""
    retryable = {"RATE_LIMITED", "AGENT_TIMEOUT"}
    assert idempotency._in_progress_result().errorCode not in retryable


# ---------------------------------------------------------------------------
# 실패는 캐싱하지 않는다 (키에 attempt가 없어 재시도가 영구 무력화되므로)
# ---------------------------------------------------------------------------

def test_실패_응답은_캐싱하지_않고_재시도에서_재실행된다(fake_records):
    headers = {**HEADERS, "X-Idempotency-Key": KEY}
    run_agent = AsyncMock(side_effect=[
        AgentExecutionResult(success=False, status="ERROR", errorCode="RATE_LIMITED"),
        AgentExecutionResult(success=True, output="재시도 성공"),
    ])

    first = _post(headers, run_agent)
    assert first.json()["errorCode"] == "RATE_LIMITED"
    assert fake_records.docs == {}         # 실패 후 레코드가 남지 않는다

    second = _post(headers, run_agent)
    assert run_agent.await_count == 2      # 재시도가 실제로 재실행됐다
    assert second.json()["output"] == "재시도 성공"


def test_예외로_빠져도_레코드가_남지_않는다(fake_records):
    headers = {**HEADERS, "X-Idempotency-Key": KEY}
    run_agent = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        _post(headers, run_agent)

    assert fake_records.docs == {}


# ---------------------------------------------------------------------------
# Mongo 장애 → 가드를 건너뛰고 정상 실행 (degrade)
# ---------------------------------------------------------------------------

def test_mongo_장애면_가드를_건너뛰고_실행한다(fake_records, caplog):
    run_agent = AsyncMock(return_value=AgentExecutionResult(success=True, output="ok"))
    headers = {**HEADERS, "X-Idempotency-Key": KEY}

    with patch.object(fake_records, "insert_one", side_effect=PyMongoError("down")):
        with caplog.at_level("WARNING"):
            response = _post(headers, run_agent)

    assert response.status_code == 200
    assert response.json()["output"] == "ok"
    assert run_agent.await_count == 1
    assert any("멱등" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 저장 문서에 민감정보가 없어야 한다
# ---------------------------------------------------------------------------

def test_저장_문서에_크레덴셜이_없다(fake_records):
    run_agent = AsyncMock(return_value=AgentExecutionResult(success=True, output="ok"))
    _post({**HEADERS, "X-Idempotency-Key": KEY}, run_agent)

    doc = fake_records.docs[KEY]
    assert set(doc) == {"_id", "status", "createdAt", "expiresAt", "response"}
    assert "test-key" not in str(doc)


@pytest.mark.asyncio
async def test_진행중_TTL이_완료_캐시보다_짧다():
    """프로세스가 죽으면 TTL 만료까지 재시도가 막히므로 in-flight는 짧아야 한다."""
    assert idempotency.IN_FLIGHT_TTL_SECONDS < idempotency.COMPLETED_TTL_SECONDS


@pytest.mark.asyncio
async def test_취소되면_IN_PROGRESS_레코드가_해제된다(fake_records):
    """CancelledError는 BaseException이라 except Exception으로는 안 잡힌다.

    run_agent()가 모든 Exception을 내부 처리하므로 라우터 except에 도달하는 실질 경로는
    취소뿐이다. 여기서 해제가 안 되면 TTL 10분간 그 노드의 재시도가 전부 막힌다."""
    from api.routes.execute import execute
    from api.schemas.request import AgentNodeRequest

    credentials = {"provider": "CLAUDE", "api_key": "test-key", "user_id": "test-user"}
    with patch("api.routes.execute.run_agent", new=AsyncMock(side_effect=asyncio.CancelledError())):
        with pytest.raises(asyncio.CancelledError):
            await execute(
                AgentNodeRequest(**PAYLOAD),
                credentials=credentials,
                x_idempotency_key=KEY,
            )

    assert fake_records.docs == {}


@pytest.mark.asyncio
async def test_캐시_응답은_원_응답과_동일하다(fake_records):
    """멱등 계약은 "같은 키 = 같은 응답"이다.

    저장 시 추가 마스킹을 걸면 재시도로 캐시를 받은 실행만 값이 달라진다. strict 마스킹은
    token/api_key 같은 키 이름 휴리스틱이라 정상 페이로드도 바꾸고 JSON도 깨뜨린다."""
    original = AgentExecutionResult(
        success=True,
        status="COMPLETED",
        output='{"access_token": "eyJhbGciOiJIUzI1NiJ9.abcdefgh", "expires_in": 3600}',
    )
    await idempotency.claim(KEY)
    await idempotency.complete(KEY, original)

    _, cached = await idempotency.claim(KEY)
    assert cached is not None
    assert cached.output == original.output
    assert json.loads(cached.output)["access_token"] == "eyJhbGciOiJIUzI1NiJ9.abcdefgh"


@pytest.mark.asyncio
async def test_재취소돼도_레코드가_해제된다(fake_records):
    """취소된 태스크에서 그냥 await하면 재취소가 걸릴 때 release가 중간에 끊긴다.

    그러면 이 가드가 고치려던 레코드 누수가 그대로 재현되므로 shield가 필요하다."""
    from api.routes.execute import execute
    from api.schemas.request import AgentNodeRequest

    original_delete = fake_records.delete_one

    async def slow_delete(filt):
        await asyncio.sleep(0.05)
        await original_delete(filt)

    fake_records.delete_one = slow_delete
    credentials = {"provider": "CLAUDE", "api_key": "test-key", "user_id": "test-user"}

    async def _never_finishes(*args, **kwargs):
        await asyncio.sleep(10)

    with patch("api.routes.execute.run_agent", new=_never_finishes):
        task = asyncio.create_task(
            execute(AgentNodeRequest(**PAYLOAD), credentials=credentials, x_idempotency_key=KEY)
        )
        await asyncio.sleep(0.01)
        task.cancel()          # 1차 — run_agent 대기 중
        await asyncio.sleep(0.01)
        task.cancel()          # 2차 — release 진행 중
        with pytest.raises(asyncio.CancelledError):
            await task

    await asyncio.sleep(0.2)   # shield된 release가 끝날 시간
    assert fake_records.docs == {}

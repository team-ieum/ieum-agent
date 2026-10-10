"""ACTION 응답(output: dict)의 멱등 캐시. claim()이 응답 모델을 인자로 받아야 dict output을 복원한다."""
from unittest.mock import patch

import pytest

from api.schemas.response import ActionExecutionResult, AgentExecutionResult
from db import idempotency
from tests.test_idempotency import FakeCollection

KEY = "c" * 32


@pytest.fixture
def fake_records():
    col = FakeCollection()
    with patch.object(idempotency, "idempotency_records", col):
        yield col


def test_action_result_shape():
    result = ActionExecutionResult(success=True, output={"number": 7})
    assert result.model_dump() == {"success": True, "output": {"number": 7}, "errorMessage": None, "errorCode": None}


@pytest.mark.asyncio
async def test_dict_output_캐시는_ActionExecutionResult로_복원된다(fake_records):
    original = ActionExecutionResult(success=True, output={"number": 7, "labels": ["a", "b"], "nested": {"k": 1}})

    assert await idempotency.claim(KEY, ActionExecutionResult) == (True, None)
    await idempotency.complete(KEY, original)
    claimed, cached = await idempotency.claim(KEY, ActionExecutionResult)

    assert claimed is False
    assert isinstance(cached, ActionExecutionResult)
    assert cached == original


@pytest.mark.asyncio
async def test_기본_모델로는_dict_output_캐시를_못_읽는다(fake_records, caplog):
    """claim(model) 인자가 필요한 이유. 기본 모델(AgentExecutionResult.output: str)로 읽으면 캐시 미스가 돼
    재시도마다 쓰기 액션이 다시 실행된다."""
    await idempotency.claim(KEY, ActionExecutionResult)
    await idempotency.complete(KEY, ActionExecutionResult(success=True, output={"number": 7}))

    with caplog.at_level("WARNING"):
        assert await idempotency.claim(KEY) == (False, None)
    assert any("멱등 캐시 역직렬화 실패" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_진행중_응답은_요청한_모델로_만든다(fake_records):
    fake_records.docs[KEY] = {"_id": KEY, "status": "IN_PROGRESS"}

    claimed, response = await idempotency.claim(KEY, ActionExecutionResult)

    assert claimed is False and isinstance(response, ActionExecutionResult)
    assert (response.success, response.errorCode) == (False, "DUPLICATE_REQUEST")
    assert "status" not in response.model_dump()


def test_진행중_기본_응답은_그대로다():
    """기존 /v1/execute 호출부는 인자를 안 넘긴다 — 응답 모양이 바뀌면 안 된다."""
    response = idempotency._in_progress_result()
    assert isinstance(response, AgentExecutionResult)
    assert (response.success, response.status, response.errorCode) == (False, "ERROR", "DUPLICATE_REQUEST")


@pytest.mark.asyncio
async def test_기존_AgentExecutionResult_캐시는_기본_인자로_그대로_복원된다(fake_records):
    original = AgentExecutionResult(success=True, status="COMPLETED", output="메일 1건 발송")
    await idempotency.claim(KEY)
    await idempotency.complete(KEY, original)

    claimed, cached = await idempotency.claim(KEY)

    assert claimed is False and cached == original

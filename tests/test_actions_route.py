"""POST /v1/actions/execute — 라우트·크레덴셜 dependency·멱등 흐름."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.schemas.response import ActionExecutionResult
from db import idempotency
from main import app
from tests.test_idempotency import FakeCollection

client = TestClient(app)

URL = "/v1/actions/execute"
HEADERS = {"X-User-Id": "user-1", "X-GitHub-Token": "gh-token"}   # LLM 헤더(X-LLM-*, X-Key-Mode)는 없다
BODY = {"nodeId": "node-1", "toolKey": "builtin:github_create_issue",
        "config": {"owner": "o", "repo": "r", "title": "버그", "body": "재현"}}
KEY = "d" * 32
RESULT = ActionExecutionResult(success=True, output={"number": 7, "url": "https://github.com/o/r/issues/7", "title": "버그"})


@pytest.fixture
def fake_records():
    col = FakeCollection()
    with patch.object(idempotency, "idempotency_records", col):
        yield col


def _post(headers=None, body=None, run_action=None):
    headers = HEADERS if headers is None else headers
    body = BODY if body is None else body
    if run_action is None:
        return client.post(URL, json=body, headers=headers)
    with patch("api.routes.actions.run_action", new=run_action):
        return client.post(URL, json=body, headers=headers)


# --- 계약 ---------------------------------------------------------------------------------------

def test_LLM_헤더_없이_끝까지_실행된다():
    """실제 github_create_issue를 부르고 HTTP만 목킹한다. 주입 토큰이 Authorization에 실리는지도 본다."""
    response = MagicMock(status_code=201, text="")
    response.json.return_value = {"number": 7, "title": "버그", "html_url": "https://github.com/o/r/issues/7"}
    http = AsyncMock()
    http.post = AsyncMock(return_value=response)
    body = {**BODY, "config": {**BODY["config"], "webhookCredentialId": "cred-1", "_names": {"owner": "내 조직"},
                               "token": "attacker"}}

    with patch("tools.github.get_http_client", return_value=http):
        res = _post(body=body)

    assert res.status_code == 200
    assert res.json() == {"success": True, "errorMessage": None, "errorCode": None,
                          "output": {"number": 7, "url": "https://github.com/o/r/issues/7", "title": "버그"}}
    assert http.post.call_args.kwargs["headers"]["Authorization"] == "Bearer gh-token"
    assert http.post.call_args.kwargs["json"] == {"title": "버그", "body": "재현"}


def test_openapi에_LLM_헤더가_없다():
    names = {p["name"] for p in app.openapi()["paths"][URL]["post"]["parameters"]}
    assert "X-User-Id" in names and "X-Idempotency-Key" in names
    assert not {"X-LLM-Provider", "X-LLM-Api-Key", "X-Key-Mode"} & names


def test_X_User_Id가_없으면_422다():
    assert _post(headers={"X-GitHub-Token": "gh-token"}).status_code == 422


@pytest.mark.parametrize("tool_key", ["builtin:does_not_exist", "builtin:github_nope", "mcp", "", "github_list_issues"])
def test_알_수_없는_toolKey는_400_UNKNOWN_TOOL이다(tool_key):
    res = _post(body={**BODY, "toolKey": tool_key})
    assert res.status_code == 400
    assert res.json()["errorCode"] == "UNKNOWN_TOOL"


def test_토큰_누락은_200_실패_응답이다():
    res = _post(headers={"X-User-Id": "user-1"})
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is False and body["errorCode"] == "AGENT_EXECUTION_FAILED"
    assert "GitHub" in body["errorMessage"] and body["output"] is None


@pytest.mark.parametrize("body", [
    {"nodeId": "node-1", "toolKey": "builtin:github_create_issue"},
    {"nodeId": "node-1", "toolKey": "builtin:github_create_issue", "config": None},
])
def test_config가_없거나_null이면_빈_입력으로_실패_응답이다(body):
    res = _post(body=body)
    assert res.status_code == 200
    assert res.json()["errorMessage"] == "필수 입력이 없습니다: owner, repo, title"


# --- 멱등 ---------------------------------------------------------------------------------------

def test_같은_키_재시도는_재실행하지_않고_같은_응답을_돌려준다(fake_records):
    run = AsyncMock(return_value=RESULT)
    headers = {**HEADERS, "X-Idempotency-Key": KEY}

    first = _post(headers, run_action=run)
    second = _post(headers, run_action=run)

    assert run.await_count == 1          # 쓰기 액션(이슈 생성)이 두 번 실행되면 안 된다
    assert second.json() == first.json()
    assert second.json()["output"] == {"number": 7, "url": "https://github.com/o/r/issues/7", "title": "버그"}


def test_헤더가_없으면_매번_실행하고_레코드를_남기지_않는다(fake_records):
    run = AsyncMock(return_value=RESULT)
    _post(run_action=run)
    _post(run_action=run)
    assert run.await_count == 2 and fake_records.docs == {}


def test_실패는_캐싱하지_않아_재시도가_재실행된다(fake_records):
    run = AsyncMock(side_effect=[
        ActionExecutionResult(success=False, errorCode="ACTION_TOOL_FAILED", errorMessage="GitHub API 오류 (502)"),
        RESULT,
    ])
    headers = {**HEADERS, "X-Idempotency-Key": KEY}

    first = _post(headers, run_action=run)
    assert first.json()["errorCode"] == "ACTION_TOOL_FAILED" and fake_records.docs == {}
    second = _post(headers, run_action=run)

    assert run.await_count == 2 and second.json()["success"] is True


def test_진행중_재요청은_DUPLICATE_REQUEST다(fake_records):
    run = AsyncMock(return_value=RESULT)
    fake_records.docs[KEY] = {"_id": KEY, "status": "IN_PROGRESS"}

    res = _post({**HEADERS, "X-Idempotency-Key": KEY}, run_action=run)

    assert run.await_count == 0
    assert res.status_code == 200
    assert res.json() == {"success": False, "output": None, "errorMessage": "앞선 동일 요청이 아직 실행 중입니다.",
                          "errorCode": "DUPLICATE_REQUEST"}


def test_알_수_없는_toolKey는_멱등_레코드를_남기지_않는다(fake_records):
    """레코드를 만든 뒤 400을 던지면 IN_PROGRESS가 10분간 남아 그 키의 재시도가 전부 DUPLICATE_REQUEST로 막힌다."""
    res = _post({**HEADERS, "X-Idempotency-Key": KEY}, body={**BODY, "toolKey": "builtin:nope"})
    assert res.status_code == 400
    assert fake_records.docs == {}


def test_가드_거부도_레코드를_남기지_않는다(fake_records):
    res = _post({"X-User-Id": "user-1", "X-Idempotency-Key": KEY})        # GitHub 토큰 없음 → 가드 거부
    assert res.json()["success"] is False
    assert fake_records.docs == {}


def test_성공_캐시에는_dict_output이_그대로_저장된다(fake_records):
    _post({**HEADERS, "X-Idempotency-Key": KEY}, run_action=AsyncMock(return_value=RESULT))
    assert fake_records.docs[KEY]["status"] == "COMPLETED"
    assert fake_records.docs[KEY]["response"]["output"] == {
        "number": 7, "url": "https://github.com/o/r/issues/7", "title": "버그"}


@pytest.mark.asyncio
async def test_취소되면_IN_PROGRESS_레코드가_해제된다(fake_records):
    from api.routes.actions import execute_action
    from api.schemas.request import ActionExecutionRequest

    credentials = {"user_id": "user-1", "google_access_token": None, "notion_token": None, "github_token": "gh"}
    with patch("api.routes.actions.run_action", new=AsyncMock(side_effect=asyncio.CancelledError())):
        with pytest.raises(asyncio.CancelledError):
            await execute_action(ActionExecutionRequest(**BODY), credentials=credentials, x_idempotency_key=KEY)

    assert fake_records.docs == {}

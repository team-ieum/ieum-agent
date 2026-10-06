"""run_action — 앱 도구 하나를 LLM 없이 실행하는 코어(POST /v1/actions/execute의 실행부)."""
import inspect
import json
import socket
import threading
from unittest.mock import AsyncMock, patch

import pytest

from api.schemas.request import ActionExecutionRequest
from api.schemas.response import ActionExecutionResult
from common.error_code import ErrorCode
from core import action_executor
from core.action_executor import run_action
from tools import _TOOL_MAP
from tools.registry import _DYNAMIC_SERVICE_ACTIONS, resolve_action_fn


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch):
    """가드의 SSRF 검사가 config URL을 DNS 조회한다 — 테스트는 실제 네트워크에 닿지 않는다(조회 실패로 간주)."""
    def _fail(host):
        raise socket.gaierror(host)
    monkeypatch.setattr(socket, "gethostbyname", _fail)


def _req(tool_key="builtin:github_create_issue", config=None, node_id="node-1"):
    return ActionExecutionRequest(nodeId=node_id, toolKey=tool_key, config=config if config is not None else {})


def _ok(**extra):
    return json.dumps({"success": True, **extra}, ensure_ascii=False)


# --- 요청 스키마 -------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [{"nodeId": "n", "toolKey": "slack"},
                                 {"nodeId": "n", "toolKey": "slack", "config": None}])
def test_request_config가_없거나_null이면_빈_객체다(raw):
    """BE가 config 없는 tools[0]을 보내도 422로 죽지 않는다."""
    assert ActionExecutionRequest(**raw).config == {}


def test_error_codes():
    assert ErrorCode.UNKNOWN_TOOL.status_code == 400
    assert ErrorCode.ACTION_TOOL_FAILED.name == "ACTION_TOOL_FAILED"


# --- 성공 · 인자 조립 ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_성공하면_success_키를_뺀_dict가_output이다():
    async def fake(token, owner, repo, title, body=""):
        return _ok(number=7, url="https://github.com/o/r/issues/7", title=title)

    result = await run_action(fake, _req(config={"owner": "o", "repo": "r", "title": "t"}), "u1",
                              github_token="gh-token")

    assert result == ActionExecutionResult(success=True, output={
        "number": 7, "url": "https://github.com/o/r/issues/7", "title": "t"})


@pytest.mark.asyncio
async def test_success_키만_있는_반환도_빈_dict_output이다():
    async def fake(webhook_url, message, channel=None):
        return _ok()

    result = await run_action(fake, _req("slack", {"webhook_url": "x", "message": "m"}), "u1")
    assert result.success is True and result.output == {}


@pytest.mark.asyncio
async def test_시그니처_밖_키는_버린다():
    """slack/discord는 webhookCredentialId가, 드롭다운 필드는 _names가 config에 같이 저장된다."""
    seen = {}

    async def fake(token, owner, repo, state="open"):
        seen.update(token=token, owner=owner, repo=repo, state=state)
        return _ok()

    config = {"owner": "o", "repo": "r", "webhookCredentialId": "cred-1", "_names": {"owner": "내 조직"}}
    result = await run_action(fake, _req("builtin:github_list_issues", config), "u1", github_token="gh-token")

    assert result.success is True
    assert seen == {"token": "gh-token", "owner": "o", "repo": "r", "state": "open"}


@pytest.mark.asyncio
async def test_주입값이_config의_같은_키를_덮어쓴다():
    seen = {}

    async def fake(token, owner, repo):
        seen["token"] = token
        return _ok()

    await run_action(fake, _req("builtin:github_list_issues", {"owner": "o", "repo": "r", "token": "attacker"}),
                     "u1", github_token="gh-token")

    assert seen["token"] == "gh-token"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_key, param, expected", [
    ("builtin:google_sheets_read", "access_token", "g-token"),
    ("builtin:notion_search", "token", "n-token"),
    ("builtin:github_list_issues", "token", "h-token"),
])
async def test_토큰은_서비스_접두사로_갈라_주입한다(tool_key, param, expected):
    """notion·github 함수는 둘 다 `token`을 받는다 — 인자 이름이 아니라 서비스로 갈라야 토큰이 안 섞인다."""
    seen = {}

    async def fake(**kwargs):
        seen.update(kwargs)
        return _ok()

    fake.__signature__ = inspect.Signature([inspect.Parameter(param, inspect.Parameter.KEYWORD_ONLY)])
    result = await run_action(fake, _req(tool_key), "u1", google_access_token="g-token", notion_token="n-token",
                              github_token="h-token")

    assert result.success is True
    assert seen == {param: expected}


def test_접두사가_가리키는_주입_인자가_실제_함수_시그니처에_있다():
    """도구를 추가했는데 인자 이름이 다르면 호출 때 TypeError가 난다 — 전수로 막는다."""
    keys = [k for k in _TOOL_MAP if k.startswith(("builtin:google_", "builtin:notion_"))]
    keys += [f"builtin:{n}" for n in _DYNAMIC_SERVICE_ACTIONS["github"]]
    assert len(keys) > 10
    for key in keys:
        injected = action_executor._injected_args(
            key, google_access_token="g", notion_token="n", github_token="h")
        assert len(injected) == 1, key
        assert set(injected) <= set(inspect.signature(resolve_action_fn(key)).parameters), key


@pytest.mark.asyncio
async def test_동기_도구는_이벤트_루프_밖_스레드에서_돈다():
    seen = {}

    def sync_fn(json_string):
        seen["thread"] = threading.current_thread()
        return _ok(value=1)

    result = await run_action(sync_fn, _req("builtin:json_parse", {"json_string": "{}"}), "u1")

    assert result.success is True
    assert seen["thread"] is not threading.main_thread()


@pytest.mark.asyncio
async def test_실제_json_parse_도구로_끝까지_돈다():
    config = {"json_string": '{"a": {"b": 3}}', "key_path": "a.b", "junk": "무시"}
    result = await run_action(_TOOL_MAP["builtin:json_parse"], _req("builtin:json_parse", config), "u1")
    assert result == ActionExecutionResult(success=True, output={"value": 3})


# --- 실패 분류 ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_도구가_error를_돌려주면_ACTION_TOOL_FAILED다():
    async def fake(token, owner, repo, title, body=""):
        return json.dumps({"error": 'GitHub API 오류 (422): {"message":"Validation Failed"}'}, ensure_ascii=False)

    result = await run_action(fake, _req(config={"owner": "o", "repo": "r", "title": "t"}), "u1",
                              github_token="gh-token")

    assert result.success is False and result.output is None
    assert result.errorCode == "ACTION_TOOL_FAILED"
    assert result.errorMessage == 'GitHub API 오류 (422): {"message":"Validation Failed"}'


@pytest.mark.asyncio
@pytest.mark.parametrize("error", ["", None, 0, {"code": 1}])
async def test_error_키가_있으면_값이_비어도_실패다(error):
    """truthiness로 판정하면 {"error": ""}가 거짓 성공이 된다 — 쓰기 액션이 성공으로 기록되면 안 된다."""
    async def fake(token, owner, repo):
        return json.dumps({"error": error})

    result = await run_action(fake, _req("builtin:github_list_issues", {"owner": "o", "repo": "r"}), "u1",
                              github_token="gh-token")

    assert result.success is False
    assert result.errorCode == "ACTION_TOOL_FAILED"
    assert result.errorMessage


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["그냥 텍스트", "[1, 2]", "null", "", "123", 123, None])
async def test_도구_반환이_JSON_객체가_아니면_실패_응답이다(raw):
    """평문·리스트·None을 받아도 500이 아니라 BE가 분류할 수 있는 실패 응답이어야 한다."""
    async def fake(token, owner, repo):
        return raw

    result = await run_action(fake, _req("builtin:github_list_issues", {"owner": "o", "repo": "r"}), "u1",
                              github_token="gh-token")

    assert result.success is False and result.output is None
    assert result.errorCode == "AGENT_EXECUTION_FAILED"


@pytest.mark.asyncio
async def test_도구가_예외를_던지면_AGENT_EXECUTION_FAILED이고_예외_문구를_싣지_않는다():
    async def fake(token, owner, repo):
        raise RuntimeError("내부 상세 boom")

    result = await run_action(fake, _req("builtin:github_list_issues", {"owner": "o", "repo": "r"}), "u1",
                              github_token="gh-token")

    assert result.success is False
    assert result.errorCode == "AGENT_EXECUTION_FAILED"
    assert result.errorMessage == ErrorCode.AGENT_EXECUTION_FAILED.message
    assert "boom" not in result.errorMessage


@pytest.mark.asyncio
async def test_타임아웃이면_AGENT_TIMEOUT이다(monkeypatch):
    import asyncio
    monkeypatch.setattr(action_executor.settings, "AGENT_TIMEOUT_SECONDS", 0.05)

    async def slow(token, owner, repo):
        await asyncio.sleep(5)

    result = await run_action(slow, _req("builtin:github_list_issues", {"owner": "o", "repo": "r"}), "u1",
                              github_token="gh-token")

    assert result.success is False
    assert result.errorCode == "AGENT_TIMEOUT"
    assert result.errorMessage == ErrorCode.AGENT_TIMEOUT.message


@pytest.mark.asyncio
async def test_필수_입력이_비면_도구를_부르지_않고_이름을_알려준다():
    """저장 노드에서 owner/repo를 비워 둔 채 테스트를 누르는 게 가장 흔한 실패다 — TypeError 문구 대신 칸 이름."""
    called = []

    async def fake(token, owner, repo, state="open"):
        called.append(1)
        return _ok()

    result = await run_action(fake, _req("builtin:github_list_issues", {"state": "all"}), "u1",
                              github_token="gh-token")

    assert result.success is False and result.errorCode == "AGENT_EXECUTION_FAILED"
    assert result.errorMessage == "필수 입력이 없습니다: owner, repo"
    assert called == []


# --- 가드 -----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_사설망_URL은_가드가_거부하고_도구를_부르지_않는다():
    called = []

    async def fake(url):
        called.append(1)
        return _ok()

    result = await run_action(fake, _req("builtin:http_fetch", {"url": "http://localhost:8000/admin"}), "u1")

    assert result.success is False and result.errorCode == "AGENT_EXECUTION_FAILED"
    assert "사설망 또는 로컬호스트" in result.errorMessage
    assert called == []


@pytest.mark.asyncio
async def test_토큰_헤더가_없으면_거부한다():
    called = []

    async def fake(token, owner, repo):
        called.append(1)
        return _ok()

    # notion 토큰만 있고 github 토큰이 없다 — `token` 인자 이름이 같아도 대신 쓰지 않는다
    result = await run_action(fake, _req("builtin:github_list_issues", {"owner": "o", "repo": "r"}), "u1",
                              notion_token="n-token")

    assert result.success is False and result.errorCode == "AGENT_EXECUTION_FAILED"
    assert "GitHub" in result.errorMessage
    assert called == []


# --- 마스킹 · 로그 -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_오류_문구와_출력의_토큰은_마스킹한다():
    async def leaky_error(token, owner, repo):
        return json.dumps({"error": "GitHub API 오류 (401): Bearer ghp_abcdefghijklmnop1234 rejected"})

    async def leaky_output(token, owner, repo):
        return _ok(note="token ghp_abcdefghijklmnop1234", nested={"v": ["ghs_zzzzzzzzzz9999"]})

    config = {"owner": "o", "repo": "r"}
    failed = await run_action(leaky_error, _req("builtin:github_list_issues", config), "u1", github_token="gh")
    passed = await run_action(leaky_output, _req("builtin:github_list_issues", config), "u1", github_token="gh")

    assert "ghp_" not in failed.errorMessage
    assert "ghp_" not in json.dumps(passed.output) and "ghs_" not in json.dumps(passed.output)


@pytest.mark.asyncio
async def test_실행_로그에는_결과만_남고_config는_남지_않는다():
    async def fake(webhook_url, message, channel=None):
        return _ok(message="전송 완료")

    secret_url = "https://hooks.slack.test/services/T000/B000/SECRETSECRET"
    with patch("core.agent.execution_logs.insert_one", new=AsyncMock()) as insert_one:
        result = await run_action(fake, _req("slack", {"webhook_url": secret_url, "message": "hi"}, "node-9"), "user-1")

    assert result.success is True
    doc = insert_one.call_args.args[0]
    assert (doc["userId"], doc["nodeId"], doc["agentType"], doc["model"], doc["success"]) == \
        ("user-1", "node-9", "action", "slack", True)
    assert json.loads(doc["output"]) == {"message": "전송 완료"}
    assert "SECRETSECRET" not in json.dumps(doc, default=str)


@pytest.mark.asyncio
async def test_로그_저장이_실패해도_결과는_그대로다():
    async def fake(webhook_url, message, channel=None):
        return _ok()

    with patch("core.agent.execution_logs.insert_one", new=AsyncMock(side_effect=RuntimeError("mongo down"))):
        result = await run_action(fake, _req("slack", {"webhook_url": "x", "message": "hi"}), "user-1")

    assert result.success is True

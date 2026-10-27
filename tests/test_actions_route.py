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


@pytest.mark.parametrize("tool_key", ["gmail", "builtin:web_search"])
def test_카탈로그_ACTION_밖_도구는_400_UNKNOWN_TOOL이고_레코드를_남기지_않는다(fake_records, tool_key):
    """_TOOL_MAP에 있어도 builder ACTION 항목이 아니면 거부한다(gmail smtp_host SSRF·플랫폼 Tavily 키 소모)."""
    run = AsyncMock(return_value=RESULT)
    res = _post({**HEADERS, "X-Idempotency-Key": KEY}, body={**BODY, "toolKey": tool_key}, run_action=run)
    assert res.status_code == 400 and res.json()["errorCode"] == "UNKNOWN_TOOL"
    assert run.await_count == 0 and fake_records.docs == {}


@pytest.mark.parametrize("tool_key", ["slack", "builtin:github_list_issues"])
def test_카탈로그_ACTION_도구는_통과한다(tool_key):
    res = _post(body={**BODY, "toolKey": tool_key}, run_action=AsyncMock(return_value=RESULT))
    assert res.status_code == 200 and res.json()["success"] is True


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

    credentials = {"user_id": "user-1", "google_access_token": None, "notion_token": None, "github_token": "gh",
                   "slack_token": None, "discord_token": None, "discord_guild_ids": []}
    with patch("api.routes.actions.run_action", new=AsyncMock(side_effect=asyncio.CancelledError())):
        with pytest.raises(asyncio.CancelledError):
            await execute_action(ActionExecutionRequest(**BODY), credentials=credentials, x_idempotency_key=KEY)

    assert fake_records.docs == {}


def test_캘린더_삭제는_ACTION으로_열리고_구글_토큰이_주입된다():
    """delete는 _TOOL_MAP 밖이지만 카탈로그 ACTION이라 열린다 — resolve_action_fn이 풀고 builtin:google_ 접두사로
    토큰을 받는다. 성공 응답은 빈 본문이라 json()이 터져도 성공이어야 한다."""
    got = MagicMock(status_code=200, is_success=True)
    got.json.return_value = {"id": "evt-1", "status": "confirmed"}
    gone = MagicMock(status_code=204, is_success=True)
    gone.json.side_effect = ValueError("빈 본문")
    http = AsyncMock()
    http.get = AsyncMock(return_value=got)
    http.delete = AsyncMock(return_value=gone)
    body = {"nodeId": "node-1", "toolKey": "builtin:google_calendar_delete",
            "config": {"event_id": "evt-1", "_names": {"event_id": "주간 회의"}}}

    with patch("tools.google_calendar.get_http_client", return_value=http):
        res = client.post(URL, json=body, headers={"X-User-Id": "user-1", "X-Google-Access-Token": "g-token"})

    assert res.status_code == 200
    assert res.json()["success"] is True
    assert res.json()["output"] == {"eventId": "evt-1", "deleted": True}
    assert http.delete.call_args.kwargs["headers"]["Authorization"] == "Bearer g-token"


def test_Slack_ACTION은_헤더_봇_토큰으로_끝까지_실행된다():
    """실제 slack_send_channel_message를 부르고 HTTP만 목킹한다. config의 token은 헤더 토큰에 진다."""
    def _ok(body):
        resp = MagicMock(status_code=200, headers={})
        resp.json.return_value = body
        return resp

    info = _ok({"ok": True, "channel": {"id": "C1", "is_channel": True, "is_private": False}})
    link = _ok({"ok": True, "permalink": "https://t.slack.com/archives/C1/p1"})
    http = AsyncMock()
    http.get = AsyncMock(side_effect=lambda url, **kw: info if url.endswith("/conversations.info") else link)
    http.post = AsyncMock(return_value=_ok({"ok": True, "channel": "C1", "ts": "1.0"}))
    body = {"nodeId": "node-1", "toolKey": "builtin:slack_send_channel_message",
            "config": {"channel": "C1", "text": "안녕", "token": "attacker", "_names": {"channel": "#general"}}}

    with patch("tools.slack_bot.get_http_client", return_value=http):
        res = _post(headers={"X-User-Id": "user-1", "X-Slack-Token": "xoxb-test"}, body=body)

    assert res.status_code == 200
    assert res.json() == {"success": True, "errorMessage": None, "errorCode": None,
                          "output": {"ts": "1.0", "channel": "C1", "permalink": "https://t.slack.com/archives/C1/p1"}}
    calls = http.get.call_args_list + http.post.call_args_list
    assert [c.kwargs["headers"]["Authorization"] for c in calls] == ["Bearer xoxb-test"] * 3


def test_Slack_ACTION은_봇_토큰이_없으면_200_실패_응답이다():
    body = {"nodeId": "node-1", "toolKey": "builtin:slack_send_channel_message", "config": {"channel": "C1", "text": "hi"}}
    res = _post(headers={"X-User-Id": "user-1", "X-Notion-Token": "secret_x"}, body=body)
    assert res.status_code == 200
    assert res.json()["success"] is False and "Slack" in res.json()["errorMessage"]


# --- Discord 봇 ---------------------------------------------------------------------------------

G_MINE, G_OTHER, D_CH, D_MSG = "111111111111111111", "999999999999999999", "333333333333333333", "444444444444444444"


def _discord_http(channel_guild):
    """진짜 httpx + MockTransport. GET 채널 조회는 channel_guild 서버의 텍스트 채널, POST는 보낸 메시지."""
    import httpx

    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"id": D_CH, "type": 0, "guild_id": channel_guild})
        return httpx.Response(200, json={"id": D_MSG, "channel_id": D_CH})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


def test_Discord_ACTION은_헤더_봇_토큰과_서버_목록으로_끝까지_실행된다():
    """config의 token·allowed_guild_ids는 헤더 값에 진다. guild_id(직속 칸)·_names는 도구에 가지 않는다."""
    http, seen = _discord_http(G_MINE)
    body = {"nodeId": "node-1", "toolKey": "builtin:discord_send_channel_message",
            "config": {"guild_id": G_MINE, "channel_id": D_CH, "content": "안녕", "token": "attacker",
                       "allowed_guild_ids": [G_OTHER], "_names": {"channel_id": "#general"}}}

    with patch("tools.discord_bot.get_http_client", return_value=http):
        res = _post(headers={"X-User-Id": "user-1", "X-Discord-Token": "bot-t",
                             "X-Discord-Guild-Ids": f" {G_OTHER}x , {G_MINE} ,"}, body=body)

    assert res.json() == {"success": True, "errorMessage": None, "errorCode": None,
                          "output": {"id": D_MSG, "channel_id": D_CH,
                                     "url": f"https://discord.com/channels/{G_MINE}/{D_CH}/{D_MSG}"}}
    assert [r.headers["authorization"] for r in seen] == ["Bot bot-t"] * 2


@pytest.mark.parametrize("header_ids, config_ids", [
    (G_MINE, [G_OTHER]),     # config로 남의 서버를 허용 목록에 넣어도
    ("", [G_OTHER]),         # 헤더 목록이 비었는데 config로 채워도
])
def test_Discord_ACTION은_config의_allowed_guild_ids로_남의_서버에_쓰지_못한다(header_ids, config_ids):
    """불변식 (a): 주입값이 config를 덮는다 — 채널이 G_OTHER 서버라 거절되고 POST는 없다."""
    http, seen = _discord_http(G_OTHER)
    body = {"nodeId": "node-1", "toolKey": "builtin:discord_send_channel_message",
            "config": {"channel_id": D_CH, "content": "hi", "allowed_guild_ids": config_ids}}

    with patch("tools.discord_bot.get_http_client", return_value=http):
        res = _post(headers={"X-User-Id": "user-1", "X-Discord-Token": "bot-t", "X-Discord-Guild-Ids": header_ids},
                    body=body)

    assert res.status_code == 200 and res.json()["success"] is False
    assert res.json()["errorCode"] == "ACTION_TOOL_FAILED"
    assert all(r.method == "GET" for r in seen)


def test_Discord_ACTION은_봇_토큰이_없으면_200_실패_응답이다():
    body = {"nodeId": "node-1", "toolKey": "builtin:discord_send_channel_message",
            "config": {"channel_id": D_CH, "content": "hi", "token": "attacker"}}
    res = _post(headers={"X-User-Id": "user-1", "X-Discord-Guild-Ids": G_MINE}, body=body)
    assert res.status_code == 200
    assert res.json()["success"] is False and "Discord" in res.json()["errorMessage"]


# --- Notion DB 행 -----------------------------------------------------------------------------

def test_Notion_행_수정_ACTION은_헤더_토큰으로_끝까지_실행되고_UI용_칸은_도구에_가지_않는다():
    """config.data_source_id(시그니처 밖, 드롭다운용)·_names·token은 도구에 가지 않는다 — 실행은 행이 속한 DB를 따른다."""
    from tests.test_notion_rows import PAGE, ROW, SCHEMA, _make_response
    http = AsyncMock()
    http.get = AsyncMock(side_effect=[_make_response(200, PAGE), _make_response(200, SCHEMA)])
    http.patch = AsyncMock(side_effect=[_make_response(200, PAGE)])
    body = {"nodeId": "node-1", "toolKey": "builtin:notion_update_row",
            "config": {"data_source_id": "0" * 32, "row_id": ROW, "properties": {"점수": "7"}, "token": "attacker",
                       "_names": {"data_source_id": "업무 DB", "properties": {"num": "점수"}}}}

    with patch("tools.notion_rows.get_http_client", return_value=http):
        res = _post(headers={"X-User-Id": "user-1", "X-Notion-Token": "n-token"}, body=body)

    assert res.json() == {"success": True, "errorMessage": None, "errorCode": None,
                          "output": {"pageId": ROW, "url": PAGE["url"]}}
    assert {c.kwargs["headers"]["Authorization"] for c in http.get.await_args_list + http.patch.await_args_list} \
        == {"Bearer n-token"}
    assert http.patch.await_args.kwargs["json"] == {"properties": {"num": {"number": 7}}}

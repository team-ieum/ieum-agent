"""ExecutionGuard.validate_tools — AI 노드(AgentNodeRequest.tools)와 ACTION 실행이 같이 쓰는 도구 목록 가드."""
import pytest

from api.schemas.request import AgentNodeRequest
from core.execution_guard import ExecutionGuard, ExecutionGuardError


def test_validate_tools_사설망_URL을_차단한다():
    tools = [{"name": "builtin:http_fetch", "config": {"url": "http://localhost:8000/admin"}}]
    with pytest.raises(ExecutionGuardError, match="사설망 또는 로컬호스트"):
        ExecutionGuard.validate_tools(tools)


@pytest.mark.parametrize("tool_key, label", [
    ("builtin:notion_search", "Notion"),
    ("builtin:google_sheets_read", "Google"),
    ("builtin:github_list_issues", "GitHub"),
    ("builtin:github_create_issue", "GitHub"),
    ("builtin:slack_send_channel_message", "Slack"),
    ("builtin:discord_send_channel_message", "Discord"),
])
def test_validate_tools_토큰_누락을_거부한다(tool_key, label):
    with pytest.raises(ExecutionGuardError, match=label):
        ExecutionGuard.validate_tools([{"name": tool_key}])


def test_validate_tools_토큰이_있으면_통과한다():
    ExecutionGuard.validate_tools([{"name": "builtin:github_create_issue"}], github_token="gh-token")
    ExecutionGuard.validate_tools([{"name": "builtin:notion_search"}], notion_token="n-token")
    ExecutionGuard.validate_tools([{"name": "builtin:google_sheets_read"}], google_access_token="g-token")
    ExecutionGuard.validate_tools([{"name": "builtin:slack_send_channel_message"}], slack_token="xoxb-test")
    ExecutionGuard.validate_tools([{"name": "slack"}])   # 웹훅은 봇 토큰 검사 대상이 아니다
    ExecutionGuard.validate_tools([{"name": "builtin:discord_send_channel_message"}], discord_token="bot-t")
    ExecutionGuard.validate_tools([{"name": "discord"}])


def test_validate_tools_다른_서비스_토큰으로는_통과하지_못한다():
    """notion·github 함수는 둘 다 `token` 파라미터를 쓴다. 헤더는 따로라 서로의 토큰이 대신할 수 없다."""
    with pytest.raises(ExecutionGuardError, match="Notion"):
        ExecutionGuard.validate_tools([{"name": "builtin:notion_search"}], github_token="gh-token")
    with pytest.raises(ExecutionGuardError, match="GitHub"):
        ExecutionGuard.validate_tools(
            [{"name": "builtin:github_list_issues"}], notion_token="n-token", google_access_token="g-token")
    with pytest.raises(ExecutionGuardError, match="Slack"):
        ExecutionGuard.validate_tools(
            [{"name": "builtin:slack_send_channel_message"}], notion_token="n-token", github_token="gh-token")
    with pytest.raises(ExecutionGuardError, match="Discord"):
        ExecutionGuard.validate_tools([{"name": "builtin:discord_send_channel_message"}], slack_token="xoxb-test")


@pytest.mark.parametrize("tools", [None, []])
def test_validate_tools_빈_목록은_통과한다(tools):
    ExecutionGuard.validate_tools(tools)


def test_validate_execution은_validate_tools와_같게_거부한다():
    request = AgentNodeRequest(nodeId="n1", renderedPrompt="p", agentType="react",
                               tools=[{"name": "builtin:github_list_issues"}])
    with pytest.raises(ExecutionGuardError, match="GitHub"):
        ExecutionGuard.validate_execution(request)
    ExecutionGuard.validate_execution(request, github_token="gh-token")


def test_validate_execution은_simple_노드의_Slack_봇_도구를_거부한다():
    """run_simple_agent는 봇 토큰을 바인딩하지 않는다 — 실리면 token 인자가 LLM 스키마에 드러난다."""
    request = AgentNodeRequest(nodeId="n1", renderedPrompt="p", agentType="simple",
                               tools=[{"name": "builtin:slack_send_channel_message"}])
    with pytest.raises(ExecutionGuardError, match="react"):
        ExecutionGuard.validate_execution(request, slack_token="xoxb-test")
    ExecutionGuard.validate_execution(request.model_copy(update={"agentType": "react"}), slack_token="xoxb-test")


def test_validate_execution은_simple_노드의_Discord_봇_도구를_거부한다():
    """simple 경로는 바인딩하지 않는다 — 실리면 token·allowed_guild_ids가 LLM 스키마에 드러난다."""
    request = AgentNodeRequest(nodeId="n1", renderedPrompt="p", agentType="simple",
                               tools=[{"name": "builtin:discord_send_channel_message"}])
    with pytest.raises(ExecutionGuardError, match="react"):
        ExecutionGuard.validate_execution(request, discord_token="bot-t")
    ExecutionGuard.validate_execution(request.model_copy(update={"agentType": "react"}), discord_token="bot-t")
    with pytest.raises(ExecutionGuardError, match="Discord"):
        ExecutionGuard.validate_execution(request.model_copy(update={"agentType": "react"}))


@pytest.mark.parametrize("tools", [[], None])
def test_AI_노드_GitHub_계약_tools_빈_목록은_토큰이_없어도_통과한다(tools):
    """AI 노드의 GitHub는 tools: [] + 서브에이전트 마운트다. 이 가드가 그 노드를 막으면 GitHub AI 노드가 전부 깨진다."""
    request = AgentNodeRequest(nodeId="n1", renderedPrompt="p", agentType="react", tools=tools)
    ExecutionGuard.validate_execution(request)
    ExecutionGuard.validate_execution(request, github_token="gh-token")


@pytest.mark.parametrize("url", [
    "http://[::1]/x", "http://[::ffff:127.0.0.1]/x", "http://[fd00::1]/x", "http://127.0.0.1/x",
    "http://[fe80::1]/x", "http://0.0.0.0/x", "http://[::]/x",
])
def test_IP_리터럴_사설_루프백_매핑_주소를_DNS_조회_전에_차단한다(url, monkeypatch):
    lookups = []
    monkeypatch.setattr("core.execution_guard.socket.gethostbyname", lambda h: lookups.append(h) or "8.8.8.8")
    with pytest.raises(ExecutionGuardError):
        ExecutionGuard.validate_tools([{"name": "builtin:http_fetch", "config": {"url": url}}])
    assert lookups == []


def test_공인_IP_리터럴은_DNS_조회_없이_통과한다(monkeypatch):
    lookups = []
    monkeypatch.setattr("core.execution_guard.socket.gethostbyname", lambda h: lookups.append(h) or "8.8.8.8")
    ExecutionGuard.validate_tools([{"name": "builtin:http_fetch", "config": {"url": "http://8.8.8.8/x"}}])
    assert lookups == []

import contextlib
import inspect
import logging
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StreamableHTTPConnectionParams

from agents.base import _safe_close_mcp

logger = logging.getLogger(__name__)

_CAPABILITY = (
    "GitHub MCP 서버를 통해 리포지토리·이슈·Pull Request·GitHub Actions를 관리한다. "
    "코드 검색, 파일 조회, PR 생성·리뷰, 이슈 트래킹, 워크플로우 실행 등을 담당한다."
)

# fast-path(단일 에이전트)에서 github_agent를 AgentTool로 감싸지 않고 도구만 평탄화할 때,
# instruction 손실 없이 이 규칙 텍스트를 단일 에이전트 instruction에 직접 병합하기 위해 export한다.
GITHUB_PR_RULES = (
    "\n\n## PR 목록 조회 규칙\n"
    "- 특정 리포지토리의 PR 목록을 조회할 때는 검색(search) 도구 대신 PR 목록(list pull requests) "
    "도구를 사용한다. (owner/repo 지정, state=closed, 최신순). 검색 도구는 인덱싱·날짜 경계로 "
    "결과가 누락될 수 있어 신뢰하지 않는다.\n"
    "- '머지된 PR'을 요구받으면, 목록을 받은 뒤 merged_at 필드가 null이 아닌 항목만 남기고, "
    "기간 조건(예: 이번 주)이 있으면 merged_at 날짜로 직접 필터링한다.\n"
    "- 최종 응답은 HTTP 헤더·상태코드 등 부가 메타를 제외하고, 각 PR의 number·title·merged_at·html_url만 "
    "담은 간결한 JSON 목록으로 반환한다. (조회된 PR이 없으면 빈 목록을 반환한다.)"
)

_INSTRUCTION = _CAPABILITY + GITHUB_PR_RULES


async def build_github_agent(
    model: str,
    github_token: str | None,
    stack: contextlib.AsyncExitStack,
) -> tuple[LlmAgent, list]:
    """GitHubAgent 빌드. 토큰이 없으면 도구 없는 에이전트 반환."""
    if not github_token:
        instruction = _INSTRUCTION + " 현재 GitHub 토큰이 없으므로 GitHub 도구를 사용할 수 없다."
        return LlmAgent(
            name="github_agent",
            model=model,
            instruction=instruction,
            tools=[]
        ), []

    mcp = MCPToolset(
        connection_params=StreamableHTTPConnectionParams(
            url="https://api.githubcopilot.com/mcp/",
            headers={
                "Authorization": f"Bearer {github_token}",
                "X-MCP-Toolsets": "repos,issues,pull_requests,actions",
            },
        )
    )
    stack.push_async_callback(_safe_close_mcp, mcp)

    res = mcp.get_tools()
    import asyncio
    try:
        tools = await asyncio.wait_for(res if inspect.isawaitable(res) else res, timeout=5.0)
        mcps = [mcp]
        logger.info("GitHub Copilot MCP 도구 연결 성공")
    except Exception as e:
        logger.warning(
            "GitHub Copilot MCP 연결 실패 (권한 부족 또는 타임아웃). 로컬 Python GitHub API 도구로 폴백합니다. 에러: %s",
            str(e)
        )
        from core.workflow_chat import _bind_token
        from tools.github import github_list_orgs, github_list_repos, github_list_issues, github_list_pull_requests
        from google.adk.tools.function_tool import FunctionTool
        
        tools = [
            FunctionTool(_bind_token(github_list_orgs, token=github_token)),
            FunctionTool(_bind_token(github_list_repos, token=github_token)),
            FunctionTool(_bind_token(github_list_issues, token=github_token)),
            FunctionTool(_bind_token(github_list_pull_requests, token=github_token)),
        ]
        mcps = []

    agent = LlmAgent(
        name="github_agent",
        model=model,
        instruction=_INSTRUCTION,
        tools=tools
    )
    return agent, mcps

from google.adk.agents import LlmAgent
from google.adk.tools.function_tool import FunctionTool
from tools import _bind_config
from tools.slack import send_slack_message
from tools.discord import send_discord_webhook

_INSTRUCTION = (
    "Slack 메시지 발송(send_slack_message)과 Discord 웹훅 메시지 발송(send_discord_webhook)을 처리한다. "
    "Gmail은 GoogleAgent가 담당한다."
)

async def build_communication_agent(
    model: str,
    webhook_configs: dict | None = None,
) -> tuple[LlmAgent, list]:
    """CommAgent 빌드. MCP 없이 builtin 도구만 사용.

    webhook_configs: {"send_slack_message": {...}, "send_discord_webhook": {...}} 형태로
    노드 request.tools에서 추출한 webhook_url 포함 config. 제공되면 webhook_url을 바인딩하여
    comm_agent에 위임됐을 때도 실제 발송이 되도록 한다. (멀티 에이전트 경로에서 메인이
    comm_agent로 위임하면 unbound 도구가 webhook_url을 되묻던 간헐적 발송 실패를 방지)"""
    webhook_configs = webhook_configs or {}
    tools = [
        FunctionTool(_bind_config(send_slack_message, webhook_configs.get("send_slack_message"))),
        FunctionTool(_bind_config(send_discord_webhook, webhook_configs.get("send_discord_webhook"))),
    ]
    agent = LlmAgent(
        name="comm_agent",
        model=model,
        instruction=_INSTRUCTION,
        tools=tools
    )
    return agent, []

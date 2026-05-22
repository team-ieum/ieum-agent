from google.adk.agents import LlmAgent
from google.adk.tools.function_tool import FunctionTool
from tools.slack import send_slack_message
from tools.discord import send_discord_webhook

_INSTRUCTION = (
    "Slack 메시지 발송(send_slack_message)과 Discord 웹훅 메시지 발송(send_discord_webhook)을 처리한다. "
    "Gmail은 GoogleAgent가 담당한다."
)

async def build_communication_agent(model: str) -> tuple[LlmAgent, list]:
    """CommAgent 빌드. MCP 없이 builtin 도구만 사용."""
    tools = [FunctionTool(send_slack_message), FunctionTool(send_discord_webhook)]
    agent = LlmAgent(
        name="comm_agent",
        model=model,
        instruction=_INSTRUCTION,
        tools=tools
    )
    return agent, []

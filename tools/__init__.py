from google.adk.tools.function_tool import FunctionTool

from tools.slack import send_slack_message
from tools.discord import send_discord_webhook
from tools.gmail import send_gmail
from tools.mcp import call_mcp_tool


def get_tools_for_request(tool_names: list) -> list:
    tool_map = {
        "slack": FunctionTool(send_slack_message),
        "discord": FunctionTool(send_discord_webhook),
        "gmail": FunctionTool(send_gmail),
        "mcp": FunctionTool(call_mcp_tool),
    }
    result = []
    for item in tool_names:
        name = item.get("name") if isinstance(item, dict) else item
        if name in tool_map:
            result.append(tool_map[name])
    return result

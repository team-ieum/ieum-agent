from google.adk.tools.function_tool import FunctionTool

from tools.slack import send_slack_message
from tools.discord import send_discord_webhook
from tools.gmail import send_gmail
from tools.mcp import call_mcp_tool
from tools.http_fetch import http_fetch
from tools.utils import json_parse, text_extract, date_format
from tools.notion import (
    notion_create_page,
    notion_read_page,
    notion_search,
    notion_update_page,
    notion_append_block,
)
from tools.google_sheets import google_sheets_read, google_sheets_write
from tools.google_calendar import google_calendar_create, google_calendar_list
from tools.google_drive import google_drive_read, google_drive_upload


def get_tools_for_request(tool_names: list) -> list:
    tool_map = {
        "slack": FunctionTool(send_slack_message),
        "discord": FunctionTool(send_discord_webhook),
        "gmail": FunctionTool(send_gmail),
        "mcp": FunctionTool(call_mcp_tool),
        "builtin:http_fetch": FunctionTool(http_fetch),
        "builtin:notion_create_page": FunctionTool(notion_create_page),
        "builtin:notion_read_page": FunctionTool(notion_read_page),
        "builtin:notion_search": FunctionTool(notion_search),
        "builtin:notion_update_page": FunctionTool(notion_update_page),
        "builtin:notion_append_block": FunctionTool(notion_append_block),
        "builtin:google_sheets_read": FunctionTool(google_sheets_read),
        "builtin:google_sheets_write": FunctionTool(google_sheets_write),
        "builtin:google_calendar_create": FunctionTool(google_calendar_create),
        "builtin:google_calendar_list": FunctionTool(google_calendar_list),
        "builtin:google_drive_read": FunctionTool(google_drive_read),
        "builtin:google_drive_upload": FunctionTool(google_drive_upload),
        "builtin:json_parse": FunctionTool(json_parse),
        "builtin:text_extract": FunctionTool(text_extract),
        "builtin:date_format": FunctionTool(date_format),
    }
    result = []
    for item in tool_names:
        name = item.get("name") if isinstance(item, dict) else item
        if name in tool_map:
            result.append(tool_map[name])
    return result

import functools
import inspect

from google.adk.tools.function_tool import FunctionTool

from tools.slack import send_slack_message
from tools.discord import send_discord_webhook
from tools.gmail import send_gmail
from tools.mcp import call_mcp_tool
from tools.http_fetch import http_fetch
from tools.web_search import web_search
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
from tools.workflow_context import workflow_context


def _bind_config(fn, config: dict | None):
    if not config:
        return fn

    signature = inspect.signature(fn)
    bindable_args = {
        key: value
        for key, value in config.items()
        if key in signature.parameters
    }

    if not bindable_args:
        return fn

    bound_fn = functools.partial(fn, **bindable_args)
    bound_fn.__name__ = fn.__name__
    bound_fn.__doc__ = fn.__doc__
    bound_fn.__signature__ = signature.replace(parameters=[
        parameter
        for name, parameter in signature.parameters.items()
        if name not in bindable_args
    ])
    return bound_fn


def get_tools_for_request(tool_names: list) -> list:
    tool_map = {
        "slack": send_slack_message,
        "discord": send_discord_webhook,
        "gmail": send_gmail,
        "mcp": call_mcp_tool,
        "builtin:http_fetch": http_fetch,
        "builtin:web_search": web_search,
        "builtin:notion_create_page": notion_create_page,
        "builtin:notion_read_page": notion_read_page,
        "builtin:notion_search": notion_search,
        "builtin:notion_update_page": notion_update_page,
        "builtin:notion_append_block": notion_append_block,
        "builtin:google_sheets_read": google_sheets_read,
        "builtin:google_sheets_write": google_sheets_write,
        "builtin:google_calendar_create": google_calendar_create,
        "builtin:google_calendar_list": google_calendar_list,
        "builtin:google_drive_read": google_drive_read,
        "builtin:google_drive_upload": google_drive_upload,
        "builtin:workflow_context": workflow_context,
        "builtin:json_parse": json_parse,
        "builtin:text_extract": text_extract,
        "builtin:date_format": date_format,
    }
    result = []
    for item in tool_names:
        name = item.get("name") if isinstance(item, dict) else item
        if name in tool_map:
            config = item.get("config") if isinstance(item, dict) else None
            configured_fn = _bind_config(tool_map[name], config)
            result.append(FunctionTool(configured_fn))
    return result

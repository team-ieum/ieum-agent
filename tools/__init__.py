import functools
import inspect
import re

from google.adk.tools.function_tool import FunctionTool

from tools.slack import send_slack_message
from tools.discord import send_discord_webhook
from tools.gmail import send_gmail
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


def _make_partial(fn, **bound_args):
    """fn의 일부 파라미터를 바인딩하고 __signature__에서 해당 파라미터를 제거한 partial을 반환한다."""
    sig = inspect.signature(fn)
    p = functools.partial(fn, **bound_args)
    p.__name__ = fn.__name__
    # docstring의 Args 설명에서 바인딩된 파라미터 라인을 제거한다.
    # 제거하지 않으면 LLM이 docstring을 읽고 (이미 주입된) 인자를 직접 채워야 한다고
    # 오판하여 도구 호출 자체를 포기한다. (예: webhook_url 바인딩됐는데 LLM이 "URL 없음" 거부)
    _doc = fn.__doc__
    if _doc:
        for _k in bound_args:
            _doc = re.sub(rf"^[ \t]*{re.escape(_k)}\s*[:(].*\n?", "", _doc, flags=re.MULTILINE)
    p.__doc__ = _doc
    p.__signature__ = sig.replace(
        parameters=[v for k, v in sig.parameters.items() if k not in bound_args]
    )
    # ADK JSON_SCHEMA_FOR_FUNC_DECL 기능은 __annotations__로 타입 정보를 읽는다.
    # functools.partial은 __annotations__를 복사하지 않으므로 명시적으로 설정한다.
    p.__annotations__ = {
        k: v for k, v in getattr(fn, "__annotations__", {}).items()
        if k not in bound_args
    }
    return p


def _bind_config(fn, config: dict | None):
    if not config:
        return fn

    sig = inspect.signature(fn)
    bindable_args = {
        key: value
        for key, value in config.items()
        if key in sig.parameters
    }

    if not bindable_args:
        return fn

    return _make_partial(fn, **bindable_args)


_TOOL_MAP: dict = {
    "slack": send_slack_message,
    "discord": send_discord_webhook,
    "gmail": send_gmail,
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


def get_tools_for_request(tool_names: list) -> list:
    result = []
    for item in tool_names:
        name = item.get("name") if isinstance(item, dict) else item
        if name == "mcp":
            config = item.get("config") if isinstance(item, dict) else {}
            server_url = config.get("server_url") or config.get("serverUrl")
            command = config.get("command")
            args = config.get("args") or []
            env = config.get("env")
            prefix = config.get("tool_name_prefix") or config.get("toolNamePrefix")
            
            if server_url:
                from google.adk.tools import McpToolset
                from google.adk.tools.mcp_tool import SseConnectionParams
                
                toolset = McpToolset(
                    connection_params=SseConnectionParams(
                        url=server_url,
                        headers=config.get("headers"),
                    ),
                    tool_name_prefix=prefix
                )
                result.append(toolset)
            elif command:
                from google.adk.tools import McpToolset
                from mcp import StdioServerParameters
                
                toolset = McpToolset(
                    connection_params=StdioServerParameters(
                        command=command,
                        args=args,
                        env=env,
                    ),
                    tool_name_prefix=prefix
                )
                result.append(toolset)
        elif name in _TOOL_MAP:
            config = item.get("config") if isinstance(item, dict) else None
            configured_fn = _bind_config(_TOOL_MAP[name], config)
            result.append(FunctionTool(configured_fn))
    return result

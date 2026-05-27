import functools
import inspect
from google.adk.tools.function_tool import FunctionTool
from tools.google_sheets import google_sheets_read, google_sheets_write
from tools.google_calendar import google_calendar_create, google_calendar_list
from tools.google_drive import google_drive_read, google_drive_upload
from tools.notion import (
    notion_create_page, notion_read_page, notion_search,
    notion_update_page, notion_append_block,
)
from tools.workflow_context import workflow_context as _workflow_context_fn

_GOOGLE_TOOL_FUNCTIONS = {
    google_sheets_read, google_sheets_write,
    google_calendar_create, google_calendar_list,
    google_drive_read, google_drive_upload,
}
_NOTION_TOOL_FUNCTIONS = {
    notion_create_page, notion_read_page, notion_search,
    notion_update_page, notion_append_block,
}
_WORKFLOW_CONTEXT_FUNCTIONS = {_workflow_context_fn}


def _get_tool_function(tool):
    return getattr(tool, "func", None) or getattr(tool, "_func", None)


def _get_base_function(fn):
    while isinstance(fn, functools.partial):
        fn = fn.func
    return fn


def _make_partial(fn, **bound_args):
    """fn의 일부 파라미터를 바인딩하고 __signature__에서 해당 파라미터를 제거한 partial을 반환한다."""
    sig = inspect.signature(fn)
    p = functools.partial(fn, **bound_args)
    p.__name__ = fn.__name__
    p.__doc__ = fn.__doc__
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


def _bind_tool_argument(tool, **bound_args):
    fn = _get_tool_function(tool)
    if fn is None:
        return tool

    bound_fn = _make_partial(fn, **bound_args)
    return FunctionTool(bound_fn)


def _bind_workflow_context(tools: list, context_data: dict) -> list:
    """workflow_context 도구의 workflow_context_data 파라미터를 실제 컨텍스트 데이터로 바인딩한다."""
    bound = []
    for tool in tools:
        fn = _get_tool_function(tool)
        if fn is not None and _get_base_function(fn) in _WORKFLOW_CONTEXT_FUNCTIONS:
            bound.append(_bind_tool_argument(tool, workflow_context_data=context_data))
        else:
            bound.append(tool)
    return bound


def _bind_google_token(tools: list, google_access_token: str) -> list:
    """Google 도구의 access_token 파라미터를 실제 토큰으로 바인딩한다."""
    bound = []
    for tool in tools:
        fn = _get_tool_function(tool)
        if fn is not None and _get_base_function(fn) in _GOOGLE_TOOL_FUNCTIONS:
            bound.append(_bind_tool_argument(tool, access_token=google_access_token))
        else:
            bound.append(tool)
    return bound


def _bind_notion_token(tools: list, notion_token: str) -> list:
    """notion_* 도구의 token 파라미터를 실제 Notion Integration Token으로 바인딩한다."""
    bound = []
    for tool in tools:
        fn = _get_tool_function(tool)
        if fn is not None and _get_base_function(fn) in _NOTION_TOOL_FUNCTIONS:
            bound.append(_bind_tool_argument(tool, token=notion_token))
        else:
            bound.append(tool)
    return bound


async def _safe_close_mcp(mcp) -> None:
    """MCPToolset 연결을 안전하게 종료합니다. 테스트의 Mock(동기) 및 실제 비동기 close 호출을 모두 지원합니다."""
    if hasattr(mcp, "close"):
        close_fn = mcp.close
        if "Mock" in type(close_fn).__name__:
            close_fn()
            return
        res = close_fn()
        if hasattr(res, "__await__"):
            await res


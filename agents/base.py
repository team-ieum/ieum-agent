import functools
import inspect
import re
from google.adk.tools.function_tool import FunctionTool
from tools.workflow_context import workflow_context as _workflow_context_fn

_WORKFLOW_CONTEXT_FUNCTIONS = {_workflow_context_fn}


# 토큰 바인딩 대상은 수동 목록 대신 모듈·시그니처로 판별한다.
# 수동 목록은 도구 추가 시 누락돼 토큰이 LLM 인자로 노출됐다(IEUM-AI-59).
def _is_google_tool(fn) -> bool:
    return fn.__module__.startswith("tools.google_") and "access_token" in inspect.signature(fn).parameters


def _is_notion_tool(fn) -> bool:
    return fn.__module__ == "tools.notion" and "token" in inspect.signature(fn).parameters


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
    # docstring의 Args 설명에서 바인딩된 파라미터 라인을 제거한다.
    # 제거하지 않으면 LLM이 docstring을 읽고 (이미 주입된) 인자를 직접 채워야 한다고
    # 오판하여 도구 호출 자체를 포기한다.
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
        if fn is not None and _is_google_tool(_get_base_function(fn)):
            bound.append(_bind_tool_argument(tool, access_token=google_access_token))
        else:
            bound.append(tool)
    return bound


def _bind_notion_token(tools: list, notion_token: str) -> list:
    """notion_* 도구의 token 파라미터를 실제 Notion Integration Token으로 바인딩한다."""
    bound = []
    for tool in tools:
        fn = _get_tool_function(tool)
        if fn is not None and _is_notion_tool(_get_base_function(fn)):
            bound.append(_bind_tool_argument(tool, token=notion_token))
        else:
            bound.append(tool)
    return bound


async def _safe_close_mcp(mcp) -> None:
    """MCPToolset 연결을 안전하게 종료합니다. 테스트의 Mock(동기) 및 실제 비동기 close 호출을 모두 지원합니다."""
    if hasattr(mcp, "close"):
        close_fn = mcp.close
        if inspect.iscoroutinefunction(close_fn):
            await close_fn()
        else:
            res = close_fn()
            if inspect.isawaitable(res):
                await res


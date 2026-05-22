import inspect
import pytest
from google.adk.tools.function_tool import FunctionTool
from agents.base import (
    _make_partial,
    _bind_google_token,
    _bind_notion_token,
    _bind_workflow_context,
)
from tools import get_tools_for_request


# ---------- _make_partial ----------

def test_make_partial_removes_bound_param_from_signature():
    """바인딩된 파라미터가 __signature__에서 제거된다."""
    def sample_fn(a: str, b: int, c: float) -> str:
        return f"{a}{b}{c}"

    partial_fn = _make_partial(sample_fn, b=42)
    params = list(inspect.signature(partial_fn).parameters.keys())
    assert "b" not in params
    assert "a" in params
    assert "c" in params


def test_make_partial_preserves_name_and_doc():
    """__name__과 __doc__이 원본과 동일하다."""
    def sample_fn(x: str):
        """sample docstring"""
        return x

    partial_fn = _make_partial(sample_fn, x="val")
    assert partial_fn.__name__ == "sample_fn"
    assert partial_fn.__doc__ == "sample docstring"


def test_make_partial_removes_bound_annotation():
    """바인딩된 파라미터의 __annotations__가 제거된다."""
    def sample_fn(a: str, token: str) -> str:
        return a

    partial_fn = _make_partial(sample_fn, token="secret")
    assert "token" not in partial_fn.__annotations__
    assert "a" in partial_fn.__annotations__


# ---------- _bind_google_token ----------

def test_bind_google_token_binds_sheets_tool():
    """google_sheets_read 도구에 access_token이 바인딩된다."""
    tools = get_tools_for_request([{"name": "builtin:google_sheets_read"}])
    bound = _bind_google_token(tools, "google-access-token")
    fn = getattr(bound[0], "func", None) or getattr(bound[0], "_func", None)
    params = list(inspect.signature(fn).parameters.keys())
    assert "access_token" not in params


def test_bind_google_token_does_not_affect_web_search():
    """web_search 도구는 변경되지 않는다."""
    tools = get_tools_for_request([{"name": "builtin:web_search"}])
    original_fn = getattr(tools[0], "func", None) or getattr(tools[0], "_func", None)
    bound = _bind_google_token(tools, "google-access-token")
    bound_fn = getattr(bound[0], "func", None) or getattr(bound[0], "_func", None)
    assert original_fn is bound_fn


# ---------- _bind_notion_token ----------

def test_bind_notion_token_binds_create_page():
    """notion_create_page 도구에 token이 바인딩된다."""
    tools = get_tools_for_request([{"name": "builtin:notion_create_page"}])
    bound = _bind_notion_token(tools, "notion-token")
    fn = getattr(bound[0], "func", None) or getattr(bound[0], "_func", None)
    params = list(inspect.signature(fn).parameters.keys())
    assert "token" not in params


def test_bind_notion_token_with_config_binding():
    """config로 일부 바인딩된 Notion 도구에도 token이 추가 바인딩된다."""
    tools = get_tools_for_request([{
        "name": "builtin:notion_create_page",
        "config": {"parent_page_id": "page-123"},
    }])
    bound = _bind_notion_token(tools, "notion-token")
    fn = getattr(bound[0], "func", None) or getattr(bound[0], "_func", None)
    params = list(inspect.signature(fn).parameters.keys())
    assert "token" not in params
    assert "parent_page_id" not in params


def test_bind_notion_token_does_not_affect_http_fetch():
    """http_fetch 도구는 변경되지 않는다."""
    tools = get_tools_for_request([{"name": "builtin:http_fetch"}])
    original_fn = getattr(tools[0], "func", None) or getattr(tools[0], "_func", None)
    bound = _bind_notion_token(tools, "notion-token")
    bound_fn = getattr(bound[0], "func", None) or getattr(bound[0], "_func", None)
    assert original_fn is bound_fn


# ---------- _bind_workflow_context ----------

def test_bind_workflow_context_binds_workflow_tool():
    """workflow_context 도구에 context_data가 바인딩된다."""
    tools = get_tools_for_request([{"name": "builtin:workflow_context"}])
    ctx = {"node-1": {"output": "test"}}
    bound = _bind_workflow_context(tools, ctx)
    fn = getattr(bound[0], "func", None) or getattr(bound[0], "_func", None)
    params = list(inspect.signature(fn).parameters.keys())
    assert "workflow_context_data" not in params


def test_bind_workflow_context_ignores_other_tools():
    """workflow_context 이외의 도구는 변경되지 않는다."""
    tools = get_tools_for_request([{"name": "builtin:web_search"}])
    original_fn = getattr(tools[0], "func", None) or getattr(tools[0], "_func", None)
    bound = _bind_workflow_context(tools, {})
    bound_fn = getattr(bound[0], "func", None) or getattr(bound[0], "_func", None)
    assert original_fn is bound_fn

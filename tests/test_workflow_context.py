import json

import pytest

from tools.workflow_context import workflow_context

SAMPLE_CONTEXT = {
    "nodes": {
        "node-1": {
            "type": "TRIGGER",
            "status": "COMPLETED",
            "output": {"body": {"message": "안녕하세요", "items": [{"name": "A"}]}},
        },
        "node-2": {
            "type": "HTTP",
            "status": "COMPLETED",
            "output": {"statusCode": 200},
        },
    },
    "trigger": {"body": {"message": "안녕하세요"}},
}


# ---------------------------------------------------------------------------
# get_node_output
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_node_output_success():
    result = await workflow_context(
        action="get_node_output",
        workflow_context_data=SAMPLE_CONTEXT,
        node_id="node-1",
    )
    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["data"] == SAMPLE_CONTEXT["nodes"]["node-1"]["output"]


@pytest.mark.asyncio
async def test_get_node_output_field_path():
    result = await workflow_context(
        action="get_node_output",
        workflow_context_data=SAMPLE_CONTEXT,
        node_id="node-1",
        field_path="body.message",
    )
    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["data"] == "안녕하세요"


@pytest.mark.asyncio
async def test_get_node_output_missing_node_id():
    result = await workflow_context(
        action="get_node_output",
        workflow_context_data=SAMPLE_CONTEXT,
    )
    parsed = json.loads(result)
    assert "error" in parsed
    assert "node_id is required" in parsed["error"]


@pytest.mark.asyncio
async def test_get_node_output_nonexistent_node():
    result = await workflow_context(
        action="get_node_output",
        workflow_context_data=SAMPLE_CONTEXT,
        node_id="node-999",
    )
    parsed = json.loads(result)
    assert "error" in parsed
    assert "node-999" in parsed["error"]


@pytest.mark.asyncio
async def test_get_node_output_nonexistent_field_path():
    result = await workflow_context(
        action="get_node_output",
        workflow_context_data=SAMPLE_CONTEXT,
        node_id="node-1",
        field_path="body.nonexistent",
    )
    parsed = json.loads(result)
    assert "error" in parsed
    assert "nonexistent" in parsed["error"]


# ---------------------------------------------------------------------------
# list_completed_nodes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_completed_nodes_success():
    result = await workflow_context(
        action="list_completed_nodes",
        workflow_context_data=SAMPLE_CONTEXT,
    )
    parsed = json.loads(result)
    assert parsed["success"] is True
    node_ids = [n["nodeId"] for n in parsed["data"]]
    assert "node-1" in node_ids
    assert "node-2" in node_ids
    assert len(parsed["data"]) == 2


@pytest.mark.asyncio
async def test_list_completed_nodes_empty_context():
    result = await workflow_context(
        action="list_completed_nodes",
        workflow_context_data={},
    )
    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["data"] == []


# ---------------------------------------------------------------------------
# get_trigger_input
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_trigger_input_success():
    result = await workflow_context(
        action="get_trigger_input",
        workflow_context_data=SAMPLE_CONTEXT,
    )
    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["data"] == SAMPLE_CONTEXT["trigger"]


@pytest.mark.asyncio
async def test_get_trigger_input_none():
    result = await workflow_context(
        action="get_trigger_input",
        workflow_context_data={},
    )
    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["data"] is None


# ---------------------------------------------------------------------------
# unknown action
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_action():
    result = await workflow_context(
        action="unknown_action",
        workflow_context_data=SAMPLE_CONTEXT,
    )
    parsed = json.loads(result)
    assert "error" in parsed
    assert "unknown_action" in parsed["error"]

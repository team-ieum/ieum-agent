import pytest
from pydantic import ValidationError
from api.schemas.request import AgentNodeRequest, McpServerConfig


# ---------- McpServerConfig ----------

def test_mcp_server_config_requires_server_url():
    """server_url 없이 생성하면 ValidationError가 발생한다."""
    with pytest.raises(ValidationError):
        McpServerConfig()


def test_mcp_server_config_headers_defaults_to_empty_dict():
    """headers 미제공 시 {} 기본값."""
    cfg = McpServerConfig(server_url="https://mcp.example.com/sse")
    assert cfg.headers == {}


def test_mcp_server_config_accepts_custom_headers():
    """커스텀 headers로 생성 성공."""
    cfg = McpServerConfig(
        server_url="https://mcp.example.com/sse",
        headers={"Authorization": "Bearer token"},
    )
    assert cfg.headers["Authorization"] == "Bearer token"


def test_mcp_server_config_model_dump():
    """.model_dump() 결과가 올바르다."""
    cfg = McpServerConfig(server_url="https://mcp.example.com/sse")
    assert cfg.model_dump() == {"server_url": "https://mcp.example.com/sse", "headers": {}}


# ---------- AgentNodeRequest mcp_servers ----------

def test_agent_node_request_mcp_servers_defaults_to_none():
    """mcp_servers 미제공 시 None."""
    req = AgentNodeRequest(nodeId="node-1", renderedPrompt="test")
    assert req.mcp_servers is None


def test_agent_node_request_backward_compatible_without_mcp_servers():
    """mcp_servers 없이 기존 필드만으로 생성 성공."""
    req = AgentNodeRequest(nodeId="node-1", renderedPrompt="test")
    assert req.nodeId == "node-1"
    assert req.mcp_servers is None


def test_agent_node_request_accepts_mcp_servers_list():
    """mcp_servers 리스트 설정 시 정상 저장된다."""
    cfg = McpServerConfig(server_url="https://test/sse")
    req = AgentNodeRequest(
        nodeId="node-1",
        renderedPrompt="test",
        mcp_servers=[cfg],
    )
    assert len(req.mcp_servers) == 1
    assert req.mcp_servers[0].server_url == "https://test/sse"

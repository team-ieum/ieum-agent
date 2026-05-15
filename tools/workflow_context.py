import json
from typing import Any

from common.error_code import ToolErrorCode


def _resolve_field_path(data: dict, field_path: str) -> tuple[bool, Any, str]:
    """
    점(.) 구분자로 된 필드 경로를 순차 접근하여 값을 반환한다.

    Returns:
        (success, value, error_message) 튜플
    """
    current = data
    parts = field_path.split(".")
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return False, None, f"Field path '{field_path}' not found (missing key: '{part}')"
        current = current[part]
    return True, current, ""


def workflow_context(
    action: str,
    workflow_context_data: dict,
    node_id: str | None = None,
    field_path: str | None = None,
) -> dict:
    """
    현재 워크플로우 실행에서 이전 노드의 결과를 조회합니다.
    다른 노드의 출력을 참조하여 의사결정에 활용할 수 있습니다.

    Args:
        action: get_node_output | list_completed_nodes | get_trigger_input
        workflow_context_data: 워크플로우 컨텍스트 데이터 (내부 바인딩, LLM에 노출 안 됨)
        node_id: 조회할 노드 ID (get_node_output 시 필수)
        field_path: 출력에서 특정 필드 경로. 예: response.category
    """
    try:
        if action == "get_node_output":
            if not node_id:
                return {"success": False, "error": "node_id is required for get_node_output"}

            nodes = workflow_context_data.get("nodes", {})
            if node_id not in nodes:
                return {"success": False, "error": f"Node '{node_id}' not found in workflow context"}

            output = nodes[node_id].get("output")

            if field_path:
                if not isinstance(output, dict):
                    return {"success": False, "error": f"Field path '{field_path}' cannot be applied: output is not an object"}
                ok, value, err = _resolve_field_path(output, field_path)
                if not ok:
                    return {"success": False, "error": err}
                return {"success": True, "data": value}

            return {"success": True, "data": output}

        elif action == "list_completed_nodes":
            nodes = workflow_context_data.get("nodes", {})
            completed = [
                {"nodeId": nid, "type": node.get("type")}
                for nid, node in nodes.items()
                if node.get("status") == "COMPLETED"
            ]
            return {"success": True, "data": completed}

        elif action == "get_trigger_input":
            trigger = workflow_context_data.get("trigger")
            return {"success": True, "data": trigger}

        else:
            return {"success": False, "error": f"Unknown action: '{action}'. Supported: get_node_output, list_completed_nodes, get_trigger_input"}

    except Exception as e:
        return {"success": False, "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (workflow_context: {str(e)})"}

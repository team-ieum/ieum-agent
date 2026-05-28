import pytest
from core.validators.workflow_validator import WorkflowValidator, WorkflowValidationError
from api.schemas.generate_workflow import WorkflowNode

# --- 정상 워크플로우 피스처 ---
VALID_WORKFLOW = {
    "nodes": [
        {
            "id": "node-1",
            "type": "TRIGGER",
            "label": "매일 아침 9시 트리거",
            "config": {"triggerType": "SCHEDULE", "cron": "0 9 * * *"}
        },
        {
            "id": "node-2",
            "type": "AI",
            "label": "뉴스 수집",
            "config": {
                "llmProvider": "GEMINI",
                "agentType": "react",
                "tools": [{"name": "builtin:web_search"}],
                "prompt": "최신 뉴스 수집해줘"
            }
        },
        {
            "id": "node-3",
            "type": "TRANSFORM",
            "label": "데이터 변환",
            "config": {
                "mappings": {
                    "summary": "{{nodes.node-2.output.output}}"
                }
            }
        }
    ],
    "edges": [
        {"source": "node-1", "target": "node-2"},
        {"source": "node-2", "target": "node-3"}
    ]
}

# --- 1. 정상 케이스 테스트 ---
def test_valid_workflow_passes():
    # 예외 발생 없이 통과해야 함
    WorkflowValidator.validate(VALID_WORKFLOW["nodes"], VALID_WORKFLOW["edges"])

# --- 2. TRIGGER 누락 및 초과 테스트 ---
def test_missing_trigger_raises():
    invalid_nodes = [
        {
            "id": "node-2",
            "type": "AI",
            "label": "뉴스 수집",
            "config": {"llmProvider": "GEMINI"}
        }
    ]
    with pytest.raises(WorkflowValidationError) as excinfo:
        WorkflowValidator.validate(invalid_nodes, [])
    assert "반드시 TRIGGER 노드로 시작해야 합니다" in str(excinfo.value)

def test_first_node_not_trigger_raises():
    invalid_nodes = [
        {
            "id": "node-2",
            "type": "AI",
            "label": "뉴스 수집",
            "config": {"llmProvider": "GEMINI"}
        },
        {
            "id": "node-1",
            "type": "TRIGGER",
            "label": "매일 아침 9시 트리거",
            "config": {"triggerType": "SCHEDULE", "cron": "0 9 * * *"}
        }
    ]
    with pytest.raises(WorkflowValidationError) as excinfo:
        WorkflowValidator.validate(invalid_nodes, [])
    assert "반드시 TRIGGER 노드로 시작해야 합니다" in str(excinfo.value)

def test_multiple_triggers_raises():
    invalid_nodes = [
        {
            "id": "node-1",
            "type": "TRIGGER",
            "label": "매일 아침 9시 트리거",
            "config": {"triggerType": "SCHEDULE", "cron": "0 9 * * *"}
        },
        {
            "id": "node-2",
            "type": "TRIGGER",
            "label": "수동 트리거",
            "config": {"triggerType": "MANUAL"}
        }
    ]
    edges = [{"source": "node-1", "target": "node-2"}]
    with pytest.raises(WorkflowValidationError) as excinfo:
        WorkflowValidator.validate(invalid_nodes, edges)
    assert "오직 1개의 TRIGGER 노드만 허용됩니다" in str(excinfo.value)

# --- 3. SCHEDULE 크론 구문 오류 테스트 ---
def test_invalid_cron_fields_raises():
    invalid_nodes = [
        {
            "id": "node-1",
            "type": "TRIGGER",
            "label": "잘못된 크론 트리거",
            "config": {"triggerType": "SCHEDULE", "cron": "0 9 * *"} # 4자리만 있음
        }
    ]
    with pytest.raises(WorkflowValidationError) as excinfo:
        WorkflowValidator.validate(invalid_nodes, [])
    assert "표준 5필드(분 시 일 월 요일) 형식이어야 합니다" in str(excinfo.value)

def test_invalid_cron_chars_raises():
    invalid_nodes = [
        {
            "id": "node-1",
            "type": "TRIGGER",
            "label": "잘못된 문자 크론 트리거",
            "config": {"triggerType": "SCHEDULE", "cron": "abc 9 * * *"} # abc 문자 포함
        }
    ]
    with pytest.raises(WorkflowValidationError) as excinfo:
        WorkflowValidator.validate(invalid_nodes, [])
    assert "올바른 cron 구문이 아닙니다" in str(excinfo.value)

# --- 4. HTTP 노드로 Notion/Slack 직접 호출 오용 테스트 ---
def test_prohibited_http_domain_notion_raises():
    nodes = [
        {
            "id": "node-1",
            "type": "TRIGGER",
            "label": "트리거",
            "config": {"triggerType": "MANUAL"}
        },
        {
            "id": "node-2",
            "type": "HTTP",
            "label": "Notion API 직접 호출 오용",
            "config": {
                "method": "POST",
                "url": "https://api.notion.com/v1/pages",
                "headers": {"Authorization": "Bearer key"}
            }
        }
    ]
    edges = [{"source": "node-1", "target": "node-2"}]
    with pytest.raises(WorkflowValidationError) as excinfo:
        WorkflowValidator.validate(nodes, edges)
    assert "직접 HTTP 노드로 호출하지 말고" in str(excinfo.value)

# --- 5. 존재하지 않는 노드 연결 오류 테스트 ---
def test_missing_node_in_edges_raises():
    nodes = [
        {
            "id": "node-1",
            "type": "TRIGGER",
            "label": "트리거",
            "config": {"triggerType": "MANUAL"}
        }
    ]
    edges = [{"source": "node-1", "target": "node-999"}] # node-999 없음
    with pytest.raises(WorkflowValidationError) as excinfo:
        WorkflowValidator.validate(nodes, edges)
    assert "도착 노드 ID 'node-999'가 존재하지 않습니다" in str(excinfo.value)

# --- 6. 트리거 노드로 다시 되돌아가는 연결 차단 테스트 ---
def test_edge_pointing_to_trigger_raises():
    nodes = [
        {
            "id": "node-1",
            "type": "TRIGGER",
            "label": "트리거",
            "config": {"triggerType": "MANUAL"}
        },
        {
            "id": "node-2",
            "type": "AI",
            "label": "AI 노드",
            "config": {"llmProvider": "OPENAI"}
        }
    ]
    edges = [
        {"source": "node-1", "target": "node-2"},
        {"source": "node-2", "target": "node-1"} # node-1(TRIGGER)로 향함
    ]
    with pytest.raises(WorkflowValidationError) as excinfo:
        WorkflowValidator.validate(nodes, edges)
    assert "다른 노드의 도착지(target)가 될 수 없습니다" in str(excinfo.value)

# --- 7. 고아 노드(도달 불가 노드) 존재 테스트 ---
def test_unreachable_orphan_node_raises():
    nodes = [
        {
            "id": "node-1",
            "type": "TRIGGER",
            "label": "트리거",
            "config": {"triggerType": "MANUAL"}
        },
        {
            "id": "node-2",
            "type": "AI",
            "label": "연결된 노드",
            "config": {"llmProvider": "OPENAI"}
        },
        {
            "id": "node-3",
            "type": "AI",
            "label": "고아 노드",
            "config": {"llmProvider": "OPENAI"}
        }
    ]
    edges = [
        {"source": "node-1", "target": "node-2"}
        # node-3으로 향하는 연결이 아예 없음
    ]
    with pytest.raises(WorkflowValidationError) as excinfo:
        WorkflowValidator.validate(nodes, edges)
    assert "도달할 수 없는 고아 노드가 발견되었습니다" in str(excinfo.value)

# --- 8. AI 노드 변수 참조 문법 오류 테스트 ---
def test_mismatched_brackets_raises():
    nodes = [
        {
            "id": "node-1",
            "type": "TRIGGER",
            "label": "트리거",
            "config": {"triggerType": "MANUAL"}
        },
        {
            "id": "node-2",
            "type": "AI",
            "label": "AI 노드",
            "config": {
                "llmProvider": "OPENAI",
                "prompt": "{{nodes.node-1.output.triggeredAt" # 중괄호가 닫히지 않음
            }
        }
    ]
    edges = [{"source": "node-1", "target": "node-2"}]
    with pytest.raises(WorkflowValidationError) as excinfo:
        WorkflowValidator.validate(nodes, edges)
    assert "이중 중괄호 괄호 짝이 맞지 않습니다" in str(excinfo.value)

def test_invalid_reference_format_raises():
    nodes = [
        {
            "id": "node-1",
            "type": "TRIGGER",
            "label": "트리거",
            "config": {"triggerType": "MANUAL"}
        },
        {
            "id": "node-2",
            "type": "AI",
            "label": "AI 노드",
            "config": {
                "llmProvider": "OPENAI",
                "prompt": "{{nodes.node-1.triggeredAt}}" # .output 누락
            }
        }
    ]
    edges = [{"source": "node-1", "target": "node-2"}]
    with pytest.raises(WorkflowValidationError) as excinfo:
        WorkflowValidator.validate(nodes, edges)
    assert "형식이 올바르지 않습니다" in str(excinfo.value)

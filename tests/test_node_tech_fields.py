"""노드 기술정보 필드(IEUM-AI-55 Task 2): AI 노드 config.model + 앱 노드 config.serviceType.

FE 노드 카드가 표시하는 값이고 backend는 agent 응답 노드를 그대로 저장하므로,
agent가 넣지 않으면 그 필드는 영영 존재하지 않는다.
serviceType은 폐지됐고 실행에 관여하지 않는다.
"""
import pytest

from core import template_registry as tr
from tests.catalog_paths import apps


# --- model: 기본 모델 계약 -------------------------------------------------------

def test_default_model_contract_values():
    """노드에 실릴 기본 모델명은 backend GET /api/v1/providers의 ModelInfo.id와 맞춰야 하는 계약이다.
    배포 환경변수가 덮어쓸 수 있으므로 여기서는 코드 기본값(계약 기준선)을 고정한다."""
    from core.config import Settings

    assert Settings.model_fields["CLAUDE_DEFAULT_MODEL"].default == "claude-sonnet-5-5"
    assert Settings.model_fields["OPENAI_DEFAULT_MODEL"].default == "gpt-5.6-terra"
    assert Settings.model_fields["GEMINI_DEFAULT_MODEL"].default == "gemini-3.7-flash"


# --- serviceType: 템플릿 상수 ---------------------------------------------------

def test_no_node_or_entry_carries_service_type():
    """config.serviceType은 폐지됐다(spec §4) — 항목 fixed에도, 입력 칸에도 없다."""
    for tpl in tr.all_entries():
        assert "serviceType" not in tpl["fixed"]["config"], tpl["id"]
        assert "config.serviceType" not in {f["path"] for f in tpl["inputFields"]}, tpl["id"]


def test_invalid_service_type_rejected_by_schema_gate():
    """허용 목록 밖 serviceType은 템플릿 로드 단계에서 막힌다(오타 드리프트 게이트)."""
    bad = {
        "id": "ai.bogus", "node_type": "AI", "tool_key": None, "app": "SLACKK",
        "tags": ["x"], "menu": "m", "fixed": {"type": "AI", "config": {}},
    }
    with pytest.raises(tr.TemplateSchemaError, match="app"):
        tr._validate_template(bad, bad["id"], tr._tool_map_keys(), apps())


# --- 실행 경로 무관성 -----------------------------------------------------------

def test_execute_request_ignores_service_type():
    """/v1/execute 요청 스키마에 serviceType이 없고, 붙여 보내도 요청 내용이 달라지지 않는다."""
    from api.schemas.request import AgentNodeRequest

    assert "serviceType" not in AgentNodeRequest.model_fields
    base = {"nodeId": "node-1", "renderedPrompt": "안녕"}
    assert AgentNodeRequest(**base).model_dump() == \
        AgentNodeRequest(**{**base, "serviceType": "SLACK"}).model_dump()


# serviceType이 실행에 스며들지 않는다는 보장은 test_execute_request_ignores_service_type이
# 동작으로 검증한다. 소스 텍스트 grep은 정확한 주석에도 걸려 '올바른 주석 삭제'가 해결책이 된다.


def test_ai_template_event_rule():
    """AI 노드 event 규칙(spec §4): 도구 없는 simple AI = ai.reasoning, 도구가 있거나 프리셋 모양 = ai.agent."""
    seen = set()
    for tpl in tr._generation_templates().values():
        if tpl["node_type"] != "AI":
            continue
        cfg = tpl["fixed"]["config"]
        simple = cfg.get("agentType") == "simple" and not cfg.get("tools")
        assert tpl["fixed"]["event"] == ("ai.reasoning" if simple else "ai.agent"), tpl["id"]
        seen.add(tpl["fixed"]["event"])
    assert seen == {"ai.reasoning", "ai.agent"}

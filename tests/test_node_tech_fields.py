"""노드 기술정보 필드(IEUM-AI-55 Task 2): AI 노드 config.model + 앱 노드 config.serviceType.

FE 노드 카드가 표시하는 값이고 backend는 agent 응답 노드를 그대로 저장하므로,
agent가 넣지 않으면 그 필드는 영영 존재하지 않는다.
model은 LLM이 아니라 시스템이 주입하고, serviceType은 템플릿 상수이며 실행에 관여하지 않는다.
"""
import json
import os

import pytest

from core import template_registry as tr
from core.provider_config import resolve_model
from core.template_registry import (
    SlotFillError,
    dehydrate_node,
    hydrate_node,
    hydrate_nodes,
    service_type_for_node,
    slot_catalog_text,
)


# 템플릿별 기대 serviceType. tool_key/노드 내용으로 판단한 앱 소속이며,
# 여기 없는 AI 템플릿(ai.reasoning/ai.web_search/ai.http_fetch/ai.mcp)은 앱 노드가 아니다.
EXPECTED_SERVICE_TYPES = {
    "ai.slack_send": "SLACK",
    "ai.discord_send": "DISCORD",
    "ai.github_query": "GITHUB",
    "ai.notion_append_block": "NOTION",
    "ai.notion_create_page": "NOTION",
    "ai.notion_query_database": "NOTION",
    "ai.notion_read_page": "NOTION",
    "ai.notion_search": "NOTION",
    "ai.notion_update_page": "NOTION",
    "ai.gmail_send": "GOOGLE",
    "ai.google_calendar_create": "GOOGLE",
    "ai.google_calendar_list": "GOOGLE",
    "ai.google_calendar_update": "GOOGLE",
    "ai.google_drive_read": "GOOGLE",
    "ai.google_drive_upload": "GOOGLE",
    "ai.google_sheets_append": "GOOGLE",
    "ai.google_sheets_read": "GOOGLE",
    "ai.google_sheets_write": "GOOGLE",
}

NON_APP_AI_TEMPLATES = {"ai.reasoning", "ai.web_search", "ai.http_fetch", "ai.mcp"}

_SLOTS = {
    "ai.reasoning": {"prompt": "요약해줘"},
    "ai.slack_send": {"prompt": "슬랙으로 보내줘"},
    "ai.mcp": {"prompt": "도구 호출", "catalogId": "srv-1"},
}


def _draft(template_id: str, **extra_slots) -> dict:
    slots = {"label": "테스트", "description": "이 노드가 하는 일을 쉽게 설명해요."}
    slots.update(_SLOTS.get(template_id, {"prompt": "지시"}))
    slots.update(extra_slots)
    return {"id": "node-1", "templateId": template_id, "slots": slots}


# --- model: 시스템 주입 ---------------------------------------------------------

@pytest.mark.parametrize("provider", ["CLAUDE", "OPENAI", "GEMINI"])
def test_hydrated_ai_node_has_provider_default_model(provider):
    """AI 노드에 model이 채워지고 요청 provider의 기본 모델(resolve_model)과 일치한다."""
    node = hydrate_node(_draft("ai.reasoning"), provider=provider)
    assert node["config"]["model"] == resolve_model(provider)
    assert node["config"]["llmProvider"] == provider


def test_default_model_contract_values():
    """노드에 실릴 기본 모델명은 backend GET /api/v1/providers의 ModelInfo.id와 맞춰야 하는 계약이다.
    배포 환경변수가 덮어쓸 수 있으므로 여기서는 코드 기본값(계약 기준선)을 고정한다."""
    from core.config import Settings

    assert Settings.model_fields["CLAUDE_DEFAULT_MODEL"].default == "claude-sonnet-4-6"
    assert Settings.model_fields["OPENAI_DEFAULT_MODEL"].default == "gpt-4o"
    assert Settings.model_fields["GEMINI_DEFAULT_MODEL"].default == "gemini-3.5-flash"


def test_every_ai_template_hydrates_with_model():
    """모든 AI 템플릿이 model 슬롯을 갖고 하이드레이션 결과에 값이 들어간다."""
    for tpl in tr.all_templates():
        if tpl["node_type"] != "AI":
            continue
        kinds = {s["name"]: s["kind"] for s in tpl["slots"]}
        assert kinds.get("model") == "model", f"{tpl['id']}: model 슬롯 없음/kind 불일치"
        assert "model" in tpl["allowed_config_fields"], f"{tpl['id']}: allowed_config_fields 누락"
        node = hydrate_node(_draft(tpl["id"]), provider="CLAUDE")
        assert node["config"]["model"] == resolve_model("CLAUDE"), tpl["id"]


def test_llm_supplied_model_is_overwritten():
    """LLM이 model 슬롯에 임의 값을 넣어도 시스템 주입값이 이긴다(모델명 날조 차단)."""
    node = hydrate_node(_draft("ai.reasoning", model="gpt-4-turbo-날조"), provider="CLAUDE")
    assert node["config"]["model"] == resolve_model("CLAUDE")


def test_model_slot_marked_auto_injected_in_catalog():
    """Builder에게 주는 슬롯 카탈로그에서 model은 '자동주입(작성금지)'로 표기된다."""
    for line in slot_catalog_text().split("\n"):
        if "model(" in line:
            assert "model(model,자동주입(작성금지))" in line, line


def test_model_excluded_from_dehydrate():
    """역변환(draft)에는 model이 실리지 않는다 — 재하이드레이션 때 다시 주입된다."""
    node = hydrate_node(_draft("ai.reasoning"), provider="CLAUDE")
    draft = dehydrate_node(node)
    assert "model" not in draft["slots"]
    assert "llmProvider" not in draft["slots"]


def test_model_slot_requires_provider():
    """provider 없이 하이드레이션하면 model을 주입할 수 없어 실패한다(LLM 값으로 대체 불가)."""
    with pytest.raises(SlotFillError):
        hydrate_node(_draft("ai.reasoning", model="아무거나"))


# --- serviceType: 템플릿 상수 ---------------------------------------------------

def test_app_templates_have_expected_service_type():
    """앱 템플릿으로 만든 노드에 올바른 serviceType이 있다."""
    for tid, expected in EXPECTED_SERVICE_TYPES.items():
        node = hydrate_node(_draft(tid), provider="CLAUDE")
        assert node["config"]["serviceType"] == expected, tid
        assert "serviceType" in tr.get_template(tid)["allowed_config_fields"], tid


def test_non_app_ai_nodes_have_no_service_type():
    """앱과 무관한 AI 노드에는 serviceType이 없다."""
    for tid in NON_APP_AI_TEMPLATES:
        node = hydrate_node(_draft(tid), provider="CLAUDE")
        assert "serviceType" not in node["config"], tid


def test_service_type_coverage_matches_registry():
    """AI 템플릿은 앱 매핑에 있거나 비앱 목록에 있거나 둘 중 하나다(새 템플릿 추가 시 분류 강제)."""
    ai_ids = {t["id"] for t in tr.all_templates() if t["node_type"] == "AI"}
    assert ai_ids == set(EXPECTED_SERVICE_TYPES) | NON_APP_AI_TEMPLATES


def test_structural_nodes_have_no_service_type():
    """트리거/조건/변환/HTTP 등 구조 노드에는 serviceType이 없다."""
    for tpl in tr.all_templates():
        if tpl["node_type"] == "AI":
            continue
        assert "serviceType" not in (tpl["fixed"].get("config") or {}), tpl["id"]


def test_invalid_service_type_rejected_by_schema_gate():
    """허용 목록 밖 serviceType은 템플릿 로드 단계에서 막힌다(오타 드리프트 게이트)."""
    bad = {
        "id": "ai.bogus", "node_type": "AI", "tool_key": None,
        "tags": ["x"], "menu": "m",
        "fixed": {"type": "AI", "config": {"serviceType": "SLACKK"}},
        "slots": [], "allowed_config_fields": ["serviceType"],
    }
    with pytest.raises(tr.TemplateSchemaError):
        tr._validate_template(bad, "/tmp/ai.bogus.json", tr._tool_map_keys())


# --- 실행 경로 무관성 -----------------------------------------------------------

def test_execute_request_ignores_service_type():
    """/v1/execute 요청 스키마에 serviceType이 없고, 붙여 보내도 요청 내용이 달라지지 않는다."""
    from api.schemas.request import AgentNodeRequest

    assert "serviceType" not in AgentNodeRequest.model_fields
    base = {"nodeId": "node-1", "renderedPrompt": "안녕"}
    assert AgentNodeRequest(**base).model_dump() == \
        AgentNodeRequest(**{**base, "serviceType": "SLACK"}).model_dump()


def test_executor_sources_do_not_read_service_type():
    """실행 경로 소스는 serviceType을 읽지 않는다(순수 FE 표시용 메타 유지).

    누군가 실행 분기에 끌어다 쓰면 실패시켜 표시용 메타가 동작에 스며드는 것을 막는다."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    offenders = []
    for rel in ("core/agent.py", "core/model_factory.py", "core/execution_guard.py",
                "core/output_validator.py", "agents", "tools"):
        path = os.path.join(root, rel)
        files = [path] if os.path.isfile(path) else [
            os.path.join(dirpath, f)
            for dirpath, _, names in os.walk(path)
            for f in names if f.endswith(".py")
        ]
        for f in files:
            with open(f, "r", encoding="utf-8") as fh:
                if "serviceType" in fh.read():
                    offenders.append(os.path.relpath(f, root))
    assert not offenders, f"실행 경로가 serviceType을 참조한다: {offenders}"


# --- 레거시 워크플로우 -----------------------------------------------------------

def test_legacy_node_round_trip_backfills_both_fields():
    """model/serviceType 도입 이전에 저장된 노드도 수정 왕복에서 실패 없이 값이 채워진다.

    두 필드 모두 LLM이 채우는 슬롯이 아니므로(시스템 주입 / 템플릿 fixed) 레거시 draft에
    값이 없어도 SlotFillError가 나지 않는다."""
    legacy = {
        "id": "node-3", "type": "AI", "label": "Slack 알림",
        "description": "정리된 내용을 슬랙으로 보내드려요.",
        "config": {"llmProvider": "CLAUDE", "credentialId": "", "prompt": "보내줘",
                   "agentType": "react", "tools": [{"name": "slack"}]},
    }
    draft = dehydrate_node(legacy)
    assert "model" not in draft["slots"]

    node = hydrate_node(draft, provider="GEMINI")
    assert node["config"]["model"] == resolve_model("GEMINI")
    assert node["config"]["serviceType"] == "SLACK"


def test_hydrated_workflow_with_tech_fields_passes_validator():
    """model/serviceType이 붙은 노드가 config 화이트리스트 검증을 통과한다."""
    from core.validators.workflow_validator import WorkflowValidator
    from tools.registry import apply_service_brand

    drafts = [
        {"templateId": "trigger.manual", "slots": {"label": "시작", "description": "버튼을 누르면 시작돼요."}},
        {"templateId": "ai.slack_send",
         "slots": {"label": "발송", "description": "슬랙으로 보내드려요.", "prompt": "보내줘"}},
    ]
    nodes = hydrate_nodes(drafts, provider="CLAUDE")
    apply_service_brand(nodes)
    WorkflowValidator.validate(nodes, [{"source": "node-1", "target": "node-2"}], set())


def test_service_type_for_node_resolves_from_template():
    """완성 노드에서 serviceType을 템플릿으로 복원한다(하이드레이션 없는 경로용)."""
    slack = {"type": "AI", "config": {"agentType": "react", "tools": [{"name": "slack"}]}}
    reasoning = {"type": "AI", "config": {"agentType": "simple", "tools": []}}
    assert service_type_for_node(slack) == "SLACK"
    assert service_type_for_node(reasoning) is None


# --- /v1/modify-workflow (하이드레이션 없는 경로) --------------------------------

@pytest.mark.asyncio
async def test_modify_workflow_backfills_model_and_service_type():
    """LLM이 두 필드를 빼먹어도 modify 응답 노드에 결정론적으로 채워진다."""
    from tests.test_modify import VALID_MODIFY_JSON, CURRENT_NODES, CURRENT_EDGES, _make_patches
    from core.workflow_modifier import modify_workflow

    p1, p2, p3, p4 = _make_patches(VALID_MODIFY_JSON)
    with p1, p2, p3, p4:
        result = await modify_workflow(
            "Slack 알림 노드를 추가해줘", CURRENT_NODES, CURRENT_EDGES, "CLAUDE", "test-key",
        )

    trigger, reasoning, slack = result.nodes
    assert "model" not in trigger.config          # AI 노드가 아니면 붙이지 않는다
    assert "serviceType" not in trigger.config
    assert reasoning.config["model"] == resolve_model("CLAUDE")
    assert "serviceType" not in reasoning.config  # 앱 노드 아님
    assert slack.config["model"] == resolve_model("CLAUDE")
    assert slack.config["serviceType"] == "SLACK"


@pytest.mark.asyncio
async def test_modify_workflow_overwrites_hallucinated_tech_fields():
    """LLM이 모델명·앱 종류를 날조해도 템플릿·provider 기준값으로 덮어쓴다."""
    from tests.test_modify import VALID_MODIFY_JSON, CURRENT_NODES, CURRENT_EDGES, _make_patches
    from core.workflow_modifier import modify_workflow

    payload = json.loads(VALID_MODIFY_JSON)
    payload["nodes"][1]["config"]["serviceType"] = "NOTION"      # 앱 노드가 아닌데 붙임
    payload["nodes"][2]["config"]["model"] = "claude-9-ultra"    # 존재하지 않는 모델
    payload["nodes"][2]["config"]["serviceType"] = "DISCORD"     # 실제로는 slack 도구

    p1, p2, p3, p4 = _make_patches(json.dumps(payload))
    with p1, p2, p3, p4:
        result = await modify_workflow(
            "수정해줘", CURRENT_NODES, CURRENT_EDGES, "CLAUDE", "test-key",
        )

    assert "serviceType" not in result.nodes[1].config
    assert result.nodes[2].config["model"] == resolve_model("CLAUDE")
    assert result.nodes[2].config["serviceType"] == "SLACK"


@pytest.mark.asyncio
async def test_modify_workflow_tool_less_react_node_gets_no_service_type():
    """도구 없는 react AI 노드에 앱 종류를 찍지 않는다.

    resolve_template_for_node는 도구 없는 AI를 agentType으로 판별하는데 react 후보가
    ai.github_query 하나뿐이라, 도구를 보지 않고 추정하면 무관한 추론 노드에 GITHUB가 박힌다."""
    from tests.test_modify import VALID_MODIFY_JSON, CURRENT_NODES, CURRENT_EDGES, _make_patches
    from core.workflow_modifier import modify_workflow

    payload = json.loads(VALID_MODIFY_JSON)
    payload["nodes"][1]["config"]["agentType"] = "react"   # 도구는 그대로 []
    payload["nodes"][1]["config"]["tools"] = []

    p1, p2, p3, p4 = _make_patches(json.dumps(payload))
    with p1, p2, p3, p4:
        result = await modify_workflow(
            "수정해줘", CURRENT_NODES, CURRENT_EDGES, "CLAUDE", "test-key",
        )

    assert "serviceType" not in result.nodes[1].config


@pytest.mark.asyncio
async def test_modify_workflow_keeps_service_type_when_tools_unmatched():
    """도구가 어느 템플릿과도 매칭되지 않으면 기존 serviceType을 지우지 않는다."""
    from tests.test_modify import VALID_MODIFY_JSON, CURRENT_NODES, CURRENT_EDGES, _make_patches
    from core.workflow_modifier import modify_workflow

    payload = json.loads(VALID_MODIFY_JSON)
    payload["nodes"][2]["config"]["tools"] = [{"name": "unknown_tool"}]
    payload["nodes"][2]["config"]["serviceType"] = "SLACK"

    p1, p2, p3, p4 = _make_patches(json.dumps(payload))
    with p1, p2, p3, p4:
        result = await modify_workflow(
            "수정해줘", CURRENT_NODES, CURRENT_EDGES, "CLAUDE", "test-key",
        )

    assert result.nodes[2].config["serviceType"] == "SLACK"


@pytest.mark.asyncio
async def test_modify_workflow_model_follows_request_provider():
    """model은 노드에 복사된 llmProvider가 아니라 요청 provider에서 뽑는다.

    노드의 llmProvider는 LLM이 기존 워크플로우에서 옮겨온 값이라, 비면 resolve_model이
    GEMINI 기본값으로 폴백해 실제 실행 프로바이더와 다른 모델 id가 박힌다."""
    from tests.test_modify import VALID_MODIFY_JSON, CURRENT_NODES, CURRENT_EDGES, _make_patches
    from core.workflow_modifier import modify_workflow

    payload = json.loads(VALID_MODIFY_JSON)
    payload["nodes"][2]["config"]["llmProvider"] = ""   # 복사 과정에서 깨진 값

    p1, p2, p3, p4 = _make_patches(json.dumps(payload))
    with p1, p2, p3, p4:
        result = await modify_workflow(
            "수정해줘", CURRENT_NODES, CURRENT_EDGES, "CLAUDE", "test-key",
        )

    assert result.nodes[2].config["model"] == resolve_model("CLAUDE")
    assert result.nodes[2].config["model"] != resolve_model("")

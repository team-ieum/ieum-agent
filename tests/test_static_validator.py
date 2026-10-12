"""정적 검증(로드맵 ④) — code별 단위 테스트. spec: ieum-backend docs/superpowers/specs/2026-10-12-node-static-validation-design.md §3."""
import copy

import pytest

from core.template_registry import builder_entries, match_entry
from core.validators.static_validator import _check, field_errors, validate_workflow

TRIGGER = {"id": "t", "type": "TRIGGER", "config": {"triggerType": "MANUAL"}}


def _ai(nid="a", **config):
    return {"id": nid, "type": "AI", "config": {"llmProvider": "GEMINI", "model": "gemini-3.7-flash",
                                                "prompt": "요약해줘", "agentType": "simple", "tools": [], **config}}


def _chain(*nodes):
    """TRIGGER → nodes[0] → nodes[1] … 일렬 연결."""
    all_nodes = [TRIGGER, *nodes]
    edges = [{"source": a["id"], "target": b["id"]} for a, b in zip(all_nodes, all_nodes[1:])]
    return all_nodes, edges


def _codes(errors):
    return sorted((e["code"], e["nodeId"], e["field"]) for e in errors)


def test_정상_워크플로우는_에러_없음():
    nodes, edges = _chain(_ai("a"), _ai("b", prompt="{{nodes.a.output.output}} 정리"))
    assert validate_workflow(nodes, edges) == []


def test_에러_모양은_4키():
    errors = validate_workflow([], [])
    assert errors == [{"nodeId": None, "field": None, "code": "TRIGGER_COUNT", "message": "워크플로우에 노드가 없습니다."}]


# ── 그래프 ──

def test_트리거_0개_2개는_TRIGGER_COUNT():
    assert ("TRIGGER_COUNT", None, None) in _codes(validate_workflow([_ai("a")], []))
    two = [TRIGGER, {**TRIGGER, "id": "t2"}]
    assert ("TRIGGER_COUNT", None, None) in _codes(validate_workflow(two, [{"source": "t", "target": "t2"}]))


def test_트리거가_첫_노드가_아니어도_통과():
    """BE 런타임은 TRIGGER를 타입으로 찾는다(SyncExecutionRuntime) — 저장 순서는 실행과 무관."""
    nodes, edges = _chain(_ai("a"))
    assert validate_workflow(list(reversed(nodes)), edges) == []


def test_id_중복_엣지_끝점_없음_트리거로_들어옴은_EDGE_INVALID():
    nodes, edges = _chain(_ai("a"))
    errors = validate_workflow(nodes + [_ai("a")], edges + [{"source": "a", "target": "zz"},
                                                             {"source": "a", "target": "t"}])
    codes = [e["code"] for e in errors]
    assert codes.count("EDGE_INVALID") == 3


def test_노드_id_type_없음_엣지_끝점_None은_EDGE_INVALID():
    nodes, edges = _chain(_ai("a"))
    assert _codes(validate_workflow(nodes + [{"type": "LOOP"}], edges)) == [("EDGE_INVALID", None, None)]
    assert _codes(validate_workflow(nodes + [{"id": "x"}], edges)) == [("EDGE_INVALID", "x", None),
                                                                       ("ORPHAN_NODE", "x", None)]
    assert _codes(validate_workflow(nodes, edges + [{"source": "a", "target": None}])) == [
        ("EDGE_INVALID", None, None)]


def test_소문자_trigger도_트리거로_센다():
    nodes, edges = _chain(_ai("a"))
    assert validate_workflow([{**TRIGGER, "type": "trigger"}, *nodes[1:]], edges) == []


def test_트리거가_1개가_아니면_ORPHAN_NODE를_내지_않는다():
    """기준 트리거가 없으면 도달성을 판단할 수 없다 — TRIGGER_COUNT 하나만."""
    assert _codes(validate_workflow([_ai("a"), _ai("b")], [])) == [("TRIGGER_COUNT", None, None)]


def test_고아_노드는_ORPHAN_NODE():
    nodes, edges = _chain(_ai("a"))
    assert ("ORPHAN_NODE", "b", None) in _codes(validate_workflow(nodes + [_ai("b")], edges))


def test_cron_형식_오류는_CRON_빈값은_REQUIRED():
    bad = {"id": "t", "type": "TRIGGER", "config": {"triggerType": "SCHEDULE", "cron": "매일 9시"}}
    assert _codes(validate_workflow([bad], [])) == [("CRON", "t", "config.cron")]
    empty = {"id": "t", "type": "TRIGGER", "config": {"triggerType": "SCHEDULE", "cron": ""}}
    assert _codes(validate_workflow([empty], [])) == [("REQUIRED", "t", "config.cron")]


@pytest.mark.parametrize("cron", ["0 9 * * 1-5", "0 0 9 ? * MON-FRI", "0 0 9 L * ? 2027", "0 0 12 ? * 6#3"])
def test_cron_5필드와_Quartz_6_7필드는_통과(cron):
    # BE CronConverter.toQuartz가 5/6/7필드를 모두 받는다 — REST로 저장한 Quartz cron이 오탐되면 안 된다.
    node = {"id": "t", "type": "TRIGGER", "config": {"triggerType": "SCHEDULE", "cron": cron}}
    assert _codes(validate_workflow([node], [])) == []


@pytest.mark.parametrize("cron", ["0 9 * *", "0 0 9 * * ? 2027 1", "0 9 * * 1;rm"])
def test_cron_필드수_문자_위반은_CRON(cron):
    node = {"id": "t", "type": "TRIGGER", "config": {"triggerType": "SCHEDULE", "cron": cron}}
    assert _codes(validate_workflow([node], [])) == [("CRON", "t", "config.cron")]


# ── 칸 ──

def test_필수_칸_비면_REQUIRED_칸_path와_title():
    node = {"id": "s", "type": "ACTION", "config": {"tools": [{"name": "slack", "config": {"message": "안녕"}}]}}
    errors = field_errors(node)
    assert [(e["code"], e["field"]) for e in errors] == [("REQUIRED", "config.tools.0.config.webhookCredentialId")]
    assert errors[0]["nodeId"] == "s" and "선택하세요" in errors[0]["message"]


def test_default_있는_필수_칸은_비어도_통과():
    node = {"id": "h", "type": "HTTP", "config": {"url": "https://example.com"}}  # method default GET
    assert field_errors(node) == []


def test_빈_목록은_필수_칸을_비운_것으로_본다():
    node = {"id": "g", "type": "ACTION", "config": {"tools": [{"name": "builtin:google_sheets_append", "config": {
        "spreadsheet_id": "s", "cell_range": "A:B", "values": []}}]}}
    assert [(e["code"], e["field"]) for e in field_errors(node)] == [("REQUIRED", "config.tools.0.config.values")]


def test_webhookCredentialId_있는_HTTP는_url_없어도_통과():
    """BE HttpNodeExecutor는 webhookCredentialId가 있으면 url을 읽지 않는다(BE-62 안내대로 저장한 모양)."""
    node = {"id": "h", "type": "HTTP", "config": {"method": "POST", "webhookCredentialId": "614ac2ef", "body": "{}"}}
    assert field_errors(node) == []
    blank = {"id": "h", "type": "HTTP", "config": {"method": "POST", "webhookCredentialId": "  "}}
    assert [(e["code"], e["field"]) for e in field_errors(blank)] == [("REQUIRED", "config.url")]


def test_AI_노드_도구_칸은_비어도_통과():
    node = _ai("w", agentType="react", tools=[{"name": "builtin:web_search"}])  # query 필수지만 AI가 결정
    assert match_entry(node)["id"] == "ai.web_search"
    assert field_errors(node) == []


def test_ai_mcp_catalogId는_도구_칸이_아니라_검사한다():
    node = _ai("m", agentType="react", tools=[{"name": "mcp", "config": {}}])
    assert [e["field"] for e in field_errors(node)] == ["config.tools.0.config.catalogId"]


def test_condition_isEmpty_계열은_right_비어도_통과():
    for op in ("isEmpty", "isNotEmpty"):
        node = {"id": "c", "type": "CONDITION", "config": {"operator": op, "left": "{{nodes.a.output.output}}"}}
        assert field_errors(node) == []
    node = {"id": "c", "type": "CONDITION", "config": {"operator": "equals", "left": "x"}}
    assert [e["field"] for e in field_errors(node)] == ["config.right"]


def test_condition_옛_표기_leftValue_rightValue도_채운_것으로_본다():
    """BE ConditionNodeExecutor는 left/right가 없으면 leftValue/rightValue를 읽는다."""
    node = {"id": "c", "type": "CONDITION", "config": {"operator": "equals", "leftValue": "x", "rightValue": "y"}}
    assert field_errors(node) == []


@pytest.mark.parametrize("bad", [[{"key": "title", "type": "text"}, {"key": "score"}], {"key": "a"},
                                 [{"key": "t", "type": "날짜"}]])
def test_AI_outputFields는_칸_검사하지_않는다(bad):
    """spec §1: 출력 칸 규칙은 실행 시점 INVALID_OUTPUT_FIELDS 몫(③ spec)."""
    assert field_errors(_ai("r", outputFields=bad)) == []


_LIST_FIELD = {"key": "rows", "title": "행", "type": "object", "list": True, "children": [
    {"key": "name", "title": "이름", "type": "string", "required": True},
    {"key": "n", "title": "개수", "type": "integer"}]}


def test_list_칸은_목록이어야_하고_children은_원소마다_검사():
    """spec §3.3·3.4 — 지금 카탈로그엔 children 칸이 없어(outputFields 제외) 칸 정의를 직접 넣는다."""
    assert [(e["code"], e["field"]) for e in _check(_LIST_FIELD, {"a": 1}, "config.rows", "x")] == [
        ("TYPE", "config.rows")]
    errors = _check(_LIST_FIELD, [{"name": "a", "n": "3"}, {"n": "셋"}], "config.rows", "x")
    assert [(e["code"], e["field"]) for e in errors] == [("REQUIRED", "config.rows.1.name"), ("TYPE", "config.rows.1.n")]


@pytest.mark.parametrize("value,ok", [(5, True), ("5", True), (" 7 ", True), (True, False), ("다섯", False), (1.5, False)])
def test_integer_TYPE(value, ok):
    node = _ai("w", agentType="react", tools=[{"name": "builtin:web_search", "config": {"max_results": value}}])
    assert (field_errors(node) == []) is ok


@pytest.mark.parametrize("value,ok", [('[["a", 1]]', True), ([["a", 1]], True), ("[[a", False), (5, False), ("[" * 100000, False)])
def test_json_TYPE은_JSON_문자열도_허용(value, ok):
    """도구 인자 values는 JSON 문자열로 저장된다(tools/google_sheets.py values: str)."""
    node = {"id": "g", "type": "ACTION", "config": {"tools": [{"name": "builtin:google_sheets_append", "config": {
        "spreadsheet_id": "s", "cell_range": "A:B", "values": value}}]}}
    assert (field_errors(node) == []) is ok


def test_dict_TYPE():
    node = {"id": "x", "type": "TRANSFORM", "config": {"mappings": "a=b"}}
    assert [(e["code"], e["field"]) for e in field_errors(node)] == [("TYPE", "config.mappings")]


def test_참조식_값은_TYPE_CHOICE_건너뜀():
    node = _ai("w", agentType="react", tools=[{"name": "builtin:web_search",
                                               "config": {"max_results": "{{nodes.t.output.n}}"}}])
    assert field_errors(node) == []


@pytest.mark.parametrize("op", ["같음", ["equals"], {"id": "equals"}])
def test_choices_밖의_값은_CHOICE(op):
    """목록 칸이 아닌 choices 칸에 list·dict가 들어 있어도 CHOICE — 해시 안 되는 값으로 예외가 나면 안 된다."""
    node = {"id": "c", "type": "CONDITION", "config": {"operator": op, "left": "a", "right": "b"}}
    assert [(e["code"], e["field"]) for e in field_errors(node)] == [("CHOICE", "config.operator")]


def test_condition_operator가_목록이면_right_예외를_적용하지_않는다():
    """operator가 해시 안 되는 값이어도 예외 없이 right를 필수로 본다(500이면 BE가 검증을 건너뛴다)."""
    node = {"id": "c", "type": "CONDITION", "config": {"operator": ["isEmpty"], "left": "a"}}
    assert _codes(field_errors(node)) == [("CHOICE", "c", "config.operator"), ("REQUIRED", "c", "config.right")]


def test_object_목록_칸은_원소_CHOICE를_보지_않는다():
    """ai.agent tools 선택지는 액션 항목뿐이라 원소를 비교하면 builtin:web_search 섞인 노드가 오탐된다."""
    node = _ai("g", agentType="react", tools=["builtin:web_search", "builtin:http_fetch"])
    assert match_entry(node)["id"] == "ai.agent"
    assert field_errors(node) == []


def test_ref_false_칸의_참조식은_REF_NOT_ALLOWED():
    node = {"id": "c", "type": "CONDITION", "config": {"operator": "{{nodes.t.output.op}}", "left": "a", "right": "b"}}
    assert [(e["code"], e["field"]) for e in field_errors(node)] == [("REF_NOT_ALLOWED", "config.operator")]


def test_매칭되지_않는_노드는_칸_검사를_건너뛴다():
    assert match_entry({"id": "x", "type": "LOOP", "config": {}}) is None
    assert field_errors({"id": "x", "type": "LOOP", "config": {}}) == []


# ── 카탈로그 전 항목 기본값(fixed) 노드 (spec §5-1) ──
# 빌더가 노드를 놓는 순간의 모양에서 어떤 칸이 REQUIRED로 걸리는지 고정한다. 템플릿 변경·앱 노드 추가가
# 여기서 드러난다 — 바뀌면 의도한 변화인지 확인하고 기대값을 고친다.
FIXED_NODE_REQUIRED = {
    "ai.github_query": ["config.llmProvider", "config.model", "config.prompt"],
    "ai.http_fetch": ["config.llmProvider", "config.model", "config.prompt"],
    "ai.mcp": ["config.llmProvider", "config.model", "config.prompt", "config.tools.0.config.catalogId"],
    "ai.web_search": ["config.llmProvider", "config.model", "config.prompt"],
    "action.discord_send": ["config.tools.0.config.content", "config.tools.0.config.webhookCredentialId"],
    "action.github_create_issue": ["config.tools.0.config.owner", "config.tools.0.config.repo",
                                   "config.tools.0.config.title"],
    "action.github_list_issues": ["config.tools.0.config.owner", "config.tools.0.config.repo"],
    "action.google_calendar_create": ["config.tools.0.config.end_datetime", "config.tools.0.config.start_datetime",
                                      "config.tools.0.config.summary"],
    "action.google_calendar_list": ["config.tools.0.config.time_max", "config.tools.0.config.time_min"],
    "action.google_calendar_update": ["config.tools.0.config.event_id"],
    "action.google_drive_read": ["config.tools.0.config.file_id"],
    "action.google_drive_upload": ["config.tools.0.config.content", "config.tools.0.config.name"],
    "action.google_sheets_append": ["config.tools.0.config.cell_range", "config.tools.0.config.spreadsheet_id",
                                    "config.tools.0.config.values"],
    "action.google_sheets_read": ["config.tools.0.config.cell_range", "config.tools.0.config.spreadsheet_id"],
    "action.google_sheets_write": ["config.tools.0.config.cell_range", "config.tools.0.config.spreadsheet_id",
                                   "config.tools.0.config.values"],
    "action.notion_append_block": ["config.tools.0.config.content", "config.tools.0.config.page_id"],
    "action.notion_create_page": ["config.tools.0.config.content", "config.tools.0.config.parent_page_id",
                                  "config.tools.0.config.title"],
    "action.notion_query_database": ["config.tools.0.config.database_id"],
    "action.notion_read_page": ["config.tools.0.config.page_id"],
    "action.notion_search": ["config.tools.0.config.query"],
    "action.notion_update_page": ["config.tools.0.config.page_id"],
    "action.slack_send": ["config.tools.0.config.message", "config.tools.0.config.webhookCredentialId"],
    "ai.reasoning": ["config.llmProvider", "config.model", "config.prompt"],
    "trigger.github_new_issue": ["config.repoId"],
    "trigger.gmail_new_email": [],
    "trigger.schedule": ["config.cron"],
    "trigger.webhook": [],
    # ai.agent fixed(tools [], agentType react)는 도구가 없어 ai.github_query로 매칭된다 — 도구를 고르기 전 모양
    "ai.agent": ["config.llmProvider", "config.model", "config.prompt"],
    "approval": [],
    "condition": ["config.left", "config.operator", "config.right"],
    "http": ["config.url"],
    "transform": ["config.mappings"],
    "trigger.manual": [],
}


def test_카탈로그_전_항목_기본값_노드():
    actual = {}
    for t in builder_entries():
        node = {"id": "n", "type": t["fixed"]["type"], "config": copy.deepcopy(t["fixed"].get("config") or {})}
        errors = field_errors(node)
        assert {e["code"] for e in errors} <= {"REQUIRED"}, (t["id"], errors)  # fixed 자체는 형식이 맞아야 한다
        actual[t["id"]] = sorted(e["field"] for e in errors)
    assert actual == FIXED_NODE_REQUIRED

"""정적 검증(로드맵 ④) — code별 단위 테스트. spec: ieum-backend docs/superpowers/specs/2026-10-12-node-static-validation-design.md §3."""
import copy

import pytest

from core.template_registry import _match_entries, match_entry
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


def test_AI_노드_모델은_비어도_통과_채우면_형식_검사():
    """dev 사전 점검 실측 모양: IEUM-AI-55 이전 Gemini 노드에 model 키가 없고, 기본 모델로 실행돼 왔다."""
    legacy = {"id": "node-2", "type": "AI", "config": {"agentType": "simple", "brand": "x", "credentialId": "",
                                                       "llmProvider": "GEMINI", "prompt": "요약해줘", "tools": []}}
    assert field_errors(legacy) == []
    assert field_errors({**legacy, "config": {**legacy["config"], "model": None}}) == []
    bad = {**legacy, "config": {**legacy["config"], "model": "{{nodes.t.output.m}}"}}
    assert [(e["code"], e["field"]) for e in field_errors(bad)] == [("REF_NOT_ALLOWED", "config.model")]


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


# ── 참조식 ──

@pytest.mark.parametrize("ref", [
    "{{nodes.a.output}}",               # 경로 없음
    "{{nodes.a_b.output.x}}",           # id에 밑줄 — BE가 치환 못 한다
    "{{nodes.a.output.my-field}}",      # 경로에 하이픈
    "{{ nodes.a.output.output }}",      # 공백 — BE 정규식은 공백을 받지 않는다
    "{{trigger.x}}",
])
def test_형식이_틀린_참조는_REF_FORMAT(ref):
    nodes, edges = _chain(_ai("a"), _ai("b", prompt=f"정리: {ref}"))
    assert ("REF_FORMAT", "b", "config.prompt") in _codes(validate_workflow(nodes, edges))


def test_닫히지_않은_참조는_REF_FORMAT_JSON_닫는_괄호는_무시():
    nodes, edges = _chain(_ai("a"), _ai("b", prompt="정리: {{nodes.a.output.output"))
    assert [e["code"] for e in validate_workflow(nodes, edges)] == ["REF_FORMAT"]
    http = {"id": "h", "type": "HTTP", "config": {"url": "https://x.io", "body": '{"text": "{{nodes.a.output.output}}", "meta": {"k": 1}}'}}
    nodes, edges = _chain(_ai("a"), http)
    assert validate_workflow(nodes, edges) == []


def test_중첩_경로_참조는_통과():
    lst = {"id": "l", "type": "ACTION", "config": {"tools": [{"name": "builtin:github_list_issues",
                                                              "config": {"owner": "o", "repo": "r"}}]}}
    nodes, edges = _chain(lst, _ai("b", prompt="{{nodes.l.output.issues.0.title}}"))
    assert validate_workflow(nodes, edges) == []


def test_없는_노드_자기_뒤_노드_참조():
    nodes, edges = _chain(_ai("a", prompt="{{nodes.b.output.output}} {{nodes.zz.output.x}} {{nodes.a.output.output}}"),
                          _ai("b"))
    assert _codes(validate_workflow(nodes, edges)) == [
        ("REF_NOT_UPSTREAM", "a", "config.prompt"), ("REF_SELF", "a", "config.prompt"),
        ("REF_UNKNOWN_NODE", "a", "config.prompt")]


def test_두_칸_이상_떨어진_조상_참조는_통과():
    nodes, edges = _chain(_ai("a"), _ai("b"), _ai("c", prompt="{{nodes.a.output.output}} {{nodes.t.output.x}}"))
    assert validate_workflow(nodes, edges) == []


def test_선언되지_않은_출력_칸은_REF_UNKNOWN_FIELD():
    nodes, edges = _chain(_ai("a"), _ai("b", prompt="{{nodes.a.output.summary}}"))
    assert _codes(validate_workflow(nodes, edges)) == [("REF_UNKNOWN_FIELD", "b", "config.prompt")]


def test_outputsFrom_값이_있으면_정적_출력을_대체():
    a = _ai("a", outputFields=[{"key": "title", "type": "text"}])
    nodes, edges = _chain(a, _ai("b", prompt="{{nodes.a.output.title}} {{nodes.a.output.output}}"))
    assert _codes(validate_workflow(nodes, edges)) == [("REF_UNKNOWN_FIELD", "b", "config.prompt")]
    nodes, edges = _chain(a, _ai("b", prompt="{{nodes.a.output.title}}"))
    assert validate_workflow(nodes, edges) == []
    tr = {"id": "x", "type": "TRANSFORM", "config": {"mappings": {"pr": "{{nodes.t.output.body}}"}}}
    nodes, edges = _chain(tr, _ai("b", prompt="{{nodes.x.output.pr}}"))
    assert validate_workflow(nodes, edges) == []


def test_빈_outputFields는_기본_출력_output을_유지():
    nodes, edges = _chain(_ai("a", outputFields=[]), _ai("b", prompt="{{nodes.a.output.output}}"))
    assert validate_workflow(nodes, edges) == []


def test_앞_단계가_아니면_출력_칸은_보지_않는다():
    """처음 걸린 것 하나만 — 형제 노드의 없는 칸을 가리켜도 REF_NOT_UPSTREAM 한 건."""
    nodes = [TRIGGER, _ai("a"), _ai("b", prompt="{{nodes.a.output.zzz}}")]
    edges = [{"source": "t", "target": "a"}, {"source": "t", "target": "b"}]
    assert _codes(validate_workflow(nodes, edges)) == [("REF_NOT_UPSTREAM", "b", "config.prompt")]


def test_카탈로그에_없는_노드도_참조_검사를_받는다():
    """spec §3.2 — 미매칭 노드는 칸 검사만 건너뛰고 그래프·참조 검사는 받는다."""
    loop = {"id": "x", "type": "LOOP", "config": {"body": "{{nodes.zz.output.x}}"}}
    assert _codes(validate_workflow(*_chain(loop))) == [("REF_UNKNOWN_NODE", "x", None)]


def test_칸_안에_중첩된_참조의_field는_그_칸_path():
    http = {"id": "h", "type": "HTTP", "config": {"url": "https://x.io", "headers": {"X": "{{nodes.zz.output.x}}"}}}
    assert _codes(validate_workflow(*_chain(http))) == [("REF_UNKNOWN_NODE", "h", "config.headers")]


def test_outputDynamic_미매칭_대상은_출력_칸을_보지_않는다():
    nodes, edges = _chain(_ai("b", prompt="{{nodes.t.output.anything}}"))  # manual.run = outputDynamic
    assert validate_workflow(nodes, edges) == []
    loop = {"id": "x", "type": "LOOP", "config": {}}
    nodes, edges = _chain(loop, _ai("b", prompt="{{nodes.x.output.anything}}"))
    assert validate_workflow(nodes, edges) == []


def test_도구_칸_참조식의_field는_그_칸_path():
    act = {"id": "s", "type": "ACTION", "config": {"tools": [{"name": "slack", "config": {
        "webhookCredentialId": "wh", "message": "{{nodes.t.output.x}} {{nodes.q.output.y}}"}}]}}
    nodes, edges = _chain(act)
    assert _codes(validate_workflow(nodes, edges)) == [("REF_UNKNOWN_NODE", "s", "config.tools.0.config.message")]


def test_깨진_입력에도_예외_없이_에러를_돌려준다():
    errors = validate_workflow([{"type": "TRIGGER"}, {"id": 3, "type": None, "config": "x"}, {"id": ["x"]}, "nope"],
                               [{"source": None}, {"source": ["t"], "target": {"a": 1}}, "nope"])
    assert {e["code"] for e in errors} <= {"EDGE_INVALID", "TRIGGER_COUNT"}
    assert all(e["nodeId"] is None or isinstance(e["nodeId"], str) for e in errors)


def test_깨진_출력_칸_key에도_예외_없이_참조를_검사한다():
    """outputFields 원소 key가 list면 버린다 — 500이면 BE가 fail-open으로 검증을 통째로 건너뛴다."""
    a = _ai("a", outputFields=[{"key": ["x"], "type": "text"}])
    nodes, edges = _chain(a, _ai("b", prompt="{{nodes.a.output.x}}"))
    assert _codes(validate_workflow(nodes, edges)) == [("REF_UNKNOWN_FIELD", "b", "config.prompt")]


# ── 카탈로그 전 항목 기본값(fixed) 노드 (spec §5-1) ──
# 빌더가 노드를 놓는 순간의 모양에서 어떤 칸이 REQUIRED로 걸리는지 고정한다. 템플릿 변경·앱 노드 추가가
# 여기서 드러난다 — 바뀌면 의도한 변화인지 확인하고 기대값을 고친다.
FIXED_NODE_REQUIRED = {
    "ai.github_query": ["config.llmProvider", "config.prompt"],
    "ai.http_fetch": ["config.llmProvider", "config.prompt"],
    "ai.mcp": ["config.llmProvider", "config.prompt", "config.tools.0.config.catalogId"],
    "ai.web_search": ["config.llmProvider", "config.prompt"],
    "discord.send_message": ["config.tools.0.config.content", "config.tools.0.config.webhookCredentialId"],
    "github.create_issue": ["config.tools.0.config.owner", "config.tools.0.config.repo",
                                   "config.tools.0.config.title"],
    "github.list_issues": ["config.tools.0.config.owner", "config.tools.0.config.repo"],
    "google_calendar.create": ["config.tools.0.config.end_datetime", "config.tools.0.config.start_datetime",
                                      "config.tools.0.config.summary"],
    "google_calendar.list": ["config.tools.0.config.time_max", "config.tools.0.config.time_min"],
    "google_calendar.update": ["config.tools.0.config.event_id"],
    "google_drive.read": ["config.tools.0.config.file_id"],
    "google_drive.upload": ["config.tools.0.config.content", "config.tools.0.config.name"],
    "google_sheets.append": ["config.tools.0.config.cell_range", "config.tools.0.config.spreadsheet_id",
                                    "config.tools.0.config.values"],
    "google_sheets.read": ["config.tools.0.config.cell_range", "config.tools.0.config.spreadsheet_id"],
    "google_sheets.write": ["config.tools.0.config.cell_range", "config.tools.0.config.spreadsheet_id",
                                   "config.tools.0.config.values"],
    "notion.append_block": ["config.tools.0.config.content", "config.tools.0.config.page_id"],
    "notion.create_page": ["config.tools.0.config.content", "config.tools.0.config.parent_page_id",
                                  "config.tools.0.config.title"],
    "notion.query_database": ["config.tools.0.config.database_id"],
    "notion.read_page": ["config.tools.0.config.page_id"],
    "notion.search": ["config.tools.0.config.query"],
    "notion.update_page": ["config.tools.0.config.page_id"],
    "slack.send_message": ["config.tools.0.config.message", "config.tools.0.config.webhookCredentialId"],
    "ai.reasoning": ["config.llmProvider", "config.prompt"],
    "github.new_issue": ["config.repoId"],
    "gmail.new_email": [],
    "schedule.every": ["config.cron"],
    "webhook.catch": [],
    # ai.agent fixed(tools [], agentType react)는 도구가 없어 ai.github_query로 매칭된다 — 도구를 고르기 전 모양
    "ai.agent": ["config.llmProvider", "config.prompt"],
    "approval.request": [],
    "branch.if": ["config.left", "config.operator", "config.right"],
    "http.request": ["config.url"],
    "transform.map": ["config.mappings"],
    "manual.run": [],
}


def test_카탈로그_전_항목_기본값_노드():
    actual = {}
    for t in _match_entries():  # 매칭 후보 = builder 항목 + match 보유 프리셋(빌더 목록엔 프리셋이 없다)
        node = {"id": "n", "type": t["fixed"]["type"], "config": copy.deepcopy(t["fixed"].get("config") or {})}
        errors = field_errors(node)
        assert {e["code"] for e in errors} <= {"REQUIRED"}, (t["id"], errors)  # fixed 자체는 형식이 맞아야 한다
        actual[t["id"]] = sorted(e["field"] for e in errors)
    assert actual == FIXED_NODE_REQUIRED


def test_닫히지_않은_중괄호가_길어도_빨리_끝난다():
    import time
    nodes, edges = _chain(_ai("a"), _ai("b", prompt="{" * 80_000))
    t = time.monotonic()
    validate_workflow(nodes, edges)
    assert time.monotonic() - t < 1


# ── 상한 (spec §7-15) ──

def test_노드_500개_연결_2000개까지는_검증한다():
    assert validate_workflow([None] * 499 + [TRIGGER], []) == []
    assert validate_workflow([TRIGGER], [None] * 2000) == []


def test_노드_501개나_연결_2001개면_TOO_LARGE_하나만():
    """원본 길이로 센다 — dict 아닌 원소를 거르기 전. 다른 검사는 돌지 않는다."""
    assert validate_workflow([None] * 500 + [TRIGGER], []) == [{
        "nodeId": None, "field": None, "code": "TOO_LARGE",
        "message": "노드는 500개, 연결은 2000개까지 검증할 수 있어요(현재 노드 501개, 연결 0개)."}]
    errors = validate_workflow([_ai("a")], [None] * 2001)  # 트리거 없음도 내지 않는다
    assert _codes(errors) == [("TOO_LARGE", None, None)]
    assert errors[0]["message"].endswith("(현재 노드 1개, 연결 2001개).")


def test_같은_에러는_처음_하나만_순서_유지():
    prompt = "{{nodes.gone.output.x}} {{nodes.zz.output.x}} {{nodes.gone.output.x}}"
    errors = validate_workflow(*_chain(_ai("a", prompt=prompt)))
    assert [e["message"] for e in errors] == ["없는 노드 'gone'를 참조해요.", "없는 노드 'zz'를 참조해요."]


def test_에러는_최대_200건():
    prompt = " ".join(f"{{{{nodes.gone{i}.output.x}}}}" for i in range(300))
    errors = validate_workflow(*_chain(_ai("a", prompt=prompt)))
    assert len(errors) == 200
    assert errors[0]["message"] == "없는 노드 'gone0'를 참조해요."


def test_메시지에_싣는_사용자_값은_80자로_자른다():
    long = "x" * 200
    cond = {"id": "c", "type": "CONDITION", "config": {"operator": "o" * 200, "left": "a", "right": "b",
                                                       "extra": "{{" + long + "}} {{nodes." + "g" * 200 + ".output.x}}"}}
    sched = {"id": "t", "type": "TRIGGER", "config": {"triggerType": "SCHEDULE", "cron": "5" * 200}}
    dup = {"id": "q" * 200, "type": "HTTP", "config": {"url": "https://x.io"}}
    nodes = [sched, cond, dup, dup]
    edges = [{"source": "t", "target": "c"}, {"source": "c", "target": "q" * 200}, {"source": "t", "target": "z" * 200}]
    by_code = {}
    for e in validate_workflow(nodes, edges):
        by_code.setdefault(e["code"], []).append(e["message"])
    for code in ("REF_FORMAT", "REF_UNKNOWN_NODE", "CHOICE", "CRON", "EDGE_INVALID"):
        assert by_code.get(code), code
        for msg in by_code[code]:
            assert len(msg) < 200, (code, msg)
    assert "{{" + "x" * 80 + "…}}" in by_code["REF_FORMAT"][0]
    assert "'" + "g" * 80 + "…'" in by_code["REF_UNKNOWN_NODE"][0]


def test_응답은_제어문자_값이나_긴_nodeId에도_바이트_예산_안():
    """BE WebClient 기본 버퍼 256KB — 넘으면 파싱 실패 = fail-open(최종 리뷰 rv/size.py 재현)."""
    import json
    ctrl = [chr(c) for c in range(1, 32) if chr(c) not in "\t\n\r\b\f"]
    ends = ["".join(ctrl[i // len(ctrl) ** k % len(ctrl)] for k in range(3)) + ctrl[0] * 97 for i in range(200)]
    errors = validate_workflow([TRIGGER], [{"source": s, "target": s} for s in ends])
    assert errors and len(json.dumps(errors)) <= 200_000
    prompt = " ".join(f"{{{{nodes.gone{i}.output.x}}}}" for i in range(200))
    for nid in ("n" * 2000, "\x01" * 300):
        errors = validate_workflow(*_chain(_ai(nid, prompt=prompt)))
        assert errors and len(json.dumps(errors)) <= 200_000
    errors = validate_workflow(*_chain({"id": "n" * 300_000, "type": "HTTP", "config": {}}))
    assert [e["code"] for e in errors] == ["REQUIRED"]  # 최소 1건은 남아 valid=false
    assert errors[0]["nodeId"] == "n" * 80 + "…"


def test_긴_키_아래_큰_목록도_빨리_끝난다():
    """_iter_strings가 원소마다 긴 경로를 복사하지 않는다(rv/kp2.py 모양)."""
    import time
    node = {"id": "a", "type": "TRANSFORM", "config": {"k" * 1_000_000: [0] * 200_000}}
    t = time.monotonic()
    validate_workflow(*_chain(node))
    assert time.monotonic() - t < 1


def test_중복_에러가_많아도_모아_두지_않는다():
    """에러를 다 만든 뒤 자르지 않고 수집하면서 거른다(rv/mem.py 모양)."""
    import tracemalloc
    nodes, edges = _chain({"id": "h", "type": "HTTP", "config": {"url": "{{a}}" * 200_000}})
    tracemalloc.start()
    try:
        errors = validate_workflow(nodes, edges)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert [e["code"] for e in errors] == ["REF_FORMAT"]
    assert peak < 10_000_000, peak


def test_대상_출력_칸이_많아도_참조_검사는_빨리_끝난다():
    """대상 노드 출력 칸은 대상당 한 번만 계산한다(perf_tools 모양: tools 16000 × 참조 16000)."""
    import time
    k = 16000
    nodes = [{"id": "t", "type": "TRIGGER", "config": {"triggerType": "WEBHOOK"}},
             {"id": "a", "type": "TRANSFORM", "config": {"tools": ["x"] * k}},
             {"id": "b", "type": "TRANSFORM", "config": {"m": ["{{nodes.a.output.x}}"] * k}}]
    edges = [{"source": "t", "target": "a"}, {"source": "a", "target": "b"}]
    t = time.monotonic()
    validate_workflow(nodes, edges)
    assert time.monotonic() - t < 1


# ── 칸 검사가 건너뛰는 칸은 참조 검사도 건너뛴다 (spec §7-16) ──

def test_isEmpty_조건의_right_참조는_검사하지_않는다():
    cond = {"id": "c", "type": "CONDITION",
            "config": {"operator": "isEmpty", "left": "{{nodes.t.output.x}}", "right": "{{nodes.gone.output.x}}"}}
    assert validate_workflow(*_chain(cond)) == []


def test_웹훅_크레덴셜이_있는_HTTP의_url_참조는_검사하지_않는다():
    http = {"id": "h", "type": "HTTP", "config": {"method": "POST", "webhookCredentialId": "614ac2ef-0000",
                                                  "url": "https://x/{{nodes.gone.output.x}}"}}
    assert validate_workflow(*_chain(http)) == []


def test_건너뛴_칸의_하위_경로와_옛_표기는_건너뛰고_형제_키는_검사한다():
    gone = "{{nodes.gone.output.x}}"
    base = {"operator": "isEmpty", "left": "{{nodes.t.output.x}}"}
    for extra in ({"right": {"a": [gone]}}, {"rightValue": gone}):
        cond = {"id": "c", "type": "CONDITION", "config": {**base, **extra}}
        assert validate_workflow(*_chain(cond)) == [], extra
    cond = {"id": "c", "type": "CONDITION", "config": {**base, "rightX": gone}}
    assert [e["code"] for e in validate_workflow(*_chain(cond))] == ["REF_UNKNOWN_NODE"]


def test_재귀_한도보다_깊은_중첩도_훑는다():
    """_iter_strings는 명시 스택이다 — 재귀 yield from은 깊이만큼 문자열을 올려 보내 깊은 중첩에서 2.6배 느렸고
    (깊이 94 × 99만 개 3.1s → 1.2s, BE 3초 타임아웃), 재귀 한도를 넘으면 RecursionError로 500이 된다."""
    deep = ["{{}}"]
    for _ in range(3000):
        deep = [deep]
    nodes = [TRIGGER, {"id": "x", "type": "TRANSFORM", "config": {"mappings": {"a": "b"}, "x": deep}}]
    assert [e["code"] for e in validate_workflow(nodes, [{"source": "t", "target": "x"}])] == ["REF_FORMAT"]

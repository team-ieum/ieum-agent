"""정적 검증(로드맵 ④) — code별 단위 테스트. spec: ieum-backend docs/superpowers/specs/2026-10-12-node-static-validation-design.md §3."""
import copy

import pytest

from core.template_registry import load_templates, match_entry
from core.validators.static_validator import (_approval_errors, _check, condition_branches, field_errors,
                                              validate_workflow)

TRIGGER = {"id": "t", "type": "TRIGGER", "event": "manual.run", "config": {}}


def _ai(nid="a", event="ai.reasoning", **config):
    """도구 없는 simple AI = ai.reasoning. 도구를 쥔 AI는 event="ai.agent"(프리셋 노드의 event)."""
    return {"id": nid, "type": "AI", "event": event, "config": {"llmProvider": "GEMINI", "model": "gemini-3.7-flash",
                                                "prompt": "요약해줘", "agentType": "simple", "tools": [], **config}}


def _act(nid, event, **config):
    """ACTION 노드 — 입력 칸은 config 바로 아래(config.<칸>)."""
    return {"id": nid, "type": "ACTION", "event": event, "config": config}


def _sched(cron, nid="t"):
    return {"id": nid, "type": "TRIGGER", "event": "schedule.every", "config": {"cron": cron}}


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
    assert _codes(validate_workflow([_sched("매일 9시")], [])) == [("CRON", "t", "config.cron")]
    assert _codes(validate_workflow([_sched("")], [])) == [("REQUIRED", "t", "config.cron")]


@pytest.mark.parametrize("cron", ["0 9 * * 1-5", "0 0 9 ? * MON-FRI", "0 0 9 L * ? 2027", "0 0 12 ? * 6#3"])
def test_cron_5필드와_Quartz_6_7필드는_통과(cron):
    # BE CronConverter.toQuartz가 5/6/7필드를 모두 받는다 — REST로 저장한 Quartz cron이 오탐되면 안 된다.
    assert _codes(validate_workflow([_sched(cron)], [])) == []


@pytest.mark.parametrize("cron", ["0 9 * *", "0 0 9 * * ? 2027 1", "0 9 * * 1;rm"])
def test_cron_필드수_문자_위반은_CRON(cron):
    assert _codes(validate_workflow([_sched(cron)], [])) == [("CRON", "t", "config.cron")]


# ── 칸 ──

def test_필수_칸_비면_REQUIRED_칸_path와_title():
    node = _act("s", "slack.send_message", message="안녕")
    errors = field_errors(node)
    assert [(e["code"], e["field"]) for e in errors] == [("REQUIRED", "config.webhookCredentialId")]
    assert errors[0]["nodeId"] == "s" and "선택하세요" in errors[0]["message"]


def test_웹훅_노드에_남은_channel_값은_검증을_막지_않는다():
    """channel 칸을 지우기 전에 저장된 노드가 정적 검증·실행 차단에 걸리면 안 된다."""
    nodes, edges = _chain(_act("s", "slack.send_message", webhookCredentialId="wh-1", message="안녕", channel="#old"))
    assert validate_workflow(nodes, edges) == []


def test_Discord_봇_노드는_서버_칸이_비어도_채널_참조식이면_통과한다():
    """guild_id는 드롭다운용 칸이라 필수가 아니다 — 채널 ID 직접 입력·참조식 노드의 수동 실행을 막지 않는다(spec §9)."""
    nodes, edges = _chain(_act("d", "discord_bot.send_channel_message",
                               channel_id="{{nodes.t.output.channel}}", content="안녕"))
    assert validate_workflow(nodes, edges) == []
    assert [(e["code"], e["field"]) for e in field_errors(_act("d", "discord_bot.send_channel_message", guild_id="1"))] \
        == [("REQUIRED", "config.channel_id"), ("REQUIRED", "config.content")]


def test_Slack_봇_노드는_채널과_메시지가_필수다():
    node = _act("s", "slack_bot.send_channel_message", thread_ts="")
    assert [(e["code"], e["field"]) for e in field_errors(node)] == \
        [("REQUIRED", "config.channel"), ("REQUIRED", "config.text")]


def test_default_있는_필수_칸은_비어도_통과():
    node = {"id": "h", "type": "HTTP", "event": "http.request", "config": {"url": "https://example.com"}}  # method default GET
    assert field_errors(node) == []


def test_빈_목록은_필수_칸을_비운_것으로_본다():
    f = {"key": "values", "title": "값", "type": "json", "required": True}
    assert [(e["code"], e["field"]) for e in _check(f, [], "config.values", "g")] == [("REQUIRED", "config.values")]


def test_webhookCredentialId_있는_HTTP는_url_없어도_통과():
    """BE HttpNodeExecutor는 webhookCredentialId가 있으면 url을 읽지 않는다(BE-62 안내대로 저장한 모양)."""
    node = {"id": "h", "type": "HTTP", "event": "http.request",
            "config": {"method": "POST", "webhookCredentialId": "614ac2ef", "body": "{}"}}
    assert field_errors(node) == []
    blank = {"id": "h", "type": "HTTP", "event": "http.request", "config": {"method": "POST", "webhookCredentialId": "  "}}
    assert [(e["code"], e["field"]) for e in field_errors(blank)] == [("REQUIRED", "config.url")]


def test_AI_노드_모델은_비어도_통과_채우면_형식_검사():
    """dev 사전 점검 실측 모양: IEUM-AI-55 이전 Gemini 노드에 model 키가 없고, 기본 모델로 실행돼 왔다."""
    legacy = {"id": "node-2", "type": "AI", "event": "ai.reasoning", "config": {"agentType": "simple", "brand": "x", "credentialId": "",
                                                       "llmProvider": "GEMINI", "prompt": "요약해줘", "tools": []}}
    assert field_errors(legacy) == []
    assert field_errors({**legacy, "config": {**legacy["config"], "model": None}}) == []
    bad = {**legacy, "config": {**legacy["config"], "model": "{{nodes.t.output.m}}"}}
    assert [(e["code"], e["field"]) for e in field_errors(bad)] == [("REF_NOT_ALLOWED", "config.model")]


def test_AI_노드_도구_칸은_비어도_통과():
    node = _ai("w", "ai.agent", agentType="react", tools=[{"name": "builtin:web_search"}])  # query 필수지만 AI가 결정
    assert match_entry(node)["id"] == "ai.web_search"
    assert field_errors(node) == []


def test_ai_mcp_catalogId는_도구_칸이_아니라_검사한다():
    node = _ai("m", "ai.agent", agentType="react", tools=[{"name": "mcp", "config": {}}])
    assert [e["field"] for e in field_errors(node)] == ["config.tools.0.config.catalogId"]


def test_condition_isEmpty_계열은_right_비어도_통과():
    for op in ("isEmpty", "isNotEmpty"):
        node = {"id": "c", "type": "CONDITION", "event": "branch.if", "config": {"operator": op, "left": "{{nodes.a.output.output}}"}}
        assert field_errors(node) == []
    node = {"id": "c", "type": "CONDITION", "event": "branch.if", "config": {"operator": "equals", "left": "x"}}
    assert [e["field"] for e in field_errors(node)] == ["config.right"]


def test_condition_옛_표기_leftValue_rightValue도_채운_것으로_본다():
    """BE ConditionNodeExecutor는 left/right가 없으면 leftValue/rightValue를 읽는다."""
    node = {"id": "c", "type": "CONDITION", "event": "branch.if", "config": {"operator": "equals", "leftValue": "x", "rightValue": "y"}}
    assert field_errors(node) == []


def _cond(nid="c", **config):
    return {"id": nid, "type": "CONDITION", "event": "branch.if", "config": config}


def _br(key="b_00000001", name="긴급", rules=None, **extra):
    return {"key": key, "name": name, "rules": rules if rules is not None
            else [{"left": "{{nodes.t.output.x}}", "operator": "contains", "right": "긴급"}], **extra}


def test_새_모양_분기는_레거시_칸_없이_통과하고_레거시_칸을_보지_않는다():
    assert field_errors(_cond(branches=[_br()])) == []
    assert field_errors(_cond(branches=[_br()], operator="같음", left="", right="")) == []


@pytest.mark.parametrize("config", [{}, {"branches": []}, {"branches": None}])
def test_레거시_분기는_operator_left_right_필수(config):
    assert _codes(field_errors(_cond(**config))) == [
        ("REQUIRED", "c", "config.left"), ("REQUIRED", "c", "config.operator"), ("REQUIRED", "c", "config.right")]


@pytest.mark.parametrize("op", ["isTrue", "isFalse"])
def test_참거짓_연산자는_right가_비어도_통과(op):
    assert field_errors(_cond(operator=op, left="x")) == []


def test_갈래_안_조건_right는_연산자가_쓰면_필수():
    rules = [{"left": "a", "operator": "equals"}, {"left": "a", "operator": "isEmpty"}, {"left": "a", "operator": "isTrue"}]
    assert _codes(field_errors(_cond(branches=[_br(rules=rules)]))) == [
        ("REQUIRED", "c", "config.branches.0.rules.0.right")]


def test_갈래_key_이름_조건이_비면_REQUIRED():
    assert _codes(field_errors(_cond(branches=[{"key": "", "name": "", "rules": []}]))) == [
        ("REQUIRED", "c", "config.branches.0.key"), ("REQUIRED", "c", "config.branches.0.name"),
        ("REQUIRED", "c", "config.branches.0.rules")]


def test_갈래_key_중복_예약어와_이름_중복은_CHOICE():
    branches = [_br("b_1", "A"), _br("b_1", "B"), _br("else", "C"), _br("true", "A")]
    assert _codes(field_errors(_cond(branches=branches))) == [
        ("CHOICE", "c", "config.branches.1.key"), ("CHOICE", "c", "config.branches.2.key"),
        ("CHOICE", "c", "config.branches.3.key"), ("CHOICE", "c", "config.branches.3.name")]


def test_갈래_key_공백은_빈_값이_아니고_예약어는_정확히_같을_때만():
    """컨트롤러 판정 F3 — 빈 값은 None·""만, 예약어는 정확 일치."""
    branches = [_br(" ", "A"), _br("Else", "B"), _br(" else", "C"), _br("false", "D")]
    assert _codes(field_errors(_cond(branches=branches))) == [("CHOICE", "c", "config.branches.3.key")]


def test_갈래_조건_10개_초과는_TOO_LARGE():
    many = [_br(f"b_{i:08x}", f"갈래{i}") for i in range(11)]
    assert _codes(field_errors(_cond(branches=many))) == [("TOO_LARGE", "c", "config.branches")]
    assert field_errors(_cond(branches=many[:10])) == []
    rules = [{"left": "a", "operator": "equals", "right": "b"}] * 11
    assert _codes(field_errors(_cond(branches=[_br(rules=rules)]))) == [("TOO_LARGE", "c", "config.branches.0.rules")]


def test_갈래_조건이_상한을_크게_넘어도_앞_10개만_검사한다():
    """원소 1만 개여도 에러는 TOO_LARGE + 앞 10개 몫 — 원소마다 에러를 만들지 않는다(메모리 증폭 방지)."""
    errors = field_errors(_cond(branches=[{}] * 10000))
    assert ("TOO_LARGE", "c", "config.branches") in _codes(errors)
    assert all(int(e["field"].split(".")[2]) < 10 for e in errors if e["field"].startswith("config.branches."))
    assert len(errors) <= 1 + 10 * 5
    errors = field_errors(_cond(branches=[_br(rules=[{}] * 10000)]))
    assert ("TOO_LARGE", "c", "config.branches.0.rules") in _codes(errors)
    assert all(int(e["field"].split(".")[4]) < 10 for e in errors if e["field"].startswith("config.branches.0.rules."))
    assert len(errors) <= 1 + 10 * 5


def test_없는_연산자_match_pick_otherwise는_CHOICE():
    node = _cond(branches=[_br(rules=[{"left": "a", "operator": "regex", "right": "x"}], match="some")],
                 pick="many", otherwise="skip")
    assert _codes(field_errors(node)) == [
        ("CHOICE", "c", "config.branches.0.match"), ("CHOICE", "c", "config.branches.0.rules.0.operator"),
        ("CHOICE", "c", "config.otherwise"), ("CHOICE", "c", "config.pick")]


def test_갈래_key와_이름에는_참조식을_넣을_수_없다():
    assert _codes(field_errors(_cond(branches=[_br(key="{{nodes.t.output.k}}")]))) == [
        ("REF_NOT_ALLOWED", "c", "config.branches.0.key")]


@pytest.mark.parametrize("branches, expected", [
    (["x"], [("TYPE", "c", "config.branches.0")]),
    ([_br(), 3], [("TYPE", "c", "config.branches.1")]),
    ([_br(key=["b_1"]), _br(key=["b_1"], name={"a": 1})],
     [("TYPE", "c", "config.branches.0.key"), ("TYPE", "c", "config.branches.1.key"),
      ("TYPE", "c", "config.branches.1.name")]),
    ([_br(key=5, rules=["x"])], [("TYPE", "c", "config.branches.0.key"), ("TYPE", "c", "config.branches.0.rules.0")]),
])
def test_갈래_조건_원소가_객체가_아니거나_key_이름이_문자열이_아니면_TYPE(branches, expected):
    assert _codes(field_errors(_cond(branches=branches))) == expected  # 예외 없이, 해시 안 되는 값도


def _refs(errors):
    return [(e["code"], e["nodeId"], e["field"]) for e in errors if e["code"].startswith("REF_")]


def test_새_모양_분기의_참조_오류는_config_branches까지_짚고_레거시_칸은_보지_않는다():
    node = _cond(branches=[_br(rules=[{"left": "{{nodes.zz.output.a}}", "operator": "equals", "right": "x"}])],
                 operator="{{nodes.zz.output.b}}")
    nodes, edges = _chain(node)
    assert _refs(validate_workflow(nodes, edges)) == [("REF_UNKNOWN_NODE", "c", "config.branches")]


def test_분기_출력_예약_키_참조는_REF_UNKNOWN_FIELD():
    slack = _act("x", "slack_bot.send_channel_message", channel="C1",
                 text="{{nodes.c.output._branchKeys}} {{nodes.c.output.branch}} {{nodes.c.output.matched}}")
    nodes = [TRIGGER, _cond(branches=[_br()]), slack]
    edges = [{"source": "t", "target": "c"}, {"source": "c", "target": "x", "conditionType": "b_00000001"}]
    assert _refs(validate_workflow(nodes, edges)) == [("REF_UNKNOWN_FIELD", "x", "config.text")]


_X = {"id": "x", "type": "TRANSFORM", "event": "transform.map", "config": {}}
_Y = {"id": "y", "type": "TRANSFORM", "event": "transform.map", "config": {}}


def _edge_errors(cond, *out_edges):
    """t → c → (x, y). out_edges = (target, conditionType) — conditionType None이면 키를 뺀다."""
    edges = [{"source": "t", "target": "c"}] + [
        {"source": "c", "target": tgt, **({} if ct is None else {"conditionType": ct})} for tgt, ct in out_edges]
    return [(e["nodeId"], e["field"]) for e in validate_workflow([TRIGGER, cond, _X, _Y], edges)
            if e["code"] == "EDGE_INVALID"]


def test_갈래_목록은_key_이름과_그_외():
    assert condition_branches(_cond(branches=[_br("b_1", "긴급")])) == [("b_1", "긴급"), ("else", "그 외")]
    assert condition_branches(_cond(branches=[_br("b_1", "긴급")], otherwise="stop")) == [("b_1", "긴급")]
    assert condition_branches(_cond(operator="equals")) == [("true", "참"), ("false", "그 외")]
    assert condition_branches(_cond(branches=["x", _br("", "빈")])) == [("else", "그 외")]


def test_새_모양_분기_엣지는_갈래_key나_else만():
    cond = _cond(branches=[_br("b_aaaaaaaa", "긴급")])
    assert _edge_errors(cond, ("x", "b_aaaaaaaa"), ("y", "else")) == []
    assert _edge_errors(cond, ("x", "true"), ("y", None)) == [("c", None), ("c", None)]


def test_멈춤_분기의_else_엣지는_EDGE_INVALID():
    cond = _cond(branches=[_br("b_aaaaaaaa", "긴급")], otherwise="stop")
    assert _edge_errors(cond, ("x", "b_aaaaaaaa"), ("y", "else")) == [("c", None)]


def test_레거시_분기_엣지는_true_false만():
    cond = _cond(operator="equals", left="a", right="b")
    assert _edge_errors(cond, ("x", "true"), ("y", "false")) == []
    assert _edge_errors(cond, ("x", "else")) == [("c", None)]


@pytest.mark.parametrize("ct", [["b_aaaaaaaa"], {"k": 1}, 1])
def test_분기_엣지_conditionType이_문자열이_아니어도_예외_없음(ct):
    assert _edge_errors(_cond(branches=[_br("b_aaaaaaaa")]), ("x", ct)) == [("c", None)]


def test_분기_아닌_노드의_엣지_conditionType은_보지_않는다():
    edges = [{"source": "t", "target": "x", "conditionType": "zzz"}]
    assert [e for e in validate_workflow([TRIGGER, _X], edges) if e["code"] == "EDGE_INVALID"] == []


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
    node = _ai("w", "ai.agent", agentType="react", tools=[{"name": "builtin:web_search", "config": {"max_results": value}}])
    assert (field_errors(node) == []) is ok


@pytest.mark.parametrize("value,ok", [('[["a", 1]]', True), ([["a", 1]], True), ("[[a", False), (5, False), ("[" * 100000, False)])
def test_json_TYPE은_JSON_문자열도_허용(value, ok):
    """json 칸은 JSON 문자열로도 저장된다(notion.query_database filter_json: str)."""
    node = _act("q", "notion.query_database", database_id="d", filter_json=value)
    assert (field_errors(node) == []) is ok


def test_dict_TYPE():
    node = {"id": "x", "type": "TRANSFORM", "event": "transform.map", "config": {"mappings": "a=b"}}
    assert [(e["code"], e["field"]) for e in field_errors(node)] == [("TYPE", "config.mappings")]


def test_참조식_값은_TYPE_CHOICE_건너뜀():
    node = _ai("w", "ai.agent", agentType="react", tools=[{"name": "builtin:web_search",
                                               "config": {"max_results": "{{nodes.t.output.n}}"}}])
    assert field_errors(node) == []


@pytest.mark.parametrize("op", ["같음", ["equals"], {"id": "equals"}])
def test_choices_밖의_값은_CHOICE(op):
    """목록 칸이 아닌 choices 칸에 list·dict가 들어 있어도 CHOICE — 해시 안 되는 값으로 예외가 나면 안 된다."""
    node = {"id": "c", "type": "CONDITION", "event": "branch.if", "config": {"operator": op, "left": "a", "right": "b"}}
    assert [(e["code"], e["field"]) for e in field_errors(node)] == [("CHOICE", "config.operator")]


def test_condition_operator가_목록이면_right_예외를_적용하지_않는다():
    """operator가 해시 안 되는 값이어도 예외 없이 right를 필수로 본다(500이면 BE가 검증을 건너뛴다)."""
    node = {"id": "c", "type": "CONDITION", "event": "branch.if", "config": {"operator": ["isEmpty"], "left": "a"}}
    assert _codes(field_errors(node)) == [("CHOICE", "c", "config.operator"), ("REQUIRED", "c", "config.right")]


def test_object_목록_칸은_원소_CHOICE를_보지_않는다():
    """ai.agent tools 선택지는 액션 항목뿐이라 원소를 비교하면 builtin:web_search 섞인 노드가 오탐된다."""
    node = _ai("g", "ai.agent", agentType="react", tools=["builtin:web_search", "builtin:http_fetch"])
    assert match_entry(node)["id"] == "ai.agent"
    assert field_errors(node) == []


def test_ref_false_칸의_참조식은_REF_NOT_ALLOWED():
    node = {"id": "c", "type": "CONDITION", "event": "branch.if", "config": {"operator": "{{nodes.t.output.op}}", "left": "a", "right": "b"}}
    assert [(e["code"], e["field"]) for e in field_errors(node)] == [("REF_NOT_ALLOWED", "config.operator")]


def test_매칭되지_않는_노드는_칸_검사를_건너뛴다():
    assert match_entry({"id": "x", "type": "LOOP", "config": {}}) is None
    assert field_errors({"id": "x", "type": "LOOP", "config": {}}) == []


def test_모르는_event_노드는_칸_검사를_건너뛰고_참조_검사는_받는다():
    """BE 카탈로그 ⊇ agent 카탈로그 — agent가 모르는 event는 422도 칸 에러도 아니다(spec §4 모르는 event)."""
    node = _act("x", "jira.create_issue", summary="{{nodes.zz.output.x}}")
    assert field_errors(node) == []
    assert _codes(validate_workflow(*_chain(node))) == [("REF_UNKNOWN_NODE", "x", None)]


def test_event_없는_노드는_옛_모양이어도_칸_검사를_건너뛴다():
    """레거시 판정 없음 — config.tools[0]만 있고 event가 없으면 매칭하지 않는다."""
    legacy = {"id": "s", "type": "ACTION", "config": {"tools": [{"name": "slack", "config": {}}]}}
    assert match_entry(legacy) is None
    assert field_errors(legacy) == []


def test_ACTION_옛_tools_칸은_입력으로_보지_않는다():
    """ACTION 입력 칸은 config.<칸>뿐 — config.tools[0].config에 값이 있어도 칸은 비어 있다."""
    node = _act("s", "slack.send_message",
                tools=[{"name": "slack", "config": {"message": "안녕", "webhookCredentialId": "wh"}}])
    assert sorted(e["field"] for e in field_errors(node)) == ["config.message", "config.webhookCredentialId"]


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
    http = {"id": "h", "type": "HTTP", "event": "http.request", "config": {"url": "https://x.io", "body": '{"text": "{{nodes.a.output.output}}", "meta": {"k": 1}}'}}
    nodes, edges = _chain(_ai("a"), http)
    assert validate_workflow(nodes, edges) == []


def test_중첩_경로_참조는_통과():
    lst = _act("l", "github.list_issues", owner="o", repo="r")
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
    tr = {"id": "x", "type": "TRANSFORM", "event": "transform.map", "config": {"mappings": {"pr": "{{nodes.t.output.body}}"}}}
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
    http = {"id": "h", "type": "HTTP", "event": "http.request", "config": {"url": "https://x.io", "headers": {"X": "{{nodes.zz.output.x}}"}}}
    assert _codes(validate_workflow(*_chain(http))) == [("REF_UNKNOWN_NODE", "h", "config.headers")]


DS_ID = "1f2e3d4c5b6a47988796a5b4c3d2e1f0"


def test_동적_칸_안_참조식_field는_칸_경로와_키():
    """spec 2026-10-26 §8 — ACTION 동적 칸(fieldsSource) 안의 참조는 <칸 경로>.<키>까지 짚는다. date처럼 한 단 더
    들어간 값도 키에서 멈춘다."""
    node = _act("r", "notion.create_row", data_source_id=DS_ID, properties={
        "title": "{{nodes.t.output.subject}}", "a%3Bc": "{{nodes.zz.output.x}}",
        "due": {"start": "{{nodes.gone.output.d}}"}})
    assert _codes(validate_workflow(*_chain(node))) == [
        ("REF_UNKNOWN_NODE", "r", "config.properties.a%3Bc"), ("REF_UNKNOWN_NODE", "r", "config.properties.due")]


def test_동적_칸_전체를_참조식으로_채우면_REF_NOT_ALLOWED_dict_아니면_TYPE():
    """칸 전체를 참조식 하나로 채우면 BE가 문자열로 치환해 dict 인자가 깨진다(spec §3-2 ref: false)."""
    node = _act("r", "notion.update_row", row_id="{{nodes.t.output.row}}", properties="{{nodes.t.output.props}}")
    assert [(e["code"], e["field"]) for e in field_errors(node)] == [("REF_NOT_ALLOWED", "config.properties")]
    for bad in (["title"], "제목"):
        node = _act("r", "notion.update_row", row_id="{{nodes.t.output.row}}", properties=bad)
        assert [(e["code"], e["field"]) for e in field_errors(node)] == [("TYPE", "config.properties")]
    # 안쪽 키별 REQUIRED·TYPE은 보지 않는다 — 스키마가 없다(D14)
    node = _act("r", "notion.update_row", row_id="{{nodes.t.output.row}}", properties={"num": ["x"], "": None})
    assert field_errors(node) == []


def test_AI_노드_도구_config_안_속성_참조는_지금처럼_config_tools():
    """동적 칸 경로는 ACTION만 — AI 노드 tools[].config는 프리셋(⑤)이 생길 때 config.tools.0.config.properties.<키>."""
    ai = _ai("a", event="ai.agent", agentType="react", tools=[{"name": "builtin:notion_create_row", "config": {
        "data_source_id": DS_ID, "properties": {"title": "{{nodes.zz.output.x}}"}}}])
    assert _codes(validate_workflow(*_chain(ai))) == [("REF_UNKNOWN_NODE", "a", "config.tools")]


def test_outputDynamic_미매칭_대상은_출력_칸을_보지_않는다():
    nodes, edges = _chain(_ai("b", prompt="{{nodes.t.output.anything}}"))  # manual.run = outputDynamic
    assert validate_workflow(nodes, edges) == []
    loop = {"id": "x", "type": "LOOP", "config": {}}
    nodes, edges = _chain(loop, _ai("b", prompt="{{nodes.x.output.anything}}"))
    assert validate_workflow(nodes, edges) == []


def test_도구_칸_참조식의_field는_그_칸_path():
    act = _act("s", "slack.send_message", webhookCredentialId="wh", message="{{nodes.t.output.x}} {{nodes.q.output.y}}")
    nodes, edges = _chain(act)
    assert _codes(validate_workflow(nodes, edges)) == [("REF_UNKNOWN_NODE", "s", "config.message")]


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
    "discord.send_message": ["config.content", "config.webhookCredentialId"],
    "github.create_issue": ["config.owner", "config.repo", "config.title"],
    "gmail.send_email": ["config.body", "config.subject"],
    "github.list_issues": ["config.owner", "config.repo"],
    "github.get_pr_files": ["config.owner", "config.pull_number", "config.repo"],
    "github.submit_pr_review": ["config.owner", "config.pull_number", "config.repo"],
    "github.find_issue": ["config.owner", "config.repo", "config.search_by", "config.value"],
    "github.update_issue": ["config.issue_number", "config.owner", "config.repo"],
    "github.add_labels": ["config.issue_number", "config.labels", "config.owner", "config.repo"],
    "github.create_comment": ["config.body", "config.issue_number", "config.owner", "config.repo"],
    "github.find_or_create_issue": ["config.owner", "config.repo", "config.search_by", "config.title",
                                    "config.value"],
    "github.create_pull_request": ["config.base", "config.head", "config.owner", "config.repo", "config.title"],
    "github.update_pull_request": ["config.owner", "config.pull_number", "config.repo"],
    "github.request_reviewers": ["config.owner", "config.pull_number", "config.repo", "config.reviewers"],
    "github.find_pull_request": ["config.owner", "config.repo", "config.search_by", "config.value"],
    "github.find_or_create_pull_request": ["config.base", "config.head", "config.owner", "config.repo",
                                           "config.title"],
    "github.get_pr_reviews": ["config.owner", "config.pull_number", "config.repo"],
    "github.find_branch": ["config.owner", "config.repo"],
    "github.get_file": ["config.owner", "config.path", "config.repo"],
    "github.find_repo": ["config.owner", "config.repo"],
    "github.find_org": ["config.org"],
    "github.check_org_membership": ["config.org", "config.username"],
    "github.find_user": [],
    "delay.for": ["config.amount", "config.unit"],
    "delay.until": ["config.until"],
    "delay.queue": ["config.amount", "config.queueName", "config.unit"],
    "google_calendar.create": ["config.end_datetime", "config.start_datetime", "config.summary"],
    "google_calendar.delete": ["config.event_id"],
    "google_calendar.find": [],
    "google_calendar.update": ["config.event_id"],
    "google_drive.read": ["config.file_id"],
    "google_drive.upload": ["config.content", "config.name"],
    "google_sheets.append": ["config.columns", "config.sheet_id", "config.spreadsheet_id"],
    "google_sheets.append_top": ["config.columns", "config.sheet_id", "config.spreadsheet_id"],
    "google_sheets.update_row": ["config.columns", "config.row_number", "config.sheet_id", "config.spreadsheet_id"],
    "google_sheets.lookup_row": ["config.lookup_column", "config.lookup_value", "config.sheet_id",
                                 "config.spreadsheet_id"],
    "google_sheets.find_or_create_row": ["config.columns", "config.lookup_column", "config.lookup_value",
                                         "config.sheet_id", "config.spreadsheet_id"],
    "google_sheets.read": ["config.cell_range", "config.sheet_id", "config.spreadsheet_id"],
    "notion.append_block": ["config.content", "config.page_id"],
    "notion.create_page": ["config.content", "config.parent_page_id", "config.title"],
    "notion.query_database": ["config.database_id"],
    "notion.create_row": ["config.data_source_id"],
    "notion.update_row": ["config.row_id"],
    "notion.read_page": ["config.page_id"],
    "notion.search": ["config.query"],
    "notion.update_page": ["config.page_id"],
    "slack.send_message": ["config.message", "config.webhookCredentialId"],
    "slack_bot.send_channel_message": ["config.channel", "config.text"],
    "discord_bot.send_channel_message": ["config.channel_id", "config.content"],
    "ai.reasoning": ["config.llmProvider", "config.prompt"],
    "github.new_issue": ["config.repoId"],
    "github.new_pull_request": ["config.repoId"],
    "gmail.new_email": [],
    "schedule.every": ["config.cron"],
    "schedule.hourly": [],
    "schedule.daily": ["config.hour"],
    "schedule.weekly": ["config.hour", "config.weekday"],
    "schedule.monthly": ["config.day", "config.hour"],
    "webhook.catch": [],
    # ai.agent fixed(tools [], agentType react)는 AI라 프리셋 match를 보고 ai.github_query가 된다 — 도구를 고르기 전 모양
    "ai.agent": ["config.llmProvider", "config.prompt"],
    "approval.request": [],
    "approval.collect": ["config.fields"],
    "branch.if": ["config.left", "config.operator", "config.right"],
    "http.request": ["config.url"],
    "transform.map": ["config.mappings"],
    "manual.run": [],
    # 포맷터 조건부 필수 칸(customPattern·index·value)은 카탈로그 required: false — 실행기만 검사한다(IEUM-AI-98)
    "formatter.date_add": ["config.expression", "config.input"],
    "formatter.date_compare": ["config.input", "config.other"],
    "formatter.date_format": ["config.input"],
    "formatter.list_pick": ["config.input"],
    "formatter.list_to_text": ["config.input"],
    "formatter.lookup_table": ["config.input", "config.table"],
    "formatter.number_currency": ["config.input"],
    "formatter.number_format": ["config.input"],
    "formatter.number_math": ["config.input", "config.operation"],
    "formatter.number_phone": ["config.input"],
    "formatter.text_case": ["config.input"],
    "formatter.text_default": ["config.defaultValue", "config.input"],
    "formatter.text_extract": ["config.input", "config.kind"],
    "formatter.text_extract_pattern": ["config.input", "config.pattern"],
    "formatter.text_length": ["config.input"],
    "formatter.text_markdown_to_html": ["config.input"],
    "formatter.text_replace": ["config.find", "config.input"],
    "formatter.text_split": ["config.input"],
    "formatter.text_strip_html": ["config.input"],
    "formatter.text_trim": ["config.input"],
    "formatter.text_truncate": ["config.input", "config.maxLength"],
    "formatter.text_url_encode": ["config.input"],
}


def test_카탈로그_전_항목_기본값_노드():
    actual = {}
    # 빌더 항목 + match 보유 프리셋. 노드 event = fixed.event(프리셋은 ai.agent) — 프리셋 칸 규칙은 AI match가 고른다
    for t in load_templates().values():
        if not (t["builder"] or (t.get("preset") and t.get("match"))):
            continue
        node = {"id": "n", "type": t["fixed"]["type"], "event": t["fixed"]["event"],
                "config": copy.deepcopy(t["fixed"].get("config") or {})}
        errors = field_errors(node)
        assert {e["code"] for e in errors} <= {"REQUIRED"}, (t["id"], errors)  # fixed 자체는 형식이 맞아야 한다
        actual[t["id"]] = sorted(e["field"] for e in errors)
    assert actual == FIXED_NODE_REQUIRED


def _delay(event, **config):
    return {"id": "d", "type": "DELAY", "event": event, "config": config}


def test_delay_queue_필수칸_REQUIRED():
    nodes, edges = _chain(_delay("delay.queue"))
    assert _codes(validate_workflow(nodes, edges)) == [
        ("REQUIRED", "d", "config.amount"), ("REQUIRED", "d", "config.queueName"), ("REQUIRED", "d", "config.unit")]


def test_delay_unit_목록밖_CHOICE():
    nodes, edges = _chain(_delay("delay.for", amount=10, unit="months"))
    assert _codes(validate_workflow(nodes, edges)) == [("CHOICE", "d", "config.unit")]


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
    cond = {"id": "c", "type": "CONDITION", "event": "branch.if", "config": {"operator": "o" * 200, "left": "a", "right": "b",
                                                       "extra": "{{" + long + "}} {{nodes." + "g" * 200 + ".output.x}}"}}
    sched = _sched("5" * 200)
    dup = {"id": "q" * 200, "type": "HTTP", "event": "http.request", "config": {"url": "https://x.io"}}
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
    errors = validate_workflow(*_chain({"id": "n" * 300_000, "type": "HTTP", "event": "http.request", "config": {}}))
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
    nodes, edges = _chain({"id": "h", "type": "HTTP", "event": "http.request", "config": {"url": "{{a}}" * 200_000}})
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
    nodes = [{"id": "t", "type": "TRIGGER", "event": "webhook.catch", "config": {}},
             {"id": "a", "type": "TRANSFORM", "config": {"tools": ["x"] * k}},
             {"id": "b", "type": "TRANSFORM", "config": {"m": ["{{nodes.a.output.x}}"] * k}}]
    edges = [{"source": "t", "target": "a"}, {"source": "a", "target": "b"}]
    t = time.monotonic()
    validate_workflow(nodes, edges)
    assert time.monotonic() - t < 1


# ── 칸 검사가 건너뛰는 칸은 참조 검사도 건너뛴다 (spec §7-16) ──

def test_isEmpty_조건의_right_참조는_검사하지_않는다():
    cond = {"id": "c", "type": "CONDITION", "event": "branch.if",
            "config": {"operator": "isEmpty", "left": "{{nodes.t.output.x}}", "right": "{{nodes.gone.output.x}}"}}
    assert validate_workflow(*_chain(cond)) == []


def test_웹훅_크레덴셜이_있는_HTTP의_url_참조는_검사하지_않는다():
    http = {"id": "h", "type": "HTTP", "event": "http.request", "config": {"method": "POST", "webhookCredentialId": "614ac2ef-0000",
                                                  "url": "https://x/{{nodes.gone.output.x}}"}}
    assert validate_workflow(*_chain(http)) == []


def test_건너뛴_칸의_하위_경로와_옛_표기는_건너뛰고_형제_키는_검사한다():
    gone = "{{nodes.gone.output.x}}"
    base = {"operator": "isEmpty", "left": "{{nodes.t.output.x}}"}
    for extra in ({"right": {"a": [gone]}}, {"rightValue": gone}):
        cond = {"id": "c", "type": "CONDITION", "event": "branch.if", "config": {**base, **extra}}
        assert validate_workflow(*_chain(cond)) == [], extra
    cond = {"id": "c", "type": "CONDITION", "event": "branch.if", "config": {**base, "rightX": gone}}
    assert [e["code"] for e in validate_workflow(*_chain(cond))] == ["REF_UNKNOWN_NODE"]


def test_재귀_한도보다_깊은_중첩도_훑는다():
    """_iter_strings는 명시 스택이다 — 재귀 yield from은 깊이만큼 문자열을 올려 보내 깊은 중첩에서 2.6배 느렸고
    (깊이 94 × 99만 개 3.1s → 1.2s, BE 3초 타임아웃), 재귀 한도를 넘으면 RecursionError로 500이 된다."""
    deep = ["{{}}"]
    for _ in range(3000):
        deep = [deep]
    nodes = [TRIGGER, {"id": "x", "type": "TRANSFORM", "event": "transform.map", "config": {"mappings": {"a": "b"}, "x": deep}}]
    assert [e["code"] for e in validate_workflow(nodes, [{"source": "t", "target": "x"}])] == ["REF_FORMAT"]


def test_트리거_종류는_event만_본다_config_triggerType은_무시():
    """옛 config.triggerType이 남아 있어도 event가 트리거 종류다 — 레거시 우선 판정 없음."""
    sched = {"id": "t", "type": "TRIGGER", "event": "schedule.every", "config": {"triggerType": "MANUAL", "cron": "매일 9시"}}
    assert _codes(validate_workflow([sched], [])) == [("CRON", "t", "config.cron")]
    manual = {"id": "t", "type": "TRIGGER", "event": "manual.run", "config": {"triggerType": "SCHEDULE", "cron": "매일 9시"}}
    assert validate_workflow([manual], []) == []


def test_모르는_event_트리거는_cron을_보지_않고_TRIGGER_COUNT에는_센다():
    """트리거 개수는 type으로 센다(규칙 불변). 모르는 트리거 event는 종류를 몰라 cron 검사를 건너뛴다."""
    future = {"id": "t", "type": "TRIGGER", "event": "jira.new_issue", "config": {"cron": "매일 9시"}}
    assert validate_workflow([future], []) == []
    assert ("TRIGGER_COUNT", None, None) in _codes(validate_workflow([future, {**TRIGGER, "id": "t2"}],
                                                                     [{"source": "t", "target": "t2"}]))


def test_레거시_매칭_뷰는_없다():
    """옛 저장 모양 정규화(소문자 type·문자열 tools 원소)는 지웠다 — 칸 검사는 노드 원본을 본다(spec §5.2)."""
    import core.template_registry as tr
    assert not hasattr(tr, "_match_view")


def _fmt(nid="f", event="formatter.date_format", **config):
    return {"id": nid, "type": "TRANSFORM", "event": event, "config": config}


def test_formatter_노드_input_비면_REQUIRED():
    """포맷터는 전용 규칙 없이 카탈로그 칸 검사(필수·choice)와 참조 검사만 받는다(IEUM-AI-98)."""
    assert _codes(validate_workflow(*_chain(_fmt()))) == [("REQUIRED", "f", "config.input")]
    assert _codes(validate_workflow(*_chain(_fmt(input="지금", format="weird")))) == [("CHOICE", "f", "config.format")]
    assert _codes(validate_workflow(*_chain(_fmt(input="{{nodes.zz.output.x}}")))) == [
        ("REF_UNKNOWN_NODE", "f", "config.input")]


# ── 사람 확인(APPROVAL) 목록 칸 — _approval_errors 직접 호출 ──

def _appr(event="approval.request", nid="p", **config):
    return {"id": nid, "type": "APPROVAL", "event": event, "config": config}


def test_approval_items_21개_TOO_LARGE():
    items = [{"key": f"k{i}", "value": "v"} for i in range(20)] + [{"key": "이름", "value": "v"}]
    assert _codes(_approval_errors(_appr(items=items), "p")) == [("TOO_LARGE", "p", "config.items")]


def test_approval_key_한글_TYPE():
    assert _codes(_approval_errors(_appr(items=[{"key": "이름", "value": "v"}]), "p")) == [
        ("TYPE", "p", "config.items.0.key")]


def test_approval_key_뒤만_틀려도_TYPE():
    assert _codes(_approval_errors(_appr(items=[{"key": "a-b", "value": "v"}]), "p")) == [
        ("TYPE", "p", "config.items.0.key")]


def test_approval_key_빈_목록은_REQUIRED_몫이라_에러없음():
    assert _approval_errors(_appr(items=[{"key": [], "value": "v"}]), "p") == []


def test_approval_객체_아닌_원소_TYPE():
    assert _codes(_approval_errors(_appr("approval.collect", fields=["email"]), "p")) == [
        ("TYPE", "p", "config.fields.0")]


def test_approval_choice_options_쉼표뿐_REQUIRED_목록이면_TYPE():
    blank = [{"key": "pick", "label": "고르기", "type": "choice", "options": " , "}]
    assert _codes(_approval_errors(_appr("approval.collect", fields=blank), "p")) == [
        ("REQUIRED", "p", "config.fields.0.options")]
    listed = [{"key": "pick", "label": "고르기", "type": "choice", "options": ["A", "B"]}]
    assert _codes(_approval_errors(_appr("approval.collect", fields=listed), "p")) == [
        ("TYPE", "p", "config.fields.0.options")]


def test_approval_민감_키_TYPE():
    assert _codes(_approval_errors(_appr(items=[{"key": "apiKey", "value": "v"}]), "p")) == [
        ("TYPE", "p", "config.items.0.key")]
    fields = [{"key": "maxTokens", "label": "토큰", "type": "number"}]
    assert _codes(_approval_errors(_appr("approval.collect", fields=fields), "p")) == [
        ("TYPE", "p", "config.fields.0.key")]


def test_approval_key_중복_CHOICE():
    fields = [{"key": "a", "label": "A", "type": "short"}, {"key": "a", "label": "B", "type": "short"}]
    assert _codes(_approval_errors(_appr("approval.collect", fields=fields), "p")) == [
        ("CHOICE", "p", "config.fields.1.key")]


def test_approval_choice_options_없음_REQUIRED():
    fields = [{"key": "pick", "label": "고르기", "type": "choice", "options": ""}]
    assert _codes(_approval_errors(_appr("approval.collect", fields=fields), "p")) == [
        ("REQUIRED", "p", "config.fields.0.options")]


def test_approval_정상_에러없음():
    items = [{"key": "title", "value": "{{nodes.a.output.x}}"}, {"key": "", "value": "v"}, {"key": "{{x}}"}]
    fields = [{"key": "amount", "label": "금액", "type": "number"},
              {"key": "pick", "label": "고르기", "type": "choice", "options": "A,B"}]
    assert _approval_errors(_appr(items=items), "p") == []
    assert _approval_errors(_appr(None, items=items), "p") == []
    assert _approval_errors(_appr("approval.collect", fields=fields), "p") == []


# ── field_errors 경로 — 카탈로그 칸 검사(_check)와 APPROVAL 훅이 함께 돈다 ──

def test_approval_collect_정적검증_연결():
    fields = [{"key": f"k{i}", "label": "칸", "type": "short"} for i in range(20)] + [{"key": "", "type": "nope"}]
    assert _codes(field_errors(_appr("approval.collect", fields=fields))) == [("TOO_LARGE", "p", "config.fields")]


def test_approval_빈_key는_REQUIRED():
    assert _codes(field_errors(_appr(items=[{"key": "", "value": "v"}]))) == [("REQUIRED", "p", "config.items.0.key")]


def test_approval_상한밖_원소는_칸검사_안함():
    items = [{"key": f"k{i}", "value": "v"} for i in range(20)] + [{"key": "", "value": "v"}]
    assert _codes(field_errors(_appr(items=items))) == [("TOO_LARGE", "p", "config.items")]

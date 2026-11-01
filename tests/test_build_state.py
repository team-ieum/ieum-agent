"""대화형 생성 상태 판정 (spec 2026-10-28 §4, IEUM-AI-84)."""
from core.builder.state import (baseline_of, hosts_in, node_hash, node_status, prune, remember_user_input,
                              said_literally, workflow_hash)


def _n(nid="n1", event="slack_bot.send_channel_message", **config):
    return {"id": nid, "event": event, "label": "x", "config": config}


def test_hash는_event와_config만_본다():
    a = _n(channel="C1", text="hi")
    b = {**_n(text="hi", channel="C1"), "label": "다른 이름", "position": {"x": 1, "y": 2}}
    assert node_hash(a) == node_hash(b)
    assert node_hash(a) == node_hash({**a, "config": {**a["config"], "brand": "slack"}})  # BE가 저장 때 덮어쓰는 값
    assert node_hash(a) != node_hash(_n(channel="C2", text="hi"))
    assert node_hash(a) != node_hash(_n(event="slack.send_message", channel="C1", text="hi"))


def test_테스트_뒤_config가_바뀌면_untested():
    node = _n(channel="C1")
    state = {"tests": {"n1": {"status": "success", "hash": node_hash(node)}}}
    assert node_status(node, state) == "success"
    node["config"]["channel"] = "C2"
    assert node_status(node, state) == "untested"


def test_baseline과_같은_노드는_통과_다르면_untested():
    node = _n(channel="C1")
    state = {"baseline": baseline_of([node])}
    assert node_status(node, state) == "passed"
    assert node_status(_n(channel="C9"), state) == "untested"


def test_빌드_중_생긴_노드_팔레트로_더한_노드는_untested():
    state = {"baseline": baseline_of([_n()]), "tests": {}}
    assert node_status(_n(nid="palette-added"), state) == "untested"
    assert node_status(_n(), {}) == "untested"


def test_테스트_기록이_baseline보다_우선():
    node = _n()
    state = {"baseline": baseline_of([node]), "tests": {"n1": {"status": "failed", "hash": node_hash(node)}}}
    assert node_status(node, state) == "failed"


def test_사라진_노드_기록은_지운다():
    assert prune({"a": 1, "b": 2}, ["b", "c"]) == {"b": 2}
    assert prune(None, ["a"]) == {}


def test_workflow_hash는_엣지_분기도_본다():
    nodes = [_n("a", "manual.run"), _n("b", "branch.if")]
    h = workflow_hash(nodes, [{"source": "a", "target": "b"}])
    assert h == workflow_hash(list(reversed(nodes)), [{"source": "a", "target": "b", "conditionType": None}])
    assert h != workflow_hash(nodes, [{"source": "a", "target": "b", "conditionType": "true"}])


def test_사용자_입력에서_호스트를_뽑는다():
    text = "https://Hooks.Example.com/x?y=1 로 보내고 api.test.io:8080 도, 그리고 sub.a.kr으로. 1.2.3.4"
    assert hosts_in(text) == {"hooks.example.com", "api.test.io", "sub.a.kr", "1.2.3.4"}
    assert hosts_in("슬랙 채널로 보내줘") == set()


def test_사용자_입력_누적은_중복_없이():
    delta = remember_user_input({"user_hosts": ["a.com"], "user_text": "처음"}, "b.com 이랑 a.com")
    assert delta["user_hosts"] == ["a.com", "b.com"]
    assert delta["user_text"] == "처음\nb.com 이랑 a.com"


def test_문자_그대로_있는_값만_인정():
    said = "저장소는 ieum/ieum-backend 이고 채널 C0123으로"
    assert said_literally("ieum/ieum-backend", said)
    assert said_literally("C0123", said)          # 한글 조사가 붙어도
    assert not said_literally("ieum", said)        # 부분 문자열은 아니다
    assert not said_literally("C012", said)
    assert not said_literally("", said)

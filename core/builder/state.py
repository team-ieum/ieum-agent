"""대화형 생성 진행 상태(spec 2026-10-28 §4) — agent 세션 state에 두는 값과 그 판정.

state 키: outline · baseline({id: hash}) · tests({id: {status, hash, fields}}) · pending · confirmed ·
user_hosts · user_text · options_seen · paused_notice. 도구는 값 전체를 다시 넣는다(ADK state는 최상위 키 단위 델타)."""
import hashlib
import json
import re

DONE_STATUSES = ("success", "skipped", "passed")
# ponytail: 최근 사용자 입력만 남긴다(드롭다운 값 "사용자가 직접 적은 값" 판정용) — 아주 긴 세션의 옛 입력은 잊는다
_USER_TEXT_MAX = 20_000
_USER_HOSTS_MAX = 200
# 호스트 모양의 ASCII 덩어리(한글 조사가 붙어도 떨어진다: "a.com으로" → a.com). IP도 잡힌다.
_HOST_RE = re.compile(r"(?<![A-Za-z0-9-])(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z0-9-]{1,63}(?![A-Za-z0-9-])")


def node_hash(node: dict) -> str:
    """sha256(event + 정렬된 config JSON) — 테스트 뒤 config가 바뀌었는지 본다. brand는 뺀다: BE가 저장·currentNodes 파생 때
    비AI 노드에 앱 brand를 덮어써(NodeDerivation.derive) 만든 턴의 테스트·확인 hash가 다음 턴에 어긋난다."""
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    config = {k: v for k, v in config.items() if k != "brand"}
    body = str(node.get("event")) + json.dumps(config, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def workflow_hash(nodes: list, edges: list) -> str:
    """finish 확인용 — 노드 hash와 엣지 끝점(분기 포함). 노드 label·위치 변경은 무시한다."""
    body = json.dumps({
        "nodes": sorted((str(n.get("id")), node_hash(n)) for n in nodes),
        "edges": sorted((str(e.get("source")), str(e.get("target")), str(e.get("conditionType"))) for e in edges),
    })
    return hashlib.sha256(body.encode()).hexdigest()


def baseline_of(nodes: list) -> dict:
    return {n["id"]: node_hash(n) for n in nodes}


def node_status(node: dict, state) -> str:
    """success|failed|skipped(테스트 기록, hash 같음) · passed(기록 없음 + 빌드 전부터 같은 모양) · untested(그 밖)."""
    h = node_hash(node)
    test = (state.get("tests") or {}).get(node["id"])
    if test:
        return test["status"] if test.get("hash") == h else "untested"
    if (state.get("baseline") or {}).get(node["id"]) == h:
        return "passed"
    return "untested"


def prune(mapping: dict | None, ids) -> dict:
    """currentNodes에서 사라진 노드의 기록을 지운다."""
    ids = set(ids)
    return {k: v for k, v in (mapping or {}).items() if k in ids}


def hosts_in(text: str) -> set[str]:
    return {m.group(0).lower().rstrip(".") for m in _HOST_RE.finditer(text or "")}


def remember_user_input(state, text: str) -> dict:
    """턴 시작에 사용자 입력을 누적한 state 델타(user_hosts·user_text). 세션 events를 갈아타도 남게 state에 둔다."""
    hosts = list(dict.fromkeys([*(state.get("user_hosts") or []), *sorted(hosts_in(text))]))[-_USER_HOSTS_MAX:]
    user_text = ((state.get("user_text") or "") + "\n" + (text or ""))[-_USER_TEXT_MAX:]
    return {"user_hosts": hosts, "user_text": user_text}


def said_literally(value: str, user_text: str) -> bool:
    """사용자 입력에 값이 문자 그대로 있는가 — 앞뒤가 ASCII 영숫자·-_./가 아닐 때만(부분 문자열 금지, 한글 조사는 허용)."""
    if not value:
        return False
    return re.search(r"(?<![A-Za-z0-9_\-./])" + re.escape(value) + r"(?![A-Za-z0-9_\-./])", user_text or "") is not None

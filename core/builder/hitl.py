"""노드 테스트 판정과 사용자 확인(spec 2026-10-28 §5.1·§5.2). 확인 문구와 요약은 코드가 만든다 — LLM이 대신 쓰지 못한다."""
import json
import re
from urllib.parse import urlsplit

from core.builder.fields import DESTINATION_EMAIL_FIELDS, DESTINATION_URL_FIELDS, clean, writable_fields
from core.template_registry import _get_by_path, event_entry

YES = {"네", "예", "응", "ㅇㅇ", "ㄱ", "좋아", "진행", "테스트해", "yes", "ok"}
NO = {"아니", "아니요", "건너뛰기", "건너뛰어", "스킵", "no"}
FINISH = "__finish__"
ASK_TEST = "테스트할까요? (네 / 건너뛰기)"
ASK_SKIP_ONLY = "앞 단계를 건너뛰어 이 노드는 테스트할 수 없어요. 건너뛸까요? (건너뛰기)"
ASK_FINISH = "완료할까요? (네 / 아니요)"


def classify_reply(text: str) -> str:
    """공백·문장부호를 걷어낸 입력이 긍정·부정 목록과 정확히 같으면 yes·no, 그 밖은 other(대기 폐기)."""
    word = re.sub(r"[\W_]+", "", text or "").lower()
    if word in YES:
        return "yes"
    if word in NO:
        return "no"
    return "other"


def how_to_test(node: dict) -> str:
    """write(확인 후) · read(바로) · app_trigger(바로, 실패하면 건너뛰기 제안) · free(부작용 없음, 바로) · listen(웹훅 — skipped).
    모르는 event와 ai.agent(옛 프리셋 — 도구·서브에이전트가 쓰기를 할 수 있다)는 쓰기로 본다."""
    if node.get("event") == "http.request":  # GET도 url·헤더에 데이터를 실어 내보낼 수 있다
        return "write"
    entry = event_entry(node)
    if entry is None or entry["event"] == "ai.agent":
        return "write"
    if entry["node_type"] == "ACTION":
        return "write" if entry.get("kind") == "action" else "read"
    if entry["node_type"] == "TRIGGER":
        if entry.get("trigger_type") == "WEBHOOK":
            return "listen"
        return "app_trigger" if entry.get("provider") not in (None, "ieum") else "free"
    return "free"


def _title(node: dict) -> str:
    entry = event_entry(node)
    return clean(entry["title"] if entry else node.get("event"), 80)


def _named(text: str, node: dict) -> str:
    """LLM이 정한 label은 코드가 만든 사실 뒤에 따옴표로 — 대상 문구로 위장하지 못한다."""
    label = clean(node.get("label") or "", 60)
    return f'{text} · 이름 "{label}"' if label else text


def target_summary(node: dict, options_seen: dict) -> str:
    """쓰기 대상 요약 — event title, 드롭다운 칸의 값과 이름(이번 세션 list_options 결과에서 id로 찾음), http면 method와 호스트,
    받는 사람 칸(참석자)은 값 그대로. LLM이 쓴 _names는 쓰지 않는다."""
    parts = [_title(node)]
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    for event, path in sorted(DESTINATION_URL_FIELDS):
        if node.get("event") != event:
            continue
        base = path.rsplit(".", 1)[0]
        url, method = _get_by_path(node, path), _get_by_path(node, base + ".method")
        if event == "http.request" and str(config.get("webhookCredentialId") or "").strip():
            target = "등록 웹훅"
        elif event == "http.request" or url not in (None, ""):
            try:
                target = urlsplit(str(url or "")).hostname or "(주소 없음)"
            except ValueError:
                target = "(주소 형식 오류)"
        else:
            continue
        if event == "http.request":
            method = method or "GET"
        parts.append(f"{clean(method, 10)} {clean(target)}" if method else clean(target))
    if node.get("event") == "ai.agent":  # 옛 프리셋 — 도구가 쓰는 대상(받는 사람·채널 등)을 보인다
        for i, tool in enumerate(config.get("tools") or []):
            if not isinstance(tool, dict):
                continue
            tconf = tool.get("config") if isinstance(tool.get("config"), dict) else {}
            args = [f"{clean(k, 30)}={clean(v)}" for k, v in tconf.items()
                    if k not in ("credentialId", "_names") and isinstance(v, (str, int, float))
                    and ("ai.agent", f"config.tools.{i}.config.{k}") not in DESTINATION_URL_FIELDS]  # url은 위에서 호스트만
            parts.append(f"{clean(str(tool.get('name') or '').removeprefix('builtin:'), 40)}({', '.join(args)})")
    for path, f in writable_fields(node).items():
        is_email = (node.get("event"), path) in DESTINATION_EMAIL_FIELDS
        if not (f.get("optionsSource") or is_email):
            continue
        value = _get_by_path(node, path)
        if value in (None, ""):
            continue
        if is_email:
            parts.append(f"{clean(f.get('title') or f['key'], 30)}: {clean(value, 200)}")
            continue
        if not isinstance(value, str):  # currentNodes 경유 노드는 dict·list일 수 있다 — 이름 조회 없이 값 그대로
            parts.append(f"{clean(f.get('title') or f['key'], 30)}: {clean(value)}")
            continue
        name = ((options_seen or {}).get(f"{node['id']}|{path}") or {}).get(value)
        shown = clean(name) if name else f"(목록에서 확인 안 됨, id={clean(value)})"
        parts.append(f"{clean(f.get('title') or f['key'], 30)}: {shown}")
    return _named(" · ".join(parts), node)


def finish_summary(nodes: list, options_seen: dict) -> str:
    """완료 확인 요약 — 켜질 트리거와 쓰기 노드별 대상."""
    triggers = []
    for n in nodes:
        entry = event_entry(n)
        if entry is None or entry["node_type"] != "TRIGGER":
            continue
        cron = ((n.get("config") or {}).get("cron") if isinstance(n.get("config"), dict) else None)
        triggers.append(_named(_title(n) + (f" cron {clean(cron, 40)}" if cron else ""), n))
    writes = [target_summary(n, options_seen) for n in nodes if how_to_test(n) == "write"]
    text = f"켜지는 트리거: {', '.join(triggers) or '없음'}"
    if writes:
        text += f" / 실행 때 쓰는 곳: {' | '.join(writes)}"
    return text


def sample_summary(output) -> str:
    """사용자에게 보여 줄 샘플 요약(필드 4개, 값 30자). LLM에는 주지 않는다(spec §3.5)."""
    if not isinstance(output, dict) or not output:
        return "테스트 성공(출력 없음)"
    items = []
    for key, value in list(output.items())[:4]:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        items.append(f"{clean(key, 30)}: {clean(text, 30)}")
    return "테스트 성공 — " + " · ".join(items)

"""빌더 AI 노드 모델 카드 — 후보 조회·카드 조립·번호 판정(spec 2026-10-30 §5, IEUM-AI-85).

크레덴셜 id는 choices(세션 state)에만 — options·글자 목록·hint에 넣지 않는다."""
import asyncio
import re

from core.builder.fields import _CONTROL, clean
from core.provider_config import resolve_model

PROVIDERS = ("CLAUDE", "OPENAI", "GEMINI")
MAX_PER_PROVIDER = 3
PROVIDER_TITLE = {"CLAUDE": "Claude", "OPENAI": "OpenAI", "GEMINI": "Gemini"}
CIRCLED = "①②③④⑤⑥⑦⑧⑨"
NO_KEY_MESSAGE = ("AI 노드를 쓰려면 AI 키가 필요해요. 통합 설정 → AI 자격증명에서 키를 등록한 뒤 "
                  "'등록했어'라고 말해 주세요.")
_MAX_ID = 200  # tools.MAX_OPTION_ID와 같은 값(순환 import 회피)
_NUMBER = re.compile(r"^([1-9]|[①-⑨])\s*번?$")


def _valid(item) -> bool:
    return (isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"] != ""
            and len(item["id"]) <= _MAX_ID and not _CONTROL.search(item["id"]))


def _items(r: dict) -> list[dict]:
    data = r.get("data")
    items = data.get("items") if isinstance(data, dict) else None
    return [i for i in items if _valid(i)] if isinstance(items, list) else []


def build_choices(chat_provider: str, keys: dict[str, dict], models: dict[str, list[dict]]) -> list[dict]:
    chat = (chat_provider or "").upper()
    order = [chat] + [p for p in PROVIDERS if p != chat] if chat in PROVIDERS else list(PROVIDERS)
    choices = []
    for p in order:
        key = keys.get(p)
        if not key:
            continue
        default = resolve_model(p)
        names = {m["id"]: m.get("name") for m in models.get(p, [])}
        ids = [default] + [m["id"] for m in models.get(p, []) if m["id"] != default]
        for model in ids[:MAX_PER_PROVIDER]:
            choices.append({"n": len(choices) + 1, "provider": p, "credentialId": key["id"], "model": model,
                            "label": clean(names.get(model) or model, 60),
                            "key": clean(key.get("name") or "AI 키", 40),
                            "recommended": p == chat and model == default})
    return choices


async def load_choices(backend, chat_provider: str) -> dict:
    rs = await asyncio.gather(*(backend.options("ieum.credentials", {"llmProvider": p}) for p in PROVIDERS))
    errors = [r["error"] for r in rs if "error" in r]
    if errors:
        return {"error": (errors[0] or {}).get("message") or "AI 키 목록을 조회하지 못했어요."}
    keys = {p: items[0] for p, items in zip(PROVIDERS, map(_items, rs)) if items}
    with_key = [p for p in PROVIDERS if p in keys]
    rs = await asyncio.gather(*(backend.options("ai.models", {"llmProvider": p}) for p in with_key))
    # ai.models 오류면 빈 목록 → 그 제공사는 기본 모델 카드 하나
    models = {p: ([] if "error" in r else _items(r)) for p, r in zip(with_key, rs)}
    return {"choices": build_choices(chat_provider, keys, models)}


def _key_text(c: dict) -> str:
    return f'내 {PROVIDER_TITLE[c["provider"]]} 키 "{c["key"]}"'


def options_of(choices: list[dict]) -> list[dict]:
    return [{"value": str(c["n"]), "label": c["label"],
             "description": _key_text(c) + (" · 추천" if c["recommended"] else "")} for c in choices]


def ask_text(choices: list[dict]) -> str:
    return "AI 노드에 쓸 모델을 골라 주세요: " + " · ".join(
        f'{CIRCLED[c["n"] - 1]} {c["label"]} ({_key_text(c)}' + (", 추천" if c["recommended"] else "") + ")"
        for c in choices)


def match_choice(text: str, choices: list) -> dict | None:
    t = (text or "").strip().rstrip(".!").strip()
    if not t:
        return None
    m = _NUMBER.match(t)
    if m:
        g = m.group(1)
        n = int(g) if g.isascii() else CIRCLED.index(g) + 1
        return next((c for c in choices if c["n"] == n), None)
    same = [c for c in choices if c["label"] == t]
    return same[0] if len(same) == 1 else None


def apply_choice(node: dict, choice: dict) -> None:
    if not isinstance(node.get("config"), dict):
        node["config"] = {}
    node["config"].update(credentialId=choice["credentialId"], llmProvider=choice["provider"], model=choice["model"])

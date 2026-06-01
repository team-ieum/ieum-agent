#!/usr/bin/env python3
"""E2E 수동 시험: GitHub + Notion + Discord 워크플로우 생성(/v1/chat) → 노드 실행(/v1/execute).

resolver 수정 검증 포함. 3개 외부 서비스를 쓰는 워크플로우를 생성시키고,
Notion 노드의 parent_page_id가 resolver로 채워졌는지 점검한 뒤, 각 서비스 노드를
직접 실행해 실제 동작(GitHub 조회 / Notion 생성 / Discord 발송)까지 확인한다.

필요 환경변수:
  GEMINI_API_KEY       : Gemini API 키 (X-LLM-Api-Key)
선택(해당 서비스 검증 시):
  NOTION_TOKEN         : Notion Integration Token (X-Notion-Token)
  GITHUB_TOKEN         : GitHub Token (X-GitHub-Token)
  DISCORD_WEBHOOK_URL  : Discord 웹훅 URL (실행 시 tool config로 주입)
파라미터:
  GITHUB_REPO          : 조회 대상 저장소 "owner/repo" (기본 "anthropics/anthropic-sdk-python")
  NOTION_TARGET        : 저장 대상 Notion 페이지/DB 이름 (resolver가 검색)
  BASE_URL             : 기본 http://localhost:8000
  USER_ID              : 기본 e2e-tester
  RUN_EXECUTE          : "1"이면 STEP 3 실제 실행 수행(외부 발송 포함). 기본 "0"(생성+점검만)

사용:
  GEMINI_API_KEY=xxx NOTION_TOKEN=secret_xxx GITHUB_TOKEN=ghp_xxx \
  DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/...' \
  GITHUB_REPO='owner/repo' NOTION_TARGET='내 노트' RUN_EXECUTE=1 \
  python scripts/e2e_multi_service.py
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request


BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
USER_ID = os.environ.get("USER_ID", "e2e-tester")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "anthropics/anthropic-sdk-python")
NOTION_TARGET = os.environ.get("NOTION_TARGET", "")
RUN_EXECUTE = os.environ.get("RUN_EXECUTE", "0") == "1"


def _headers() -> dict:
    h = {
        "Content-Type": "application/json",
        "X-LLM-Provider": "GEMINI",
        "X-LLM-Api-Key": GEMINI_API_KEY,
        "X-User-Id": USER_ID,
    }
    if NOTION_TOKEN:
        h["X-Notion-Token"] = NOTION_TOKEN
    if GITHUB_TOKEN:
        h["X-GitHub-Token"] = GITHUB_TOKEN
    return h


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(body).encode("utf-8"),
        headers=_headers(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"[HTTP {e.code}] {path}\n{e.read().decode('utf-8')}")
        return {}


def _tool_names(node: dict) -> list:
    tools = (node.get("config") or {}).get("tools") or []
    return [t.get("name") if isinstance(t, dict) else t for t in tools]


def _classify(node: dict) -> str | None:
    """노드를 서비스로 분류한다."""
    if node.get("type") != "AI":
        return None
    names = " ".join(n or "" for n in _tool_names(node))
    prompt = (node.get("config") or {}).get("prompt", "")
    if "notion_create_page" in names:
        return "notion"
    if "discord" in names:
        return "discord"
    # github는 tools:[] 계약 — 프롬프트 키워드로 식별
    if re.search(r"github|깃허브|\bPR\b|pull request", prompt, re.IGNORECASE):
        return "github"
    return None


def _strip_refs(text: str) -> str:
    """{{nodes.x.output.y}} 참조를 단일 노드 실행용 더미 값으로 치환한다."""
    return re.sub(r"\{\{[^}]*\}\}", "테스트 데이터", text)


def _execute_node(node: dict, service: str) -> None:
    cfg = node.get("config") or {}
    tools = cfg.get("tools") or []
    if service == "discord":
        if not DISCORD_WEBHOOK_URL:
            print("  [skip] DISCORD_WEBHOOK_URL 미설정 — Discord 실행 생략")
            return
        tools = [{"name": "discord", "config": {"webhook_url": DISCORD_WEBHOOK_URL}}]
    body = {
        "nodeId": node.get("id", "node-x"),
        "renderedPrompt": _strip_refs(cfg.get("prompt", "")),
        "systemMessage": cfg.get("systemMessage"),
        "agentType": cfg.get("agentType", "react"),
        "tools": tools,
    }
    result = _post("/v1/execute", body)
    print(json.dumps(result, ensure_ascii=False, indent=2)[:1500])


def main() -> None:
    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY 환경변수가 필요합니다.")
        sys.exit(2)

    target_clause = f" 저장 위치는 '{NOTION_TARGET}' 페이지." if NOTION_TARGET else ""
    gen_prompt = (
        "매뉴얼 트리거로 동작하는 워크플로우를 만들어줘. "
        f"GitHub 저장소 '{GITHUB_REPO}'의 최근 열린 Pull Request 목록을 조회하고, "
        "그 내용을 요약해서 Notion에 새 페이지로 저장한 뒤, "
        "Discord로 '리포트 완료' 알림을 보내줘." + target_clause
    )

    print("=" * 72)
    print("STEP 1) 워크플로우 생성 — POST /v1/chat")
    print("prompt:", gen_prompt)
    print("=" * 72)
    chat = _post("/v1/chat", {"prompt": gen_prompt})
    if not chat:
        return
    print("type:", chat.get("type"), "| workflowName:", chat.get("workflowName"))
    nodes = chat.get("nodes") or []
    print(json.dumps(nodes, ensure_ascii=False, indent=2))

    found = {}
    for n in nodes:
        svc = _classify(n)
        if svc and svc not in found:
            found[svc] = n

    print("\n" + "=" * 72)
    print("STEP 2) 서비스 노드 식별 + resolver 효과 점검")
    print("=" * 72)
    for svc in ("github", "notion", "discord"):
        node = found.get(svc)
        print(f"\n[{svc}] {'발견: ' + node['id'] if node else '없음'}")
        if not node:
            continue
        prompt = (node.get("config") or {}).get("prompt", "")
        print("  tools:", _tool_names(node))
        print("  prompt:", prompt)
        if svc == "notion":
            filled = bool(re.search(r"parent_page_id['\"]?\s*[:=]\s*['\"][^'\"]+['\"]", prompt))
            print("  → parent_page_id 채워짐?:", "예 (resolver 작동)" if filled else "아니오 (빈 값/누락)")
        if svc == "github":
            print("  → tools:[] 계약 유지?:", "예" if not _tool_names(node) else "아니오(github 도구명 잘못 주입)")

    if not RUN_EXECUTE:
        print("\n[RUN_EXECUTE=0] 생성+점검만 수행. 실제 실행하려면 RUN_EXECUTE=1 로 재실행.")
        return

    print("\n" + "=" * 72)
    print("STEP 3) 각 서비스 노드 실제 실행 — POST /v1/execute")
    print("=" * 72)
    for svc in ("github", "notion", "discord"):
        node = found.get(svc)
        if not node:
            continue
        print(f"\n--- 실행: {svc} ({node['id']}) ---")
        _execute_node(node, svc)


if __name__ == "__main__":
    main()

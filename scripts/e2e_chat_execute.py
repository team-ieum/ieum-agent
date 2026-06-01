#!/usr/bin/env python3
"""E2E 수동 시험: 워크플로우 생성(/v1/chat) → 단일 노드 실행(/v1/execute).

resolver 수정 검증용. Notion 저장 워크플로우를 생성시켜 parent_page_id가
실제로 채워지는지(= resolver가 notion_search를 돌렸는지) 확인하고, 해당
Notion 노드를 직접 실행해 페이지가 생성되는지까지 확인한다.

필요 환경변수:
  GEMINI_API_KEY   : Gemini API 키 (X-LLM-Api-Key)
  NOTION_TOKEN     : Notion Integration Token (X-Notion-Token)
선택:
  BASE_URL         : 기본 http://localhost:8000
  USER_ID          : 기본 e2e-tester
  TARGET_PAGE_NAME : 저장 대상 Notion 페이지/DB 이름 (resolver가 검색할 이름)

사용:
  GEMINI_API_KEY=xxx NOTION_TOKEN=secret_xxx \
  TARGET_PAGE_NAME='내 노트' python scripts/e2e_chat_execute.py
"""
import json
import os
import sys
import urllib.request


BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
USER_ID = os.environ.get("USER_ID", "e2e-tester")
TARGET = os.environ.get("TARGET_PAGE_NAME", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")


def _headers() -> dict:
    h = {
        "Content-Type": "application/json",
        "X-LLM-Provider": "GEMINI",
        "X-LLM-Api-Key": GEMINI_API_KEY,
        "X-User-Id": USER_ID,
    }
    if NOTION_TOKEN:
        h["X-Notion-Token"] = NOTION_TOKEN
    return h


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(body).encode("utf-8"),
        headers=_headers(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"[HTTP {e.code}] {path}\n{e.read().decode('utf-8')}")
        sys.exit(1)


def main() -> None:
    if not GEMINI_API_KEY or not NOTION_TOKEN:
        print("GEMINI_API_KEY, NOTION_TOKEN 환경변수가 필요합니다.")
        sys.exit(2)

    target_clause = f" 저장 위치는 '{TARGET}' 페이지." if TARGET else ""
    gen_prompt = (
        "매뉴얼 트리거로 동작하는 워크플로우를 만들어줘. "
        "내 Notion에 제목 'E2E 테스트', 본문 'resolver 검증용 페이지'를 새 페이지로 저장해."
        + target_clause
    )

    print("=" * 70)
    print("STEP 1) 워크플로우 생성 — POST /v1/chat")
    print("prompt:", gen_prompt)
    print("=" * 70)
    chat = _post("/v1/chat", {"prompt": gen_prompt})
    print("type:", chat.get("type"))
    print("workflowName:", chat.get("workflowName"))
    nodes = chat.get("nodes") or []
    print(json.dumps(nodes, ensure_ascii=False, indent=2))

    # Notion 저장 노드 찾기
    notion_node = None
    for n in nodes:
        cfg = n.get("config", {})
        tools = cfg.get("tools") or []
        names = [t.get("name") if isinstance(t, dict) else t for t in tools]
        if any("notion_create_page" in (nm or "") for nm in names):
            notion_node = n
            break

    if not notion_node:
        print("\n[!] notion_create_page 노드를 찾지 못함. 생성 결과 확인 필요.")
        return

    cfg = notion_node["config"]
    prompt_text = cfg.get("prompt", "")
    print("\n" + "=" * 70)
    print("STEP 2) parent_page_id 해소 여부 점검 (resolver 효과)")
    print("=" * 70)
    print("notion 노드 prompt:", prompt_text)
    has_pid = "parent_page_id" in prompt_text and "''" not in prompt_text.replace('""', "''")
    print("→ prompt에 parent_page_id 값이 채워졌는가? :", "예" if has_pid else "아니오(빈 값/누락)")

    print("\n" + "=" * 70)
    print("STEP 3) 해당 노드 실제 실행 — POST /v1/execute")
    print("=" * 70)
    exec_body = {
        "nodeId": notion_node.get("id", "node-x"),
        "renderedPrompt": prompt_text,
        "systemMessage": cfg.get("systemMessage"),
        "agentType": cfg.get("agentType", "react"),
        "tools": cfg.get("tools") or [],
    }
    result = _post("/v1/execute", exec_body)
    print(json.dumps(result, ensure_ascii=False, indent=2)[:2000])


if __name__ == "__main__":
    main()

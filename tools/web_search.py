import json
import os
import httpx

from common.error_code import ToolErrorCode
from tools.http_client import get_http_client

_TAVILY_URL = "https://api.tavily.com/search"
_TIMEOUT = 15.0
_MAX_RESULTS = 10


async def web_search(
    query: str,
    max_results: int = 5,
) -> str:
    """
    웹 검색 결과를 가져옵니다.

    Args:
        query: 검색어
        max_results: 반환할 최대 검색 결과 수 (1~10)

    Returns:
        title, url, snippet 목록을 포함한 JSON 문자열
    """
    if not query or not query.strip():
        return json.dumps({"error": "검색어는 필수입니다."}, ensure_ascii=False)

    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (web_search: TAVILY_API_KEY 환경변수가 설정되지 않았습니다.)"
        }, ensure_ascii=False)

    max_results = max(1, min(max_results, _MAX_RESULTS))

    payload = {
        "query": query.strip(),
        "max_results": max_results,
        "search_depth": "basic",
        "include_answer": False
    }

    try:
        client = get_http_client()
        response = await client.post(
            _TAVILY_URL,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            },
            json=payload,
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        # Tavily의 결과를 기존 format (title, url, snippet)에 매핑
        tavily_results = data.get("results", [])
        mapped_results = []
        for r in tavily_results:
            mapped_results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", "")
            })

        return json.dumps({
            "success": True,
            "query": query,
            "results": mapped_results,
        }, ensure_ascii=False)

    except httpx.HTTPStatusError as e:
        return json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (web_search: HTTP {e.response.status_code})"
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (web_search: {str(e)})"
        }, ensure_ascii=False)

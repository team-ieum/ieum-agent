import html
import json
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import httpx

from common.error_code import ToolErrorCode

_SEARCH_URL = "https://html.duckduckgo.com/html/"
_TIMEOUT = 15.0
_MAX_RESULTS = 10


class _DuckDuckGoResultParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self._current = None
        self._capture_title = False
        self._capture_snippet = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        class_names = attrs_dict.get("class", "")

        if tag == "a" and "result__a" in class_names:
            self._current = {
                "title": "",
                "url": _normalize_result_url(attrs_dict.get("href", "")),
                "snippet": "",
            }
            self._capture_title = True
            return

        if self._current is not None and "result__snippet" in class_names:
            self._capture_snippet = True

    def handle_data(self, data):
        if self._current is None:
            return

        text = data.strip()
        if not text:
            return

        if self._capture_title:
            self._current["title"] += text + " "
        elif self._capture_snippet:
            self._current["snippet"] += text + " "

    def handle_endtag(self, tag):
        if tag == "a" and self._capture_title:
            self._capture_title = False
            return

        if self._capture_snippet and tag in {"a", "div"}:
            self._capture_snippet = False
            self._append_current()
            return

        if self._current is not None and tag == "div":
            self._append_current()

    def _append_current(self):
        if self._current is None:
            return

        title = " ".join(self._current["title"].split())
        url = self._current["url"]
        snippet = " ".join(self._current["snippet"].split())

        if title and url and not any(result["url"] == url for result in self.results):
            self.results.append({
                "title": html.unescape(title),
                "url": url,
                "snippet": html.unescape(snippet),
            })

        self._current = None
        self._capture_title = False
        self._capture_snippet = False


def _normalize_result_url(url: str) -> str:
    if not url:
        return ""

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if "uddg" in query and query["uddg"]:
        return unquote(query["uddg"][0])

    return url


def _parse_results(html_text: str, max_results: int) -> list[dict[str, str]]:
    parser = _DuckDuckGoResultParser()
    parser.feed(html_text)
    return parser.results[:max_results]


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

    max_results = max(1, min(max_results, _MAX_RESULTS))
    params = urlencode({"q": query.strip()})

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(
                f"{_SEARCH_URL}?{params}",
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; IEUM-Agent/1.0)",
                    "Accept": "text/html",
                },
            )
            response.raise_for_status()

        results = _parse_results(response.text, max_results)
        return json.dumps({
            "success": True,
            "query": query,
            "results": results,
        }, ensure_ascii=False)

    except httpx.HTTPStatusError as e:
        return json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (web_search: HTTP {e.response.status_code})"
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "error": f"{ToolErrorCode.EXECUTION_FAILED.message} (web_search: {str(e)})"
        }, ensure_ascii=False)

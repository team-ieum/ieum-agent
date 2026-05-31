import json
import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from common.error_code import ToolErrorCode
from tools.http_client import get_http_client

_MAX_RESPONSE_BYTES = 1 * 1024 * 1024
_TIMEOUT = 30.0


def _parse_headers(headers_json: str | dict | None) -> tuple[dict[str, str], str | None]:
    if not headers_json:
        return {}, None

    if isinstance(headers_json, dict):
        parsed = headers_json
    else:
        try:
            parsed = json.loads(headers_json)
        except json.JSONDecodeError:
            return {}, "headers_json은 JSON 객체 문자열이어야 합니다."

    if not isinstance(parsed, dict):
        return {}, "headers_json은 JSON 객체 문자열이어야 합니다."

    return {str(key): str(value) for key, value in parsed.items()}, None


def _is_private_host(hostname: str) -> bool:
    # NOTE: DNS 조회 시점과 실제 요청 시점 사이의 TOCTOU 취약점이 존재한다.
    # DNS rebinding 공격의 완전한 방어는 네트워크 레벨(egress firewall)에서 수행해야 한다.
    # 여기서는 best-effort 방어를 제공하며, DNS 실패 시 fail-close(차단) 정책을 적용한다.
    try:
        ip = socket.gethostbyname(hostname)
        addr = ipaddress.ip_address(ip)
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except Exception:
        # DNS 해석 실패 시 차단 (fail-close)
        return True


async def http_fetch(
    url: str,
    method: str = "GET",
    headers_json: str | dict | None = None,
    body: str = None,
) -> str:
    """
    외부 HTTP API에 요청을 보내고 응답을 반환합니다.

    Args:
        url: 요청 URL (https:// 필수)
        method: HTTP 메서드 (GET, POST, PUT, PATCH, DELETE)
        headers_json: 요청 헤더 JSON 객체 문자열 또는 dict (optional, 예: {"Accept":"application/json"})
        body: 요청 본문 문자열 (POST/PUT/PATCH 시 optional)

    Returns:
        statusCode, headers, body를 포함한 JSON 문자열
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return json.dumps({"error": "HTTPS URL만 허용됩니다."}, ensure_ascii=False)

    if _is_private_host(parsed.hostname or ""):
        return json.dumps({"error": "내부 네트워크 주소로의 요청은 허용되지 않습니다."}, ensure_ascii=False)

    _ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
    if method.upper() not in _ALLOWED_METHODS:
        return json.dumps({
            "error": f"지원하지 않는 HTTP 메서드입니다. 허용된 메서드: {', '.join(sorted(_ALLOWED_METHODS))}"
        }, ensure_ascii=False)

    headers, header_error = _parse_headers(headers_json)
    if header_error:
        return json.dumps({"error": header_error}, ensure_ascii=False)

    try:
        client = get_http_client()
        response = await client.request(
            method=method.upper(),
            url=url,
            headers=headers,
            content=body.encode() if body else None,
            follow_redirects=True,
            timeout=_TIMEOUT,
        )

        raw = response.content
        truncated = False
        if len(raw) > _MAX_RESPONSE_BYTES:
            raw = raw[:_MAX_RESPONSE_BYTES]
            truncated = True

        result = {
            "statusCode": response.status_code,
            "headers": dict(response.headers),
            "body": raw.decode("utf-8", errors="replace"),
        }
        if truncated:
            result["truncated"] = True

        return json.dumps(result, ensure_ascii=False)

    except httpx.TimeoutException:
        return json.dumps({"error": f"{ToolErrorCode.EXECUTION_FAILED.message} (http_fetch: 요청 타임아웃)"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"{ToolErrorCode.EXECUTION_FAILED.message} (http_fetch: {str(e)})"}, ensure_ascii=False)

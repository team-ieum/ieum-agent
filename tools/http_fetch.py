import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from common.error_code import ErrorCode

_MAX_RESPONSE_BYTES = 1 * 1024 * 1024
_TIMEOUT = 30.0
_MAX_REDIRECTS = 5


def _is_private_host(hostname: str) -> bool:
    try:
        ip = socket.gethostbyname(hostname)
        addr = ipaddress.ip_address(ip)
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except Exception:
        return False


async def http_fetch(
    url: str,
    method: str,
    headers: dict = None,
    body: str = None,
) -> str:
    """
    외부 HTTP API에 요청을 보내고 응답을 반환합니다.

    Args:
        url: 요청 URL (https:// 필수)
        method: HTTP 메서드 (GET, POST, PUT, PATCH, DELETE)
        headers: 요청 헤더 key-value (optional)
        body: 요청 본문 문자열 (POST/PUT/PATCH 시 optional)

    Returns:
        statusCode, headers, body를 포함한 JSON 문자열
    """
    import json

    parsed = urlparse(url)
    if parsed.scheme != "https":
        return json.dumps({"error": "HTTPS URL만 허용됩니다."}, ensure_ascii=False)

    if _is_private_host(parsed.hostname or ""):
        return json.dumps({"error": "내부 네트워크 주소로의 요청은 허용되지 않습니다."}, ensure_ascii=False)

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
            timeout=_TIMEOUT,
        ) as client:
            response = await client.request(
                method=method.upper(),
                url=url,
                headers=headers or {},
                content=body.encode() if body else None,
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
        return json.dumps({"error": f"{ErrorCode.TOOL_EXECUTION_FAILED.message} (http_fetch: 요청 타임아웃)"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"{ErrorCode.TOOL_EXECUTION_FAILED.message} (http_fetch: {str(e)})"}, ensure_ascii=False)

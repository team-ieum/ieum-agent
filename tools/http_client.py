import httpx

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """공유 가능한 httpx.AsyncClient 인스턴스를 반환하여 커넥션 풀을 활성화합니다."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100)
        )
    return _client


async def close_http_client() -> None:
    """공유 HTTP 클라이언트를 안전하게 종료합니다."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None

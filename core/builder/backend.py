"""대화형 생성이 사용자 대신 BE를 부르는 클라이언트(spec 2026-10-28 §7.3).

위임 토큰은 이 객체에만 들고 도구 인자·state·로그·instruction에 넣지 않는다(도구는 클로저로 이 객체를 잡는다).
경로 조각은 quote(safe=''), 쿼리 값은 params=로 넘긴다. 결과는 예외 없이 {"data": …} 또는 {"error": {code, message, data}}."""
from urllib.parse import quote

import httpx

from core.config import settings
from tools.http_client import get_http_client

# BE 노드 테스트는 AI 노드면 최대 180초 돈다 — 공용 30초로 끊으면 실패로 오판하고 재시도해 쿼터가 두 번 깎인다(spec §5.3)
TEST_TIMEOUT_SECONDS = 190.0
_UNAVAILABLE = {"code": "BACKEND_UNAVAILABLE", "message": "지금은 연동 정보를 조회할 수 없어요. 잠시 후 다시 시도해 주세요."}


class Backend:
    def __init__(self, delegation_token: str | None):
        self._token = delegation_token

    async def _call(self, method: str, parts: list[str], *, params: dict | None = None,
                    json: dict | None = None, timeout: float | None = None) -> dict:
        if not settings.BACKEND_BASE_URL or not self._token:
            return {"error": dict(_UNAVAILABLE)}
        url = settings.BACKEND_BASE_URL.rstrip("/") + "/api/v1/" + "/".join(quote(str(p), safe="") for p in parts)
        try:
            response = await get_http_client().request(
                method, url, params=params, json=json,
                headers={"X-Delegation-Token": self._token},
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT)
        except httpx.HTTPError:
            return {"error": dict(_UNAVAILABLE)}
        try:
            body = response.json()
        except ValueError:
            body = None
        body = body if isinstance(body, dict) else {}
        if response.is_success and body.get("success", True):
            return {"data": body.get("data")}
        return {"error": {"code": body.get("code") or f"HTTP_{response.status_code}",
                          "message": body.get("message") or "요청이 실패했어요.",
                          "data": body.get("data")}}

    async def options(self, source: str, params: dict) -> dict:
        """optionsSource `<app>.<resource>` → GET /integrations/{app}/options/{resource}."""
        app, resource = source.split(".", 1)
        return await self._call("GET", ["integrations", app, "options", resource], params=params)

    async def fields(self, source: str, params: dict) -> dict:
        app, resource = source.split(".", 1)
        return await self._call("GET", ["integrations", app, "fields", resource], params=params)

    async def connections(self) -> dict:
        return await self._call("GET", ["oauth", "connections"])

    async def test_node(self, workflow_id: str, node: dict) -> dict:
        return await self._call("POST", ["workflows", workflow_id, "nodes", node["id"], "test"],
                                json={"node": node}, timeout=TEST_TIMEOUT_SECONDS)

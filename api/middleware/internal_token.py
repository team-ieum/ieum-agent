"""BE→agent 공유 비밀 검사(spec 2026-10-28 §7.4). agent는 X-User-Id·X-User-Role·X-Key-Mode를 그대로 믿으므로
BE가 아닌 호출자를 여기서 끊는다. 값은 요청마다 settings에서 읽는다(테스트가 monkeypatch로 켠다)."""
import hmac

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from core.config import settings

HEADER = "X-Internal-Token"
# 배포 헬스체크(agent-*-server.yml)는 토큰 없이 부른다
_EXEMPT = {("GET", "/health")}


class InternalTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        expected = settings.AGENT_INTERNAL_TOKEN.strip()  # 공백만이면 꺼짐(BE isBlank와 대칭)
        if expected and (request.method, request.url.path) not in _EXEMPT:
            given = request.headers.get(HEADER, "")
            if not hmac.compare_digest(given.encode(), expected.encode()):
                return JSONResponse(status_code=401, content={"detail": "unauthorized"})
        return await call_next(request)

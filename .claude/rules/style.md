---
paths:
  - "**/*.py"
---
# 코드 스타일 (린트·포맷 설정 없음 — 아래가 관례)

- 주석·docstring은 한국어로, "왜"를 적는다. 근거가 spec이면 `spec 2026-11-05 §3`처럼 날짜·절을 단다
- 로깅: 모듈마다 `logger = logging.getLogger(__name__)`, 메시지는 `%` 인자(`logger.warning("… %s", x)`) — f-string·`print` 쓰지 않음
- 외부 HTTP는 `tools/http_client.py` `get_http_client()`의 공유 `httpx.AsyncClient`만 쓴다 — 새 클라이언트를 만들면 커넥션 풀·리다이렉트 상한이 빠진다
- `json.dumps`에는 `ensure_ascii=False`
- 타입 힌트의 컨테이너는 내장 제네릭(`list[str]`, `dict[str, Any]`)
- async 경로에서 동기 블로킹 작업(큰 파싱·정규식·SDK 동기 호출)을 그대로 돌리지 않는다 — uvicorn 단일 워커라 모든 요청이 멈춘다. 필요하면 `asyncio.to_thread`
- HTTP 레이어 오류는 `raise CodedHTTPException(ErrorCode.X)` — 새 code는 `common/error_code.py`에 `NAME = (HTTP 상태, "한국어 메시지")` 추가(응답 `errorCode`로 이름이 나가므로 기존 이름은 바꾸지 않는다, `tests/test_error_code_contract.py`), 도구 결과 JSON용이면 `ToolErrorCode`

# ieum-agent 스타일 가이드

## 프로젝트 개요
IEUM 프로젝트의 AI 노드 실행 전담 마이크로서비스 (Python + FastAPI).
Spring Boot 백엔드에서 HTTP로 위임받아 Google ADK를 통해 에이전트를 실행하고, 결과를 MongoDB에 로깅한다.

## 디렉토리 구조
```
api/routes/        — FastAPI 라우터 (execute, generate, modify, chat)
api/schemas/       — Pydantic 요청/응답 스키마
api/middleware/     — 인증 미들웨어 (credential.py)
core/              — 핵심 비즈니스 로직 (agent.py, workflow_generator.py, workflow_chat.py, node_hydration.py)
tools/             — 빌트인 도구 함수 (slack, discord, gmail, notion, google_*, http_fetch, mcp, utils)
providers/         — LLM Provider 어댑터 (현재 미사용, 레거시/확장용)
common/            — 에러 코드(ErrorCode, ToolErrorCode), 커스텀 예외
db/                — MongoDB 연결(mongodb.py), 컬렉션 스키마 문서(models.py)
tests/             — pytest 테스트 (test_{모듈명}.py 형식)
```

## 라우터 패턴
- 라우터는 `api/routes/` 디렉토리에 기능 단위로 분리한다.
- 각 모듈에서 `router = APIRouter()` 정의 후, `main.py`에서 `app.include_router(router, prefix="/v1")`로 등록한다.
- 인증은 `Depends(get_llm_credentials)`로 헤더에서 provider, api_key, user_id를 추출한다.

## 에러 처리 패턴
- `ErrorCode` Enum: `(status_code: int, message: str)` 쌍. HTTP 응답에 사용.
- `ToolErrorCode` Enum: `message: str`만 보유. Tool은 HTTP 응답이 아닌 JSON 문자열로 에러를 반환하므로 별도 관리.
- 라우터에서 `ValueError` → 502 (LLM 파싱 실패), `Exception` → 500 (일반 오류)로 분기한다.
- Tool 함수는 예외를 throw하지 않고, `{"error": "..."}` JSON 문자열을 반환한다.

## 도구 작성 규칙
- `tools/` 디렉토리에 async 함수 형태로 작성한다.
- 반환값은 항상 `str` (JSON 문자열). 성공 시 `{"success": true, ...}`, 실패 시 `{"error": "..."}`.
- `tools/__init__.py`의 `get_tools_for_request()` 내 `tool_map`에 `FunctionTool(함수)`로 등록한다.
- 도구 키 네이밍: `builtin:` 접두사 (예: `builtin:google_sheets_read`), 외부 서비스는 접두사 없음 (예: `slack`).

## 핵심 실행 패턴 (core/)
- Google ADK `LlmAgent` + `Runner`로 에이전트를 실행한다.
- API 키는 요청마다 `os.environ`에 임시 설정 후 finally 블록에서 복원한다.
- 환경변수 동시 접근 방지를 위해 `core/env_lock.py`의 asyncio Lock을 사용한다.
- LLM 응답 후처리: 마크다운 코드 펜스 제거 → `json.loads` → Pydantic 모델 변환.
- 실행 결과는 MongoDB 컬렉션에 로깅한다 (실패 시 경고 로그만 남기고 무시).

## Pydantic 스키마
- 요청/응답 스키마는 `api/schemas/` 디렉토리에 위치한다.
- 필드명은 camelCase (Spring Boot와의 JSON 호환).
- MongoDB 컬렉션 스키마 문서는 `db/models.py`에 Pydantic BaseModel로 정의한다.

## 커밋 컨벤션
- 형식: `type: 내용` (한국어 작성)
- type 목록: feat, fix, refactor, docs, test, infra
- 예시: `feat: Google Calendar 빌트인 도구 구현`

## 테스트
- 테스트 파일은 `tests/` 디렉토리에 `test_{모듈명}.py` 형식으로 작성한다.
- 라우터 테스트는 `test_{모듈명}_router.py`로 분리한다.
- Google ADK Runner는 mock으로 대체하고, MongoDB 로그 저장 함수도 mock한다.
- `pytest` + `pytest-asyncio` 사용. async 테스트에는 `@pytest.mark.asyncio` 데코레이터를 붙인다.

## 보안
- API 키, Secret을 코드에 하드코딩하지 않는다.
- BYOK(Bring Your Own Key) 구조: 사용자 키는 요청 헤더로 받아 임시로만 사용한다.
- `.env` 파일은 `.gitignore`에 포함되어 있다.
- Google OAuth Access Token은 `X-Google-Access-Token` 헤더로 받아 도구에 주입한다.

## 코드 스타일
- Python 3.12+, type hint 사용 (`str | None` 형식).
- 불필요한 docstring/주석 추가 금지. 로직이 자명한 경우 생략한다.
- import 순서: stdlib → 외부 패키지 → 프로젝트 내부.

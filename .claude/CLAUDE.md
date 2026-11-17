# CLAUDE.md

IEUM Agent — AI 노드 실행·채팅 빌더·정적 검증 서비스. 설계 메모는 `docs/claude/`(로컬 전용, 체크인 안 됨).

# Commands
- 처음 받은 뒤: `git submodule update --init` — `catalog/`가 없으면 기동 시 카탈로그 로드가 실패한다
- 테스트 하나: `pytest tests/test_execute.py::test_name` (기본값 `-m "not smoke"`)
- 실 API 스모크: `set -a; source .env.smoke; set +a; pytest -m smoke` — 키를 명령줄에 인라인으로 넘기지 않는다
- 배포 스크립트 테스트(`pytest`가 안 돈다): `python3 -m unittest discover -s scripts/bluegreen -p 'test_*.py'`
- 린트·타입 검사 설정 없음. CI는 `pytest tests/`만 돈다

# Conventions
- 설정 진실원은 `core/config.py` `Settings`. 비면 기능이 꺼지는 값이 많다(`SELF_HOSTED_LLM_BASE_URL`, `PLATFORM_GEMINI_API_KEY`, `AGENT_INTERNAL_TOKEN`, `OTEL_EXPORTER_OTLP_ENDPOINT`)
- 에러는 `common/error_code.py` `ErrorCode` + `common/exception.py` `CodedHTTPException`
- 모델 라우팅은 `core/provider_config.py` + `core/model_factory.py`가 전부다(`providers/`는 삭제됨)
- 영역별 규칙은 `.claude/rules/` — 해당 파일을 열면 자동으로 로드된다

# Gotchas
- `dev`에 머지되면 곧바로 dev 서버에, `main`에 push되면 Main Blue/Green으로 배포된다
- 테스트도 개발자 `.env`를 읽는다 — 새 설정값이 테스트를 오염시키면 `tests/conftest.py`의 빈 값 목록에 추가
- 레포 간 계약(카탈로그·BE 헤더·검증 에러 code)은 워크스페이스 `../docs/cross-repo-contracts.md`

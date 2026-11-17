---
paths:
  - "tests/**"
  - "scripts/bluegreen/**"
---
# 테스트

- `tests/conftest.py`가 `MONGODB_DB_NAME=ieum_test`를 강제하고 Motor 컬렉션 쓰기를 클래스 단위로 막는다 — 우회하지 않는다(실 Mongo가 오염된 전례)
- 설정값은 `monkeypatch.setattr(settings, ...)`로 테스트마다 켠다. conftest가 비워 둔 값(`SELF_HOSTED_*`, `AGENT_INTERNAL_TOKEN`, `BACKEND_BASE_URL`)이 기본이다
- 실 외부 API를 부르는 테스트에는 `@pytest.mark.smoke`를 붙인다 — 기본 실행·CI에서 빠진다
- cost·usage는 `_completion_cost`를 패치하지 말고 실제 litellm 호출로 검증한다 — 패치 탓에 시그니처 불일치를 놓쳐 cost가 죽은 채 머지된 전례
- 카탈로그 경로는 `tests/catalog_paths.py` 헬퍼를 쓴다
- `scripts/bluegreen/test_*.py`는 unittest라 `pytest`에 안 잡힌다 — 직접 돌린다(`.claude/CLAUDE.md` Commands)

---
paths:
  - ".github/workflows/**"
  - "scripts/bluegreen/**"
  - "Dockerfile"
  - "docs/operations/**"
---
# 배포

- `agent-dev-server.yml`: `dev` push → self-hosted dev 서버. `agent-main-server.yml`: `main` push → Main Blue/Green(수동 실행은 build·stage-bootstrap·deploy)
- Secrets는 워크플로우에 명시적으로 주입한다 — 추가 절차는 `docs/operations/bluegreen-secrets.md`·`main-bluegreen-secrets.md`
- 워크플로우 로그에 요청·토큰·응답·환경변수를 출력하지 않는다
- `scripts/bluegreen/`을 고치면 unittest를 직접 돌린다 — 배포 job에서만 돌기 때문에 PR CI에서는 잡히지 않는다

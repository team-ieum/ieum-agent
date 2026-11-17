# 브랜치·동기화

- `dev`: 통합 브랜치(머지 = dev 배포). `main`: Main 배포용
- 작업 브랜치는 `dev`에서 딴다: `<type>/IEUM-AI-<N>[-slug]` (type: `feat`·`fix`·`refactor`·`chore`·`infra`)
- pull은 `git pull --rebase`
- `dev`·`main`에 직접 push·force push 금지. 작업 브랜치는 `--force-with-lease`만 쓴다
- 서브모듈 `catalog/`는 ieum-node-catalog `main`에 머지된 커밋으로만 올린다

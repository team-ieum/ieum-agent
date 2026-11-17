# PR

- base는 `dev`
- 제목: `[<type>] <한국어 요약> (IEUM-AI-N)` — type은 소문자, 인프라만 `[Infra]`
- 본문(최근 관례가 `.github/pull_request_template.md`보다 우선):
  - `## 변경` — 파일·동작 단위 bullet
  - `## 검증` — `pytest tests -q` N passed 등 실제 결과
  - BE·catalog와 순서가 있으면 `## 머지 순서`
  - 마지막 줄 `IEUM-AI-N`
- 머지는 머지 커밋으로 하고 subject는 PR 제목. `[Infra]` PR만 squash
- push·PR 생성·머지는 단계마다 사용자 확인을 받는다

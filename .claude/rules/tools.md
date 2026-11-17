---
paths:
  - "tools/**"
---
# tools/

- 새 도구: 카탈로그 이벤트 머지 확인 → `async def` + 한국어 docstring·타입 힌트(=파라미터 스펙) → `_TOOL_MAP`(AI 노출, 함수 자체·`FunctionTool` 금지) 또는 `_ACTION_ONLY_TOOLS`(ACTION 전용) 등록 → `tests/test_<서비스>*.py`에서 `get_http_client` patch로 테스트
- AI 노출이냐 ACTION 전용이냐부터 정한다 — 메일 발송·초대·삭제처럼 되돌릴 수 없는 일, 외부 글을 읽는 일은 `tools/registry.py` `_ACTION_ONLY_TOOLS`
- `builtin:google_*` 키 함수는 `access_token`, `builtin:notion_*` 키 함수는 `token` 파라미터가 필수다(`tests/test_base_utils.py`가 전수 검사)
- 출력에 바이트 예산을 둔다(BE 응답 한도). 큰 입력은 파싱하기 전에 크기부터 막는다 — 단일 워커 이벤트 루프가 멈춘다
- 외부 호출은 실행기 한도 안에서 끝나게 마감을 둔다(`tools/gmail.py` 전달 참고)
- 오류 문구에 입력을 실을 때는 `_brief`(앞 80자)

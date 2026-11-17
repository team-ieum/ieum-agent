# 보안 — 우회하지 말 것

- `api/middleware/internal_token.py`: BE가 아닌 호출자를 `X-Internal-Token`(hmac 비교)으로 끊는다 — agent는 `X-User-Id`·`X-User-Role`·`X-Key-Mode`를 그대로 믿으므로 이것이 유일한 경계. 예외는 `GET /health`뿐이며 넓히지 않는다
- 자격증명은 헤더로만 받는다(`api/middleware/credential.py`). 서비스 토큰은 도구 함수 파라미터로 바인딩해 LLM 스키마에서 숨긴다 — LLM이 채우는 인자로 토큰을 받지 않는다
- API 키를 `os.environ`에 넣지 않는다 — 요청별 모델 인스턴스에 주입한다(`core/model_factory.py`)
- `tools/http_fetch.py`의 사설·루프백·링크로컬 주소 차단과 `tools/http_client.py`의 리다이렉트 상한을 유지한다
- `tools/registry.py` `_ACTION_ONLY_TOOLS`: 되돌릴 수 없는 일, 외부 글(메일·폼 본문 — 프롬프트 인젝션)을 다루는 도구는 AI 노드에 싣지 않는다. 사용자 결정이므로 묻지 않고 AI 노출로 옮기지 않는다
- 트레이스는 `TraceConfig(hide_inputs=True, hide_outputs=True)`(`core/telemetry.py`) — 입출력 내용을 span에 싣지 않는다. 응답은 `core/output_validator.py`로 마스킹한다
- 오류 문구에 사용자 입력을 통째로 싣지 않는다 — 앞부분만(`_brief`, 80자)
- 로그에 토큰·API 키·헤더 값을 남기지 않는다(사용자는 user id까지만)
- `.env`·`.env.smoke`는 읽거나 출력하지 않는다
- `scripts/` 운영 스크립트는 읽기 전용, 환경변수 값을 출력하지 않는다
- 주석에 "사용자 결정"이 붙은 동작(`grep -rn '사용자 결정' core tools`)은 묻지 않고 바꾸지 않는다

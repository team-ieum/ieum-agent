# Agent DEV 배포 설정

DEV 배포는 `DEV_COM_` GitHub Secrets로 앱 환경설정을 만들고, 색상별 스냅샷을 보관합니다.
MAIN은 수집 방식이 다르므로 [MAIN 배포 설정](main-bluegreen-secrets.md)을 따릅니다.

## Secrets 등록

접두사를 한 번 제거한 뒤 아래 별칭을 앱 환경변수 이름으로 바꿉니다.

| Secret | 앱 환경변수 | 조건 |
|---|---|---|
| `DEV_COM_AGENT_MONGODB_URL` | `MONGODB_URL` | 필수 |
| `DEV_COM_AGENT_MONGODB_DB_NAME` | `MONGODB_DB_NAME` | 필수 |
| `DEV_COM_AGENT_PORT` | `PORT` | 필수, `8000` |
| `DEV_COM_AGENT_CLAUDE_DEFAULT_MODEL` | `CLAUDE_DEFAULT_MODEL` | 필수 |
| `DEV_COM_AGENT_OPENAI_DEFAULT_MODEL` | `OPENAI_DEFAULT_MODEL` | 필수 |
| `DEV_COM_AGENT_GEMINI_DEFAULT_MODEL` | `GEMINI_DEFAULT_MODEL` | 필수 |
| `DEV_COM_AGENT_NOTION_MCP_URL` | `NOTION_MCP_URL` | 해당 기능 사용 시 |
| `DEV_COM_AGENT_NOTION_MCP_AUTH_TOKEN` | `NOTION_MCP_AUTH_TOKEN` | 해당 기능 사용 시 |

`DEV_COM_MONGODB_URL`처럼 실제 앱 이름을 사용해도 됩니다. 같은 설정의 별칭과 직접 이름을
동시에 등록하면 실패하므로 둘 중 하나만 사용합니다.

다음 설정은 접두사를 제거한 이름으로 앱에 전달됩니다. 내부 인증 토큰은 DEV Backend와
연동할 때 같은 값으로 등록하고, 나머지는 사용하는 기능에 맞게 추가합니다.

| Secret | 용도 |
|---|---|
| `DEV_COM_AGENT_INTERNAL_TOKEN` | Backend와 같은 내부 인증 토큰 |
| `DEV_COM_OTEL_EXPORTER_OTLP_ENDPOINT` | Phoenix 트레이스 수집 주소 |
| `DEV_COM_PLATFORM_GEMINI_API_KEY` | 서비스 공용 Gemini 키 |
| `DEV_COM_TAVILY_API_KEY` | 웹 검색 키 |

내부 토큰은 수집기의 필수 6개에 포함되지 않지만, 비워 두면 Agent의 내부 요청 인증이 꺼집니다.
현재 Backend 연동에는 같은 토큰을 사용합니다.

Notion MCP URL·토큰은 DEV 서비스를 가리켜야 합니다. OTLP 주소도 컨테이너에서 접근 가능한
DEV Phoenix 내부 주소를 사용합니다. 실제 채팅 후 트레이스 수집 여부를 별도로 확인합니다.

## 새 변수와 값 처리

- 일반 앱 설정은 `DEV_COM_NEW_OPTION`으로 등록하면 `NEW_OPTION`으로 자동 수집합니다.
  앱이 해당 변수를 읽어야 기능에 반영됩니다. 새 필수값 검사는 수집기에 별도로 추가합니다.
- `DEV_`, `DEV_BLUE_`, `DEV_GREEN_`, `MAIN_`, `AWS_`, 접두사 없는 이름은 수집하지 않습니다.
- 필수 6개 값은 누락·빈 문자열·공백만 있는 값을 허용하지 않습니다.
- 위 Notion MCP 선택값 2개가 빈 문자열이면 생략합니다. 그 외 새 키의 빈 값은 `KEY=`로 남습니다.
- Secret을 삭제하면 다음 Agent 배포 후보에서 해당 설정을 제거합니다.
- 값은 줄바꿈 없는 문자열이어야 합니다. 공백·따옴표·달러 기호를 임의로 제거하거나 실행하지 않습니다.
- 같은 이름으로 해석되는 대소문자·밑줄 변형도 중복으로 거부합니다.
- 컨테이너·IP·같은 색상의 `BACKEND_BASE_URL`과 배포 제어 변수는 controller가 관리합니다.
  일반 앱 Secrets로 덮어쓰지 않습니다.

## CI 테스트용 MongoDB

DEV 테스트는 서버 러너에서 실행됩니다. 테스트 URI는 러너 호스트에서 접근할 수 있어야 합니다.
앱 컨테이너에서만 해석되는 Docker 이름을 그대로 사용할 수 있는지는 구분해서 확인합니다.

| 우선 Secret | 지원하는 대체 이름 | 용도 |
|---|---|---|
| `DEV_COM_AGENT_TEST_MONGODB_URL` | `DEV_COM_TEST_MONGODB_URL` | 테스트 MongoDB URI |
| `DEV_COM_AGENT_TEST_MONGODB_DB_NAME` | `DEV_COM_TEST_MONGODB_DB_NAME` | 테스트 DB 이름 |

테스트 URI가 없으면 workflow는 `DEV_COM_AGENT_MONGODB_URL` 또는 `DEV_COM_MONGODB_URL`을
대신 사용합니다. 테스트 전용 URI를 등록하면 이 fallback을 사용하지 않습니다.
DB 이름은 workflow 기본값이 `ieum_test`이며, `tests/conftest.py`도 `ieum_test`로 강제합니다.
다른 테스트 DB 이름을 Secret에 등록해도 현재 pytest에서는 그대로 사용되지 않습니다.

테스트 전용 URI·DB 이름은 실행 중인 앱 설정에 포함하지 않습니다.
DEV의 Python 버전 `3.11`과 이미지 이름 `ieum-agent-dev`도 workflow 고정값이며 앱 설정에서 제외합니다.

## 배포와 스냅샷

1. workflow의 배포 단계가 Secrets JSON을 전달합니다.
2. `agent_env.py`가 `DEV_COM_` 항목만 골라 새 비공개 임시 `.env`를 만듭니다.
3. 원본 JSON을 환경에서 제거하고 후보 파일을 controller에 전달합니다.
4. 새 색상의 앱 설정을 전체 교체하고, 임시 후보 파일은 종료 시 정리합니다.

기존 서버 `.env`나 현재 컨테이너의 값으로 누락 설정을 보충하지 않습니다.
색상별 파일은 `/home/kwung/deploy/bluegreen/runtime/{blue|green}/agent.env`에 보관합니다.
Backend·Frontend만 배포할 때는 기존 Agent 설정을 재사용합니다.
이전 배포로 복구할 때는 그 배포의 이미지와 설정을 사용합니다.
Secret 수정만으로 실행 중인 컨테이너나 이전 스냅샷이 바뀌지는 않습니다.

`.env`는 Docker 빌드 입력에서 제외하고, 임시 후보도 소스 폴더 밖에 만듭니다.
DEV의 `toJSON(secrets)`는 실행에서 접근 가능한 전체 Secrets를 단계에 전달합니다.
`DEV_COM_` 필터는 앱으로 넘길 값을 제한하며, 러너가 원본을 받는 범위까지 줄이지는 않습니다.
실제 값은 로그·Git·아티팩트에 올리지 않습니다. 서버 `.env`와 Docker 환경은 평문 설정이므로
서버 및 Docker 관리자 접근 권한으로 보호합니다.

## 검증과 운영 절차

수집기·배포 스크립트 단위 테스트는 합성 값과 임시 파일을 사용합니다.

```bash
python3 -m unittest discover -s scripts/bluegreen -p 'test_*.py'
```

이 명령의 성공은 실제 DB 인증이나 외부 연동 성공을 의미하지 않습니다.
설치·전환·복구 순서는 [인프라 DEV 운영 절차](https://github.com/team-ieum/ieum-infra/blob/dev/docs/bluegreen-rollout.md)를 따릅니다.

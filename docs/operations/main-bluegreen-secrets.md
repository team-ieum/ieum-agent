# Agent MAIN 배포 설정

MAIN은 `https://kiacms.co.kr` 환경을 사용하며, DEV와 컨테이너·데이터·배포 상태를 분리합니다.
필요한 Secrets만 명시적으로 전달하고, Blue·Green별 `.env` 스냅샷을 보관합니다.

## Secrets 등록

### 빌드용

| Secret | 조건 |
|---|---|
| `MAIN_COM_AGENT_PYTHON_VERSION` | Python 3.11 계열 |
| `MAIN_COM_AGENT_IMAGE_NAME` | MAIN 전용 이미지명. 예: `ieum-agent-main` |

빌드용 값은 앱 `.env`에 넣지 않습니다.

### 앱 필수값

| Secret | 앱 환경변수 |
|---|---|
| `MAIN_COM_AGENT_MONGODB_URL` | `MONGODB_URL` |
| `MAIN_COM_AGENT_MONGODB_DB_NAME` | `MONGODB_DB_NAME` |
| `MAIN_COM_AGENT_PORT` | `PORT` — `8000` 고정 |
| `MAIN_COM_AGENT_CLAUDE_DEFAULT_MODEL` | `CLAUDE_DEFAULT_MODEL` |
| `MAIN_COM_AGENT_OPENAI_DEFAULT_MODEL` | `OPENAI_DEFAULT_MODEL` |
| `MAIN_COM_AGENT_GEMINI_DEFAULT_MODEL` | `GEMINI_DEFAULT_MODEL` |
| `MAIN_COM_AGENT_INTERNAL_TOKEN` | `AGENT_INTERNAL_TOKEN` |

MongoDB는 MAIN 전용 DB를 가리켜야 합니다. 내부 토큰은 MAIN Backend와 같게, DEV와는 다르게 설정합니다.
`MAIN_COM_MONGODB_URL`처럼 앱 이름을 직접 써도 됩니다. 같은 설정의 별칭과 직접 이름을
동시에 등록하면 충돌하므로 둘 중 하나만 사용합니다.

### 선택값

| Secret | 용도 |
|---|---|
| `MAIN_COM_AGENT_NOTION_MCP_URL` | MAIN Notion MCP 주소 |
| `MAIN_COM_AGENT_NOTION_MCP_AUTH_TOKEN` | MAIN MCP 서비스와 같은 인증 토큰 |
| `MAIN_COM_OTEL_EXPORTER_OTLP_ENDPOINT` | MAIN Phoenix 트레이스 수집 주소 |
| `MAIN_COM_PLATFORM_GEMINI_API_KEY` | 서비스 공용 Gemini 키 |
| `MAIN_COM_TAVILY_API_KEY` | 웹 검색 키 |
| `MAIN_COM_SELF_HOSTED_LLM_BASE_URL` | 자체 LLM 주소 |
| `MAIN_COM_SELF_HOSTED_LLM_MODEL` | 자체 LLM 모델 |
| `MAIN_COM_SELF_HOSTED_LLM_API_KEY` | 자체 LLM 인증 키 |
| `MAIN_COM_SELF_HOSTED_ALLOWED_ROLES` | 허용 역할 JSON 배열 |
| `MAIN_COM_SUPPORTED_PROVIDERS` | 공급자 JSON 배열 |
| `MAIN_COM_AGENT_MAX_LLM_CALLS` | 도구 호출 루프 상한 |
| `MAIN_COM_AGENT_TIMEOUT_SECONDS` | 실행 시간 상한(초) |

Notion MCP 2개는 `AGENT_`를 뺀 직접 이름도 지원합니다. OTLP 주소는
`http://ieum-main-phoenix:6006/v1/traces`처럼 MAIN 컨테이너 내부 주소를 사용합니다.
해당 서비스 준비 후 실제 호출과 트레이스 수집 여부를 확인합니다.

## 새 변수와 값 처리

- MAIN은 Secrets 등록만으로 새 변수가 자동 추가되지 않습니다.
- 새 키는 `agent_env.py`의 `MAIN_RUNTIME_KEYS`와 MAIN workflow의 deploy·stage-bootstrap
  두 단계 `env`에 함께 추가합니다. 앱의 변수 처리와 필수값 검사도 필요에 따라 수정합니다.
- `MAIN_COM_`을 제거하고 기존 별칭을 적용합니다. DEV·AWS·이전 서버 `.env`에서 값을 보충하지 않습니다.
- 미등록·빈 선택값은 제외해 앱 기본값을 사용합니다. 필수값의 누락·빈 값·공백만 있는 값은 거부합니다.
- 값은 줄바꿈 없는 문자열입니다. 공백·달러 기호·따옴표·등호를 자동으로 제거하거나 실행하지 않습니다.
- 컨테이너·IP·배포 제어 설정과 같은 색상의 `BACKEND_BASE_URL`은 controller가 관리합니다.

## 실행 작업과 허용 설정

| 작업 | 동작 | 서버 실행 조건 |
|---|---|---|
| `build` | 테스트·이미지 빌드·검증·업로드 | 서버 작업 없음 |
| `stage-bootstrap` | 최초 배포용 이미지·설정 보관 | `main` + `MAIN_BOOTSTRAP_ENABLED=true` |
| `deploy` | MAIN Blue/Green 배포 | `main` + `MAIN_DEPLOY_ENABLED=true` |

수동 실행 기본값은 `build`입니다. `main` push는 빌드 후 배포 허용 설정이 켜져 있을 때 배포합니다.
작업 브랜치 `infra/IEUM-AI-111`는 수동 `build`만 허용합니다.
`stage-bootstrap`은 앱을 시작하거나 트래픽을 바꾸지 않습니다.

Repository Variables에는 위 허용 설정과 설치한 파일의 `MAIN_BLUEGREEN_CONTROLLER_SHA256`,
`MAIN_BLUEGREEN_HELPER_SHA256`을 등록합니다. 검증값은 실제 설치본과 일치해야 합니다.
서버 러너는 group `ieum_main_server`, labels `self-hosted, linux, x64, ieum-agent, main, deploy`를 사용합니다.

## 빌드·배포 및 스냅샷

1. GitHub `ubuntu-latest`에서 테스트와 이미지 빌드를 수행합니다.
2. 검증한 이미지 아카이브만 업로드합니다. 앱 Secrets와 `.env`는 포함하지 않습니다.
3. 서버 단계가 같은 실행의 결과물을 받아 해시·이미지 정보를 확인합니다.
4. 명시한 MAIN Secrets로 새 비공개 `.env`를 만들고 MAIN controller에 전달합니다.
5. controller가 색상별 설정을 보관하고, 임시 후보 파일은 종료 시 정리합니다.

controller 경로는 `/home/kwung/deploy/bluegreen-main/bluegreen.py`이며 MAIN 환경을 명시해 호출합니다.
색상별 설정은 `/home/kwung/deploy/bluegreen-main/runtime/{blue|green}/agent.env`에 보관합니다.
이전 배포로 복구할 때는 당시 이미지와 설정을 사용합니다. Secret 수정만으로 현재 컨테이너나
이전 스냅샷이 바뀌지는 않습니다. 실제 값은 Git·로그·아티팩트에 올리지 않습니다.
서버 `.env`와 Docker 환경은 평문 설정이므로 서버 및 Docker 관리자 접근 권한으로 보호합니다.

## 검증과 최초 운영 절차

MAIN CI는 GitHub 러너의 임시 MongoDB와 `ieum_test`를 사용합니다. DEV 테스트용 Secrets나
운영 MongoDB 연결값을 사용하지 않으며, `.env` 자동 로딩도 끕니다.

```bash
python3 -m unittest discover -s scripts/bluegreen -p 'test_*.py'
```

이 단위 테스트는 합성 값과 모의 실행을 사용합니다. 실제 DB·내부 인증·외부 연동은 별도 확인합니다.
세 앱의 최초 기동·전환·복구 순서는 [인프라 MAIN 운영 절차](https://github.com/team-ieum/ieum-infra/blob/infra/IEUM-SRV-31/docs/main-bluegreen-rollout.md)를 따릅니다.

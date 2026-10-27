# Agent 개발 배포의 DEV_ Secrets

이 문서는 2026-10-28 로컬 변경 기준이다. GitHub Secrets 등록·조회, 서버 적용, 커밋·push는 수행하지 않았다.

## 설정 원본과 자동 수집

Agent 저장소의 개발 배포는 **DEV_로 시작하는 개별 GitHub Actions Secrets**를 원본으로 사용한다.
접두사를 한 번 제거하고 기존 Agent 변수명 별칭을 적용한다. 일반 새 앱 설정은 별도 workflow 매핑이나
허용 목록 변경 없이 수집한다. 앱이 그 설정을 실제로 읽어야 기능에 반영된다.

```text
DEV_AGENT_MONGODB_URL        → MONGODB_URL
DEV_AGENT_CLAUDE_DEFAULT_MODEL → CLAUDE_DEFAULT_MODEL
DEV_NEW_OPTION               → NEW_OPTION
```

직접 `DEV_MONGODB_URL`처럼 실제 앱 이름에 접두사를 붙여도 된다. 별칭과 직접 이름을 동시에 등록하면
충돌로 거부한다. 대소문자와 밑줄을 무시한 등가 이름도 중복으로 취급한다. `MAIN_`, `DEVELOPMENT_`,
`DEVX_`, 접두사 없는 기존 Secrets는 앱 후보에 포함하지 않는다. 기존 이름을 자동 복사·삭제하지 않았다.

## 기존 이름 매핑과 필수값

| GitHub Secret 이름 | 앱 환경변수 | 정책 |
|---|---|---|
| `DEV_AGENT_MONGODB_URL` | `MONGODB_URL` | 필수 |
| `DEV_AGENT_MONGODB_DB_NAME` | `MONGODB_DB_NAME` | 필수 |
| `DEV_AGENT_PORT` | `PORT` | 필수, `8000` |
| `DEV_AGENT_CLAUDE_DEFAULT_MODEL` | `CLAUDE_DEFAULT_MODEL` | 필수 |
| `DEV_AGENT_OPENAI_DEFAULT_MODEL` | `OPENAI_DEFAULT_MODEL` | 필수 |
| `DEV_AGENT_GEMINI_DEFAULT_MODEL` | `GEMINI_DEFAULT_MODEL` | 필수 |
| `DEV_AGENT_NOTION_MCP_URL` | `NOTION_MCP_URL` | 선택 |
| `DEV_AGENT_NOTION_MCP_AUTH_TOKEN` | `NOTION_MCP_AUTH_TOKEN` | 선택 |

필수 6개는 기존 workflow 계약을 유지하며 누락·빈 문자열·공백만 있는 값이면 실패한다.
기존 선택 2개가 정확한 빈 문자열이면 후보에서 생략해 앱 기본값을 사용한다.
새 키의 빈 문자열은 `KEY=`로 보존한다. Secret을 삭제하면 다음 Agent 후보에서 그 키를 제거한다.
새 앱 필수 설정을 배포 전에 차단해야 한다면 필수 검증 목록도 별도로 갱신한다.

controller는 Agent를 8000 포트로 호출하고 Docker 실행 명령도 PORT를 사용하므로 `PORT=8000`을
검증한다. 다른 포트나 `8000 --reload` 같은 추가 인자는 허용하지 않는다.

## CI 테스트용 MongoDB 설정

pytest 단계는 다음 DEV 이름을 명시적으로 읽는다. 오른쪽 직접 이름도 대체 표기로 지원한다.

| 우선 Secret | 대체 이름 | 사용 목적 |
|---|---|---|
| `DEV_AGENT_TEST_MONGODB_URL` | `DEV_TEST_MONGODB_URL` | 테스트 DB 연결 |
| `DEV_AGENT_TEST_MONGODB_DB_NAME` | `DEV_TEST_MONGODB_DB_NAME` | 테스트 DB 이름 |
| `DEV_AGENT_MONGODB_URL` | `DEV_MONGODB_URL` | 기존 테스트 URL fallback |

테스트 URL이 없으면 기존처럼 앱 MongoDB URL을 사용하고, 테스트 DB 이름은 `ieum_test`를 기본값으로
사용한다. 테스트용 별도 URI·DB를 준비해 두는 것이 적절하다. 위 테스트 전용 URL·DB 이름은 Agent
runtime 후보에서 대소문자·밑줄 변형까지 제외한다. 고정 CI 값인 `PYTHON_VERSION`, `IMAGE_NAME`도 runtime 설정으로 복사하지
않는다. Python 3.11과 이미지명 `ieum-agent-dev`는 기존 workflow의 고정값을 유지한다.

로컬 검증은 합성 값만 사용한다. 운영·개발 DB에 실제 pytest를 실행한 결과로 표현하지 않는다.

## 배포 흐름과 색상별 스냅샷

1. 마지막 배포 단계에서만 `IEUM_AGENT_SECRETS_JSON: ${{ toJSON(secrets) }}`로 Secrets JSON을 받는다.
2. `scripts/bluegreen/agent_env.py`가 DEV_ 항목만 선택·검증하고 새 빈 0600 임시 후보에 기록한다.
   이전 후보나 서버 원본 `.env`를 합치거나 append하지 않는다.
3. shell은 JSON 원본을 unset한 뒤 후보 경로만 controller에 전달한다.
4. `deploy --service agent --env-file 후보 --replace-env`로 새 색상의 사용자 설정을 전체 교체한다.
5. 종료·실패·INT·TERM에서 임시 후보를 삭제한다. SIGKILL·호스트 장애의 trap 실행은 보장하지 않는다.

색상별 `/home/kwung/deploy/bluegreen/runtime/{blue|green}/agent.env`는 유지한다. controller가 관리하는
`TINI_KILL_PROCESS_GROUP=1`도 유지한다. Backend·Frontend만 배포할 때 Agent는 기존 배포 설정을
재사용하고, retained 색상으로 rollback할 때는 당시 이미지·컨테이너·설정을 사용한다.
Secret 수정만으로 실행 중인 환경이나 active·retained 스냅샷을 일괄 덮어쓰지 않는다.

이번 `.dockerignore`는 `.env`와 `.env.*`를 하위 경로까지 Docker build context에서 제외한다.
Agent 앱이 load_dotenv와 BaseSettings의 `.env` 읽기를 지원하더라도, 이미지에 예전 파일이 들어가
삭제한 설정을 되살리지 않도록 한다. private 후보는 checkout 밖 RUNNER_TEMP에 생성한다.
서버 원본 `.env`, 접두사 없는 이전 Secrets, 기존 runtime으로 필수값을 보충하지 않는다.

## 검증과 운영 예약 설정

- 입력은 UTF-8 JSON object여야 하며 중복 이름, NaN/Infinity, 잘못된 JSON·과도한 중첩을 거부한다.
- 선택한 원문 이름과 접두사 뒤 이름은 ASCII 환경변수 형식이어야 하고 값은 문자열이어야 한다.
- 대소문자·별칭·밑줄 변형 충돌은 선택 빈값 생략 전에 거부한다. 결과는 키 이름순으로 기록한다.
- 값은 단일 행 literal이다. NUL·LF/CR·VT/FF·FS/GS/RS·NEL·U+2028/U+2029를 거부하고,
  `$()`·backtick·따옴표·공백·등호를 실행·확장·자동 제거하지 않는다.
- 원본 전체 JSON은96KiB, 최종 앱 후보는48KiB로 제한한다. 비선택 Secrets도 원본 한도에 포함된다.
  OS/runner 프로세스 생성 한도로 Python 실행 전에 실패하면 도구의 고정 오류까지 보장하지 않는다.
- 파일은 배포 계정 소유의 기존 빈 regular file, 권한0600, hardlink1이어야 한다.
  symlink·공개 파일·다른 소유자·기존 내용이 있는 파일은 덮어쓰지 않는다.
- TINI의 프로세스 그룹 설정·deployment namespace·자동 GitHub 토큰·JSON 전달 변수,
  PATH·Python/shell/loader 실행 bootstrap 설정은 예약 키로 거부한다. 전체 목록은 helper 원문을 따른다.

예약 목록은 알려진 실행 경계를 보호하는 정책이며, 모든 앱 설정의 의미를 증명하는 sandbox는 아니다.
실제 MongoDB·외부 서비스 인증은 적용 전 별도 health·기능 검증이 필요하다.

## 보안상 범위

`toJSON(secrets)`는 해당 실행이 접근 가능한 전체 Secrets를 runner 단계에 일시 전달한다.
DEV_ 필터는 **앱 전달 범위**를 줄이며 runner 원본 접근 범위를 제한하는 기능은 아니다.
원문 JSON·값은 shell 코드·argv·GITHUB_ENV·빌드 입력·로그·업로드에 복사하지 않는다.
private 후보·색상 스냅샷·Docker 환경에는 평문 값이 존재하며 Docker 관리자는 이를 조회할 수 있다.
workflow job에는 environment 지정이 없으므로 repository 또는 접근이 허용된 organization Secrets를 쓴다.
repository·organization Secrets는 run queue 시점 값이므로 변경 후 새 run을 시작한다.

## 로컬 설정 경계 검증

```bash
python3 -m unittest discover -s scripts/bluegreen -p 'test_*.py'
```

합성 JSON·가짜 controller와 임시 파일만 사용한다. 실제 Docker·DB·GitHub Secrets·운영 서버에는 접근하지
않는다. workflow Test 단계에서도 이 검증을 pytest 전에 실행한다. Main/AWS workflow는 변경하지 않았다.

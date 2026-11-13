# Main 블루그린 배포 설정

이 문서는 운영 도메인 `https://kiacms.co.kr`의 로컬 구현 계약이다. DEV 도메인
`https://green.kiacms.co.kr`과 데이터·컨트롤러 상태·앱 컨테이너를 분리한다.
현재 변경만으로 서버가 설치되거나 배포되지는 않는다.

## 실행 조건

- Main workflow는 `main` push 또는 수동 실행에서만 선택된다.
- build는 배포 flag 없이 main에서 실행한다. 작업 브랜치 `infra/IEUM-AI-111`는
  수동 `operation=build`일 때만 GitHub 빌드를 허용하며 main 배포와 concurrency를 분리한다.
- 서버의 deploy·stage-bootstrap job은 계속 `github.ref == 'refs/heads/main'`을 요구한다.
- 수동 operation은 `build`(기본), `stage-bootstrap`, `deploy`다. 기본 수동 실행은 배포하지 않는다.
- 실제 deploy job은 `MAIN_DEPLOY_ENABLED == 'true'`이며 push 또는 명시적 deploy일 때만 실행한다.
- bootstrap 입력 저장 job은 수동 stage-bootstrap과 `MAIN_BOOTSTRAP_ENABLED == 'true'`일 때만 실행한다.
- build/test/image 생성은 `ubuntu-latest`에서 수행한다. 운영 DB나 DEV DB를 테스트에 사용하지 않는다.
- 배포 job은 group `ieum_main_server`, labels
  `[self-hosted, linux, x64, ieum-agent, main, deploy]`인 러너를 요구한다.
- 배포 job은 `IEUM_DEPLOY_ENVIRONMENT=main`으로
  `/home/kwung/deploy/bluegreen-main/bluegreen.py`를 호출한다.
- Nginx helper는 `/usr/local/sbin/ieum-main-bluegreen-nginx`이다.
- Repository Variables `MAIN_BLUEGREEN_CONTROLLER_SHA256`와
  `MAIN_BLUEGREEN_HELPER_SHA256`를 실제 승인·설치한 파일의 SHA-256으로 등록한다.
  DEV의 SHA 변수·제어 파일·잠금 파일·상태 파일을 덮어쓰지 않는다.

## 이미지 전달과 설정 경계

build job은 앱 Secrets 전체 JSON을 받지 않는다. 빌드 메타데이터 두 개만 읽는다.
테스트 후 커밋 SHA 태그의 이미지를 생성한다. GitHub 러너에서 `docker save` 아카이브를
검증·재로드하는 검사까지 통과하면 아카이브 한 파일만 업로드한다. 이 검사는 서버에 접속하지 않는다.
이미지 이름에는 `main` 구분자가 있어야 하고 `dev` 구분자는 허용하지 않는다.
`latest` 같은 mutable tag로 전달하지 않는다.

배포 job은 같은 run의 정확한 artifact ID로 다운로드하고, build job이 기록한 아카이브
SHA-256과 단일 MAIN 태그·linux/amd64 플랫폼을 확인한 뒤 Docker에 로드한다.
classic Docker의 config ID와 containerd의 manifest/index ID를 구분해 참조 연결을 검증한다.
저장 방식 변환으로 ID가 달라지면 실제 로드한 이미지를 확인하고, 필요한 경우 임시 tar로
다시 저장해 config 해시가 같은지 검증한 뒤 정리한다. deploy와 stage-bootstrap은 모두
검증 단계가 출력한 서버의 실제 `sha256:...` image ID를 컨트롤러에 전달한다.
앱 환경설정 파일·Secrets JSON은 artifact에 포함하지 않는다.

앱 Secrets는 배포 직전에만 `toJSON(secrets)`로 받고, 수집기가 `MAIN_COM_`만 선택한다.
권한 0600인 새 후보 파일에 기록한 후 원본 JSON을 unset하고 컨트롤러를 호출한다.
정상 종료·처리 가능한 실패·INT·TERM에서 후보 파일을 정리한다. SIGKILL·호스트 장애에서
trap 실행은 보장되지 않으므로 운영자 점검이 필요하다.

`toJSON(secrets)`는 runner에 전달되는 원본 Secrets의 범위를 줄이지 않는다. 필터는 앱에
전달할 항목을 제한한다. 원문·값·파일 내용을 로그나 artifact로 출력하지 않는다.
Docker 관리자 및 서버 snapshot 파일에는 평문 설정 접근이 가능하다.

## 수집 규칙

- CLI: `python3 scripts/bluegreen/agent_env.py PRIVATE_EMPTY_FILE --environment main`
- `--environment`를 생략하면 기존 `dev` 동작이다.
- Main은 `MAIN_COM_` 접두사를 한 번만 제거한다. `DEV_COM_`, `DEV_`, `AWS_`,
  접두사 없는 값, `MAIN_BLUE_`, `MAIN_GREEN_`을 앱 설정 fallback으로 사용하지 않는다.
- 기존 별칭은 Main에서도 유지한다. 별칭과 직접 이름을 동시에 등록하면 충돌이다.
- 입력은 단일 행 literal이다. 공백·달러·따옴표·등호는 자동 확장하지 않는다.
- 후보는 기존 서버 .env 또는 현재 컨테이너의 설정과 합치지 않고 전체 교체한다.
- 새 일반 앱 키는 자동 수집하지만 앱이 읽어야 하며, 새 필수 검증 목록은 별도 수정해야 한다.
- Blue·Green의 컨테이너·내부 주소·배포 제어 설정은 Main 컨트롤러 소유다.
  이를 일반 앱 Secrets로 덮어쓰는 예약 이름과 동등한 underscore 변형을 거부한다.
- Main Blue·Green은 Main 공통 설정을 배포 시 읽지만 각 색상 snapshot을 독립 보존한다.
  retained release rollback은 당시 이미지와 설정을 사용한다.
- Secret 수정만으로 실행 중인 컨테이너나 retained snapshot이 바뀌지 않는다.

## 초기 운영 적용

1. Main 전용 앱망·데이터망, DB·Redis·MongoDB 및 컨트롤러·helper를 준비한다.
   DEV 서비스의 데이터나 상태를 그대로 공유하지 않는다. 실제 설치 SHA를 Main Variables에
   등록하고 Main 러너 접근·라벨을 staging 전에 확인한다.
2. main의 build 또는 수동 기본 build로 검증된 최초 이미지 artifact와 job summary의 identity를 확보한다.
   `MAIN_BOOTSTRAP_ENABLED=true`를 잠시 허용하고 각 앱에서 stage-bootstrap을 명시 실행하면
   검증한 이미지와 MAIN_COM_ 후보 설정을 Main 컨트롤러의 bootstrap 저장소에 보관한다.
   이 단계는 컨테이너 시작·트래픽 전환을 수행하지 않으며 활성 Main이 있으면 거부된다.
   앱 설정 파일은 artifact에 올리지 않으며 GitHub API에서 Secret 평문을 다시 조회하지 않는다.
3. 세 앱의 bootstrap manifest·이미지·설정을 함께 검증한 후 init/prepare/switch로 첫 활성 환경을 만든다.
   staging이 끝나면 `MAIN_BOOTSTRAP_ENABLED`를 끈다.
   앱 workflow가 독자적으로 DB를 생성하거나 migration을 실행하지 않는다.
4. 도메인/TLS, OAuth callback, DB·Redis·MongoDB 연결, 내부 토큰과 같은 색상의 호출 경로를 확인한다.
5. 운영자가 준비 완료를 확인한 뒤에만 `MAIN_DEPLOY_ENABLED=true`를 설정한다.

관리 flag를 켜거나 이 workflow를 머지하는 작업은 서버 bootstrap 완료의 증거가 아니다.
실제 drain·트래픽 전환·rollback과 진행 중 요청 처리는 별도로 검증한다.

## 로컬 검증

```bash
python3 -m unittest discover -s scripts/bluegreen -p 'test_*.py'
```

테스트는 합성 설정·가짜 Docker·가짜 컨트롤러와 임시 파일을 사용한다. 실제 Secrets,
서버·Docker daemon·운영 DB에 접속하는 테스트로 표현하지 않는다.

## Agent Main Secrets

빌드 전용이며 runtime 후보에서 제외한다.

| Secret | 조건 |
|---|---|
| `MAIN_COM_AGENT_PYTHON_VERSION` | Python 3.11 계열. 이미지도 기존 Python 3.11 launcher 사용 |
| `MAIN_COM_AGENT_IMAGE_NAME` | Main 구분자가 있는 별도 이미지명, 예: ieum-agent-main |

앱 필수값은 기존 6개와 운영 내부 토큰이다.

| Secret | 앱 환경변수 |
|---|---|
| MAIN_COM_AGENT_MONGODB_URL | MONGODB_URL |
| MAIN_COM_AGENT_MONGODB_DB_NAME | MONGODB_DB_NAME |
| MAIN_COM_AGENT_PORT | PORT (8000) |
| MAIN_COM_AGENT_CLAUDE_DEFAULT_MODEL | CLAUDE_DEFAULT_MODEL |
| MAIN_COM_AGENT_OPENAI_DEFAULT_MODEL | OPENAI_DEFAULT_MODEL |
| MAIN_COM_AGENT_GEMINI_DEFAULT_MODEL | GEMINI_DEFAULT_MODEL |
| MAIN_COM_AGENT_INTERNAL_TOKEN | AGENT_INTERNAL_TOKEN |

Main 내부 토큰은 비어 있거나 공백만 있으면 거부한다. Main Backend와 같은 값을 쓰며 DEV와 분리한다.
`MAIN_COM_AGENT_NOTION_MCP_URL`, `MAIN_COM_AGENT_NOTION_MCP_AUTH_TOKEN`은 사용하는 기능에 맞게
등록한다. Main MCP의 gateway token과 일치해야 한다. `MAIN_COM_OTEL_EXPORTER_OTLP_ENDPOINT`는
Main Phoenix의 Docker 내부 `/v1/traces` 주소를 사용한다. 실제 대화 수집은 별도 확인한다.

`MAIN_COM_PLATFORM_GEMINI_API_KEY`, `MAIN_COM_TAVILY_API_KEY`, 자체 LLM 등 실제 사용하는
앱 키도 같은 접두사로 수집할 수 있다. `BACKEND_BASE_URL`은 컨트롤러가 같은 색상의 Main
Backend로 주입한다. 테스트용 MongoDB 키는 runtime 후보에서 제외한다.

Main CI는 임시 MongoDB 서비스와 `ieum_test`만 사용하며 운영 MongoDB Secret으로 fallback하지 않는다.

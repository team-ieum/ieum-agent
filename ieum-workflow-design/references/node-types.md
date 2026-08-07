# Node Types & Specification

### TRIGGER
- 모든 워크플로우는 반드시 단 1개의 TRIGGER 노드로 시작합니다.
- `config.triggerType`은 "SCHEDULE", "MANUAL", "WEBHOOK" 중 하나여야 합니다.
- `config.triggerType`이 "SCHEDULE"인 경우, 표준 5자리 크론 표현식(`cron` 필드)을 반드시 설정합니다. (예: "0 9 * * *")

### AI
- AI 추론 및 특정 외부 도구(Slack, Notion 등)를 연동해 동작을 위임합니다.
- `config.llmProvider`는 요청자 provider를 그대로 계승합니다. (CLAUDE | OPENAI | GEMINI)
- 도구 사용이 필요한 경우 `config.agentType`은 "react"로 설정하고, 단순 추론인 경우 "simple"로 설정합니다.
- `config.credentialId`는 반드시 빈 문자열("")로 설정합니다. (Spring Boot 백엔드에서 런타임 주입)
- **데이터 보존·출력 최소화**: 외부 데이터를 수집하는 조회 노드(예: 깃허브 PR 조회, 노션 페이지 조회 등)는 값을 임의로 요약/왜곡하지 않으면서도, 후속 노드가 실제 사용하는 필드만 추출해 `output`으로 출력하도록 prompt를 설계해야 합니다. 전체 원시 JSON 통째 덤프는 금지합니다(거대 출력은 LLM 토큰 생성 지연으로 실행 타임아웃 유발). 가능하면 조회 단계 server-side 필터(state/날짜 범위/개수 제한)를 적용하고, 후속 노드의 필터 조건이 명확하면 조회 노드 단계로 끌어와 추출 필드와 함께 명시합니다.
- **목록 조회 페이지 상한 필수**: 목록 조회 노드(깃허브 PR/이슈, 노션 검색 등)는 반드시 페이지/개수 상한을 명시합니다. 날짜 기반 필터(예: "최근 7일")만으로는 API가 전체 목록을 페이지마다 순회하다 타임아웃되므로, 단일 페이지·최대 건수를 prompt에 못박고 날짜 필터는 그 결과에 적용해야 합니다. (예: "team-ieum/ieum-backend의 PR을 최신순 1페이지(per_page=30, page=1, sort=created, direction=desc)만 조회하고, 그 중 최근 7일 내 생성된 것만 number, title, html_url, created_at 필드만 JSON 배열로 반환")
- **가공 위임**: 데이터 요약, 날짜 포맷팅, JSON 파싱 등 데이터 변환 작업이 필요할 때는, 조회 노드에서 직접 가공하지 말고 별도 TRANSFORM 노드를 쓰거나 별도의 AI 노드(agentType: "react")에 prompt로 가공 업무를 명시적으로 위임합니다. (실행 시 가공용 서브 에이전트가 자동 처리됩니다.)
- **tools 작성 규칙**: AI 노드의 `tools`에는 빌트인 도구 키(`slack`, `discord`, `gmail`, `builtin:...`)만 넣습니다. 서브 에이전트 이름(`transform_agent`, `web_agent`, `github_agent` 등)이나 GitHub 도구명(`github_list_pull_requests` 등)은 **절대 `tools`에 넣지 마십시오.** 이들은 실행 시 자동 부착되므로 prompt에 자연어로만 지시하고 `tools`는 비워 둡니다. (예: GitHub PR 조회 노드는 `tools: []`)

### HTTP
- Notion, Google, Slack, Discord, GitHub 등 내장 도구가 지원되는 서비스 연동 시에는 **절대로 HTTP 노드를 사용해선 안 됩니다.**
- 오직 템플릿/내장 도구에 없는 임의 외부 REST API를 호출할 때만 HTTP 노드를 설계합니다.
- `config.method` ("GET", "POST", "PUT", "DELETE")와 `config.url`이 필수입니다.

### CONDITION
- 흐름 분기를 수행합니다.
- `config.operator`, `config.left` (참조 형식), `config.right` 가 필수입니다.
- 분기 경로는 edges에서 `conditionType: "true"` 또는 `"false"`로 처리합니다.

### TRANSFORM
- 데이터를 정제하거나 텍스트 포맷팅을 수행합니다.
- `config.mappings` 가 필수이며 dict 형식이어야 합니다.

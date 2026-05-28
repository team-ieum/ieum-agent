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

### HTTP
- Notion, Google, Slack, Discord, GitHub 등 내장 도구가 지원되는 서비스 연동 시에는 **절대로 HTTP 노드를 사용해선 안 됩니다.**
- 오직 템플릿/내장 도구에 없는 임의 외부 REST API를 호출할 때만 HTTP 노드를 설계합니다.
- `config.method` ("GET", "POST", "PUT", "DELETE")와 `config.url`이 필수입니다.

### CONDITION
- 흐름 분기를 수행합니다.
- `config.operator`, `config.leftValue` (참조 형식), `config.rightValue` 가 필수입니다.
- 분기 경로는 edges에서 `conditionType: "true"` 또는 `"false"`로 처리합니다.

### TRANSFORM
- 데이터를 정제하거나 텍스트 포맷팅을 수행합니다.
- `config.mappings` 가 필수이며 dict 형식이어야 합니다.

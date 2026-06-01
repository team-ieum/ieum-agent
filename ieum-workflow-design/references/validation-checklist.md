# Validation Checklist

워크플로우 생성 또는 수정 결과를 최종 반환하기 전에 아래 사항을 만족하는지 스스로 검수하십시오.

1. **TRIGGER 노드 검증**:
   - 워크플로우에 TRIGGER 노드가 1개만 있으며 맨 앞에 구성되어 있는가?
   - SCHEDULE 타입인 경우 `cron` 필드가 표준 5자리(예: "0 9 * * *")로 정확히 존재하고 비어있지 않은가?
2. **도구 연동 검증**:
   - Notion, Google Sheets, Gmail, Slack, Discord, GitHub 등의 호출에 HTTP 노드를 남용하지 않고 도구가 들어간 AI 노드(agentType: "react")를 사용했는가?
3. **엣지 연결 및 도달 가능성 검증**:
   - 모든 노드가 엣지로 잘 연결되어 고아 노드가 발생하지 않는가?
   - 엣지가 TRIGGER 노드로 다시 연결되는 순환 구조는 없는가?
4. **변수 참조 정합성**:
   - 이전 노드 출력을 참조할 때 `{{nodes.<노드ID>.output.<필드명>}}` 형태를 준수하고 괄호 쌍이 맞는가?
5. **데이터 무결성 및 보존 검증**:
   - 외부 데이터를 수집하는 조회 노드(예: 깃허브 PR 조회, 노션 페이지 조회 등)가 값을 임의로 가공/왜곡하지 않으면서도 후속 노드가 실제 사용하는 필드만 추출하도록 프롬프트가 작성되어 있는가? 전체 원시 JSON을 통째로 덤프하게 작성되어 있다면 결함이다(거대 출력은 실행 타임아웃 유발). 가능하면 조회 단계 server-side 필터(state/날짜 범위/개수 제한)와 후속 필터의 선반영이 포함되어 있는가?
   - 데이터의 필터링, 정제, 포맷팅, 요약 작업이 별도 TRANSFORM 노드 또는 별도 AI 노드(prompt 위임)로 분리되어 설계되었는가?
6. **tools 작성 검증**:
   - AI 노드의 `tools`에 빌트인 도구 키(`slack`, `discord`, `gmail`, `builtin:...`)만 들어있는가? 서브 에이전트 이름(`transform_agent`, `web_agent`, `github_agent` 등)이나 GitHub 도구명(`github_list_pull_requests` 등)이 `tools`에 들어가 있지 않은가? (GitHub/가공 작업은 prompt로만 지시하고 `tools`는 비움)

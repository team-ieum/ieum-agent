# Tool Selection Guide

특정 도메인의 요구사항을 기획/생성할 때 아래 도구(tools)를 AI 노드에 주입해 설계하십시오.

**도구 표기 규칙 (필수)**: AI 노드의 `tools` 항목은 `{"name": "<도구키>"}` 형식이며, `<도구키>`는 아래 명시된 정확한 문자열을 **그대로** 사용합니다.
- Notion·Google·기타 빌트인 도구는 반드시 `builtin:` 프리픽스를 붙입니다. (예: `builtin:notion_create_page`)
- Slack·Discord·Gmail은 프리픽스 없이 키 그대로 씁니다. (`slack`, `discord`, `gmail`)
- 프리픽스를 누락하거나 변형하면 실행 시 도구가 매칭되지 않아 무시됩니다.

### Notion 연동
- **builtin:notion_create_page**: Notion 페이지 생성 (parent_page_id 필요)
- **builtin:notion_read_page**: Notion 페이지 내용 읽기 (page_id 필요)
- **builtin:notion_search**: Notion 워크스페이스 검색 (query 필요)
- **builtin:notion_update_page**: Notion 페이지 수정
- **builtin:notion_append_block**: Notion 블록 추가
- *설정 주의*: Notion 툴은 AI 노드(agentType: "react")와 사용해야 하며, config에 `parent_page_id`가 지정되지 않았다면 우선 `builtin:notion_search`로 페이지를 검색하도록 설계를 유도합니다.

### Google 연동
- **builtin:google_sheets_read**: 스프레드시트 읽기 (spreadsheet_id, range 필요)
- **builtin:google_sheets_write**: 스프레드시트 쓰기 (spreadsheet_id, range, values 필요)
- **builtin:google_calendar_create**: 구글 캘린더 일정 생성
- **builtin:google_calendar_list**: 구글 캘린더 일정 조회
- **builtin:google_drive_read**: 구글 드라이브 파일 읽기 (file_id 필요)
- **builtin:google_drive_upload**: 구글 드라이브 파일 업로드 (name, content 필요)

### 알림/메시징 연동
- **slack**: Slack 메시지 전송 (tools에 `{"name": "slack"}` 등록)
- **discord**: Discord 웹훅 메시지 전송 (tools에 `{"name": "discord"}` 등록)
- **gmail**: Gmail 메일 발송 (tools에 `{"name": "gmail"}` 등록)

### GitHub 연동
- GitHub는 빌트인 도구 키가 아니라 GitHub 연동 토큰이 주입될 때 전용 서브 에이전트로 처리됩니다.
- GitHub 작업이 필요한 AI 노드는 agentType을 "react"로 설정하고, prompt에 수행할 GitHub 작업(이슈/PR/리포 조회 등)을 명시합니다.

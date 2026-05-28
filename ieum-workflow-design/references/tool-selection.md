# Tool Selection Guide

특정 도메인의 요구사항을 기획/생성할 때 아래 도구(tools) 목록을 AI 노드에 주입해 설계하십시오.

### Notion 연동
- **notion_create_page**: Notion 페이지 생성 (parent_page_id 필요)
- **notion_read_page**: Notion 페이지 내용 읽기 (page_id 필요)
- **notion_search**: Notion 워크스페이스 검색 (query 필요)
- **notion_update_page**: Notion 페이지 수정
- **notion_append_block**: Notion 블록 추가
- *설정 주의*: Notion 툴은 AI 노드(agentType: "react")와 사용해야 하며, config에 `parent_page_id`가 지정되지 않았다면 우선 notion_search로 페이지를 검색하도록 설계를 유도합니다.

### Google 연동
- **google_sheets_read**: 스프레드시트 읽기 (spreadsheet_id, range 필요)
- **google_sheets_write**: 스프레드시트 쓰기 (spreadsheet_id, range, values 필요)
- **google_calendar_create**: 구글 캘린더 일정 생성
- **google_calendar_list**: 구글 캘린더 일정 조회
- **google_drive_read**: 구글 드라이브 파일 읽기 (file_id 필요)
- **google_drive_upload**: 구글 드라이브 파일 업로드 (name, content 필요)

### 알림/메시징 연동
- **slack**: Slack 메시지 전송 (tools에 `slack` 문자열 도구 등록)
- **discord**: Discord 웹훅 메시지 전송 (tools에 `discord` 문자열 도구 등록)
- **gmail**: Gmail 메일 발송 (tools에 `gmail` 문자열 도구 등록)

### GitHub 연동
- tools 목록에 `github` 가이드를 기재하여 GitHub API 조회 대행

# Workflow Patterns

### 패턴 1: 뉴스 수집 후 Notion 요약 저장
- **순서**: TRIGGER(SCHEDULE) ➡️ AI(web_search를 탑재한 react) ➡️ AI(notion_create_page를 탑재한 react)
- **설명**: 크론 트리거로 시작해, 최신 IT 뉴스를 구글링(web_search)한 뒤, 이를 마크다운 포맷으로 가공해 노션에 새 페이지로 등록하는 파이프라인.

### 패턴 2: 깃허브 PR 수집 후 Slack 알림 발송
- **순서**: TRIGGER(SCHEDULE) ➡️ AI(github PR 리스팅용 react) ➡️ TRANSFORM(PR 데이터 정리) ➡️ AI(slack 탑재 react)
- **설명**: 깃허브에서 최근 생성된 풀 리퀘스트 목록을 수집한 뒤, 이를 개발 채널에 슬랙 포맷으로 발송하는 워크플로우.

# Bad Examples & Antipatterns

### 안티패턴 1: Notion 연동에 HTTP 노드를 직접 설계하는 경우 ⚠️

#### ❌ 나쁜 예시
```json
{
  "nodes": [
    {
      "id": "node-2",
      "type": "HTTP",
      "label": "Notion에 저장",
      "config": {
        "method": "POST",
        "url": "https://api.notion.com/v1/pages",
        "body": "{...}"
      }
    }
  ]
}
```
*이유*: Notion, Slack, Google 등의 연동은 반드시 AI 도구 노드를 사용해야 합니다. 직접 HTTP 노드를 사용하면 검증 시 탈락합니다.

####  올바른 예시
```json
{
  "nodes": [
    {
      "id": "node-2",
      "type": "AI",
      "label": "Notion에 페이지 생성",
      "config": {
        "llmProvider": "GEMINI",
        "agentType": "react",
        "tools": [{"name": "builtin:notion_create_page"}],
        "prompt": "수집된 뉴스를 노션에 페이지로 저장해줘. parent_page_id: {{nodes.node-1.output.pageId}}"
      }
    }
  ]
}
```

---

### 안티패턴 2: SCHEDULE 트리거인데 cron 필드를 누락한 경우 ⚠️

#### ❌ 나쁜 예시
```json
{
  "nodes": [
    {
      "id": "node-1",
      "type": "TRIGGER",
      "label": "매일 트리거",
      "config": {
        "triggerType": "SCHEDULE"
      }
    }
  ]
}
```
*이유*: SCHEDULE 유형의 TRIGGER 노드에는 반드시 공백 분할 5자리 형식의 `cron` 표현식이 필요합니다.

####  올바른 예시
```json
{
  "nodes": [
    {
      "id": "node-1",
      "type": "TRIGGER",
      "label": "매일 아침 9시 트리거",
      "config": {
        "triggerType": "SCHEDULE",
        "cron": "0 9 * * *"
      }
    }
  ]
}
```

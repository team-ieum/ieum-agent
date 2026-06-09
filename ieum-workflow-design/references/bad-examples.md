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

---

### 안티패턴: 이중 중괄호 안에 템플릿 헬퍼/함수/반복문 사용 ⚠️

이중 중괄호는 **선행 노드 참조(`nodes.노드ID.output.필드`) 전용**이다. Handlebars/Jinja류 헬퍼·함수·반복문은 실행 엔진에 없어 문자열이 그대로 남아 깨지고, 정적 검증(WorkflowValidator)에서 거부된다.

#### ❌ 나쁜 예시
```json
{
  "type": "TRANSFORM",
  "config": {
    "mappings": {
      "summary": "## 주간 요약\n{{#each nodes.node-2.output.output}}\n- {{this.title}} ({{formatDate this.created_at 'YYYY-MM-DD'}})\n{{/each}}"
    }
  }
}
```
```json
{
  "type": "AI",
  "config": {
    "prompt": "제목은 '{{formatDate now 'YYYY-MM-DD'}} 요약'으로 저장해줘"
  }
}
```

#### ✅ 올바른 예시 — 반복/날짜/포맷은 노드 prompt에 자연어로 위임
```json
{
  "type": "AI",
  "label": "PR 목록을 마크다운으로 정리",
  "config": {
    "llmProvider": "CLAUDE",
    "credentialId": "",
    "agentType": "react",
    "tools": [],
    "prompt": "다음 PR 목록을 '- [제목](URL) (생성일)' 형식의 마크다운 목록으로 정리해줘. 데이터: {{nodes.node-2.output.output}}"
  }
}
```
```json
{
  "type": "AI",
  "label": "Notion 저장",
  "config": {
    "llmProvider": "CLAUDE",
    "credentialId": "",
    "agentType": "react",
    "tools": [{"name": "builtin:notion_create_page"}],
    "prompt": "오늘 날짜를 제목 앞에 붙여 'YYYY-MM-DD 주간 PR 요약' 형식으로 Notion 페이지를 만들어줘. 본문: {{nodes.node-3.output.markdown_summary}}"
  }
}
```

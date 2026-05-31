# Good Examples (골든 예시)

아래는 모든 설계 규칙과 검증을 통과하는 정답 워크플로우 JSON 예시다.
구조·도구 표기·변수 참조 형식을 이 예시들과 동일한 방식으로 작성한다.

---

### 예시 1: 매일 뉴스 수집 후 Notion 저장

- 흐름: TRIGGER(SCHEDULE) ➡️ AI(web_search 수집) ➡️ AI(notion_create_page 저장)
- 서로 다른 서비스(검색·노션)는 별도 AI 노드로 분리했다.

```json
{
  "nodes": [
    {
      "id": "node-1",
      "type": "TRIGGER",
      "label": "매일 아침 9시 트리거",
      "config": {"triggerType": "SCHEDULE", "cron": "0 9 * * *"}
    },
    {
      "id": "node-2",
      "type": "AI",
      "label": "IT 뉴스 검색",
      "config": {
        "llmProvider": "CLAUDE",
        "credentialId": "",
        "prompt": "오늘의 최신 IT 뉴스를 검색해줘. 결과 데이터를 절대 요약하지 말고 원본 그대로 반환하시오.",
        "agentType": "react",
        "tools": [{"name": "builtin:web_search"}]
      }
    },
    {
      "id": "node-3",
      "type": "AI",
      "label": "Notion에 페이지 생성",
      "config": {
        "llmProvider": "CLAUDE",
        "credentialId": "",
        "prompt": "다음 뉴스 내용을 마크다운으로 정리해 Notion 페이지로 저장해줘: {{nodes.node-2.output.output}}",
        "agentType": "react",
        "tools": [{"name": "builtin:notion_create_page"}]
      }
    }
  ],
  "edges": [
    {"source": "node-1", "target": "node-2", "conditionType": null},
    {"source": "node-2", "target": "node-3", "conditionType": null}
  ]
}
```

---

### 예시 2: GitHub PR 수집 → 가공 → Slack 알림

- 흐름: TRIGGER(SCHEDULE) ➡️ AI(github PR 조회) ➡️ TRANSFORM(데이터 정리) ➡️ AI(slack 발송)
- 조회 노드는 원본 데이터를 보존하고, 가공은 TRANSFORM 노드에 위임했다.

```json
{
  "nodes": [
    {
      "id": "node-1",
      "type": "TRIGGER",
      "label": "매주 월요일 오전 10시",
      "config": {"triggerType": "SCHEDULE", "cron": "0 10 * * 1"}
    },
    {
      "id": "node-2",
      "type": "AI",
      "label": "GitHub PR 목록 조회",
      "config": {
        "llmProvider": "CLAUDE",
        "credentialId": "",
        "prompt": "지난 한 주간 생성된 PR 목록을 조회해줘. 결과 데이터를 절대 요약하지 말고 JSON 원본 그대로 반환하시오.",
        "agentType": "react",
        "tools": []
      }
    },
    {
      "id": "node-3",
      "type": "TRANSFORM",
      "label": "PR 데이터 정리",
      "config": {
        "mappings": {"prSummary": "{{nodes.node-2.output.output}}"}
      }
    },
    {
      "id": "node-4",
      "type": "AI",
      "label": "Slack 알림 발송",
      "config": {
        "llmProvider": "CLAUDE",
        "credentialId": "",
        "prompt": "다음 PR 요약을 개발 채널에 슬랙 메시지로 보내줘: {{nodes.node-3.output.prSummary}}",
        "agentType": "react",
        "tools": [{"name": "slack"}]
      }
    }
  ],
  "edges": [
    {"source": "node-1", "target": "node-2", "conditionType": null},
    {"source": "node-2", "target": "node-3", "conditionType": null},
    {"source": "node-3", "target": "node-4", "conditionType": null}
  ]
}
```

---

### 예시 3: 수동 트리거 + 조건 분기

- 흐름: TRIGGER(MANUAL) ➡️ AI(분석) ➡️ CONDITION ➡️(true) AI(Gmail 발송)
- CONDITION 분기는 edges의 conditionType("true"/"false")으로 표현한다.

```json
{
  "nodes": [
    {
      "id": "node-1",
      "type": "TRIGGER",
      "label": "수동 실행",
      "config": {"triggerType": "MANUAL"}
    },
    {
      "id": "node-2",
      "type": "AI",
      "label": "문의 내용 분석",
      "config": {
        "llmProvider": "CLAUDE",
        "credentialId": "",
        "prompt": "고객 문의를 분석해 긴급도(urgent/normal)를 판정해줘.",
        "agentType": "simple",
        "tools": []
      }
    },
    {
      "id": "node-3",
      "type": "CONDITION",
      "label": "긴급 여부 분기",
      "config": {
        "operator": "equals",
        "leftValue": "{{nodes.node-2.output.output}}",
        "rightValue": "urgent"
      }
    },
    {
      "id": "node-4",
      "type": "AI",
      "label": "긴급 알림 메일 발송",
      "config": {
        "llmProvider": "CLAUDE",
        "credentialId": "",
        "prompt": "담당자에게 긴급 문의 알림 메일을 보내줘: {{nodes.node-2.output.output}}",
        "agentType": "react",
        "tools": [{"name": "gmail"}]
      }
    }
  ],
  "edges": [
    {"source": "node-1", "target": "node-2", "conditionType": null},
    {"source": "node-2", "target": "node-3", "conditionType": null},
    {"source": "node-3", "target": "node-4", "conditionType": "true"}
  ]
}
```

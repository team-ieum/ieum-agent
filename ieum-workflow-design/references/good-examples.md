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
        "prompt": "오늘의 최신 IT 뉴스를 검색해줘. 각 기사의 제목, URL, 요지를 임의로 누락하지 말고 정리해 반환하시오.",
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
- 조회 노드는 값을 왜곡하지 않으면서 후속 노드가 쓰는 필드만 추출하고(전체 raw 덤프 금지), 가공은 TRANSFORM 노드에 위임했다.

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
        "prompt": "PR을 최신순 1페이지(per_page=30, page=1, sort=created, direction=desc)만 조회하고, 그 중 최근 7일 내 생성된 것만 골라 각 PR에서 number, title, html_url, created_at 필드만 JSON 배열로 반환해줘. 값은 임의로 요약/변경하지 마.",
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
        "left": "{{nodes.node-2.output.output}}",
        "right": "urgent"
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

---

### 예시 4: Notion 타겟 ID가 확정된 저장 (parent_page_id 주입)

- 흐름: TRIGGER(SCHEDULE) ➡️ AI(web_search 수집) ➡️ AI(notion_create_page 저장)
- 백엔드가 넘긴 Notion 타겟 힌트에서 이름이 일치해 parent_page_id를 실제 ID로 채운 형태다.
- 타겟 ID를 모를 때만 빈 문자열("")로 두고, 알 수 있으면 이렇게 명시해 런타임 ID 누락을 막는다.

```json
{
  "nodes": [
    {
      "id": "node-1",
      "type": "TRIGGER",
      "label": "매일 아침 8시 트리거",
      "config": {"triggerType": "SCHEDULE", "cron": "0 8 * * *"}
    },
    {
      "id": "node-2",
      "type": "AI",
      "label": "AI 트렌드 검색",
      "config": {
        "llmProvider": "GEMINI",
        "credentialId": "",
        "prompt": "오늘의 최신 AI 트렌드를 검색해줘. 각 항목의 제목, URL, 요지를 임의로 누락하지 말고 정리해 반환하시오.",
        "agentType": "react",
        "tools": [{"name": "builtin:web_search"}]
      }
    },
    {
      "id": "node-3",
      "type": "AI",
      "label": "Notion '트렌드 노트'에 저장",
      "config": {
        "llmProvider": "GEMINI",
        "credentialId": "",
        "prompt": "다음 내용을 마크다운으로 정리해 Notion 페이지로 저장해줘. parent_page_id: '1a2b3c4d5e6f7890'. 본문: {{nodes.node-2.output.output}}",
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

### 예시 5: Google Sheets 로그 기록

- 흐름: TRIGGER(SCHEDULE) ➡️ AI(google_sheets_write 기록)
- access_token은 빈 값으로 두고(런타임 주입), spreadsheet_id·range는 prompt에 명시한다.

```json
{
  "nodes": [
    {
      "id": "node-1",
      "type": "TRIGGER",
      "label": "매시간 정각 트리거",
      "config": {"triggerType": "SCHEDULE", "cron": "0 * * * *"}
    },
    {
      "id": "node-2",
      "type": "AI",
      "label": "Google Sheets에 상태 기록",
      "config": {
        "llmProvider": "GEMINI",
        "credentialId": "",
        "prompt": "현재 시각과 상태값을 스프레드시트에 한 행 추가해줘. spreadsheet_id: 'sheet-abc123', range: 'Sheet1!A:B', values: [[현재시각, 'OK']]",
        "agentType": "react",
        "tools": [{"name": "builtin:google_sheets_write"}]
      }
    }
  ],
  "edges": [
    {"source": "node-1", "target": "node-2", "conditionType": null}
  ]
}
```

---

### 예시 6: 외부 REST API 호출 (HTTP 노드)

- 흐름: TRIGGER(SCHEDULE) ➡️ HTTP(외부 API) ➡️ AI(가공)
- 내장 도구가 없는 임의의 외부 REST API만 HTTP 노드로 호출한다. (Notion/Slack/Google/GitHub 등은 절대 HTTP 노드 금지)

```json
{
  "nodes": [
    {
      "id": "node-1",
      "type": "TRIGGER",
      "label": "매일 정오 트리거",
      "config": {"triggerType": "SCHEDULE", "cron": "0 12 * * *"}
    },
    {
      "id": "node-2",
      "type": "HTTP",
      "label": "환율 API 조회",
      "config": {
        "method": "GET",
        "url": "https://open.er-api.com/v6/latest/USD",
        "headers": {},
        "body": null
      }
    },
    {
      "id": "node-3",
      "type": "AI",
      "label": "환율 요약",
      "config": {
        "llmProvider": "GEMINI",
        "credentialId": "",
        "prompt": "다음 환율 데이터에서 원화(KRW) 환율을 뽑아 한 줄로 요약해줘: {{nodes.node-2.output.body}}",
        "agentType": "simple",
        "tools": []
      }
    }
  ],
  "edges": [
    {"source": "node-1", "target": "node-2", "conditionType": null},
    {"source": "node-2", "target": "node-3", "conditionType": null}
  ]
}
```

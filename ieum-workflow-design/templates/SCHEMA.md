# 노드 카탈로그 스키마 (SSOT)

이 디렉토리는 워크플로우 노드 **카탈로그 항목**의 단일 진실 원천이다. `*.json` 하나가 항목 하나다.
MongoDB `node_templates`는 seed 사본일 뿐이다(읽는 곳 없음). 설계: ieum-backend
`docs/superpowers/specs/2026-10-05-node-catalog-design.md`.

항목은 두 곳에서 쓰인다.
- **생성(LLM)** — `generation: true` 항목. 로더가 필드에서 `slots`(LLM이 채울 자리)와
  `allowed_config_fields`(검증 화이트리스트)를 **파생**한다. 파일에 직접 쓰지 않는다.
- **빌더(FE)** — `builder: true` 항목. 카탈로그 API(`GET /v1/nodes/catalog`)가 `inputFields`를 내보낸다.

## 항목 필드

| 필드 | 필수 | 설명 |
|---|---|---|
| `id` | ✅ | 파일명과 일치. `<종류>.<이름>` (예: `ai.notion_create_page`, `action.slack_send`) |
| `node_type` | ✅ | `TRIGGER` `AI` `ACTION` `HTTP` `CONDITION` `TRANSFORM` `APPROVAL` |
| `tool_key` | ✅ | 바인딩하는 `_TOOL_MAP` 키, 없으면 `null` |
| `fixed` | ✅ | 불변 노드 골격(`type` + `config` 불변 키). `serviceType`은 쓰지 않는다(`app`에서 주입) |
| `app` | ⬜ | 앱 노드만 `GOOGLE`·`NOTION`·`GITHUB`·`SLACK`·`DISCORD`. 로더가 `fixed.config.serviceType`에 넣는다 |
| `builder` / `generation` | ⬜ | 기본 `true`. 액션은 `generation: false`(실행·생성 전환 전), 앱 도구 AI 프리셋은 `builder: false` |
| `title` / `description` | builder면 ✅ | 빌더에 보이는 항목 이름·한 문장 설명 |
| `tags` / `menu` | generation이면 ✅ | LLM 태그 검색·항상층 메뉴 |
| `inputFields` | ⬜ | config 직속 필드(Field 목록). AI 항목은 공통 필드(llmProvider·model·prompt·credentialId·systemMessage) 뒤에 붙고, 같은 key를 쓰면 공통 필드에 병합된다(예: 템플릿별 prompt `llm.hint`) |
| `outputFields` | ⬜ | 출력 필드(Field 목록). AI 항목은 공통 출력(output·metadata)이 앞에 붙는다. 액션은 도구 성공 반환 키와 같아야 한다(성공 테스트의 `assert_matches_outputs`) |
| `outputDynamic` | ⬜ | 최상위 출력 키가 실행마다 다름(수동·웹훅 트리거) |
| `outputsFrom` | ⬜ | 출력 키를 노드 config에서 읽음(`config.mappings` = 그 dict의 키) |
| `match` | ⬜ | 저장된 노드 → 항목 매칭 `{경로: 값}`(값 null = 없거나 빔). 없으면 type + tool_key + triggerType로 자동. builder 항목끼리 같으면 거부 |
| `fields` | ⬜ | 도구 필드 덮어쓰기 `{파라미터: 부분 Field}`. **도구당 한 항목만**(액션이 있으면 액션, 없으면 그 AI 항목). 시그니처 밖 키는 title·type 포함 전체 정의 |
| `golden_snippet` | ⬜ | 완성형 노드 예시(검색층 few-shot). 그 자체로 검증을 통과해야 함 |
| `service` | ⬜ | 동적 서브에이전트 서비스명(ai.github_query) |

`slots`·`allowed_config_fields`를 파일에 쓰면 로더가 거부한다.

## Field

```jsonc
{"key": "spreadsheet_id", "title": "스프레드시트", "type": "string",
 "description": "대상 스프레드시트", "required": true, "default": "primary",
 "choices": [{"id": "page", "name": "페이지"}], "optionsSource": "google.spreadsheets",
 "optionsInputs": ["spreadsheet_id"], "list": false, "children": [], "dynamic": false, "ref": true,
 "path": "config.custom.path",                       // 직속 필드의 예외 경로만
 "llm": {"hint": "…", "slot": true, "name": "…", "kind": "expr", "inject": "model"}}
```
- `type`: `string` `text` `integer` `number` `boolean` `datetime` `cron` `json` `dict` `object`
- 도구 필드의 type·required·default는 시그니처에서 파생(덮어쓰기 가능). 실행 시 주입 인자(`RUNTIME_INJECTED_PARAMS`)는 빠진다.
- `default`는 키가 없을 때 실행이 쓰는 값이다. FE는 저장하지 않는다(키 있음 = 고정).
- `llm`은 생성 전용이다. `hint`=슬롯 설명, `slot: false`=슬롯 만들지 않음, `name`=표시 이름 슬롯
  `<key>_name`(경로 `…_names.<key>`), `kind`=기존 슬롯 kind 고정, `inject`=시스템 주입(provider|model).

## 파생 규칙 (core/node_fields.py)

- 경로: 직속 `config.<key>`, 도구 필드 `config.tools.0.config.<key>`.
- 슬롯 = label·description + 직속 필드 + `optionsSource`가 있는 도구 필드(항상 선택). `llm.slot: false` 제외.
- 슬롯 kind = `llm.kind` → `llm.inject` → choices면 enum → cron → dict면 mapping → string.
- 허용 config 키 = fixed.config 키 ∪ 직속 필드 최상위 키 ∪ (직속 optionsSource 필드가 있으면 `_names`).

## 불변 규칙

1. `id`는 전역 고유, 파일명(`<id>.json`)과 일치.
2. `tool_key`·`fixed.config.tools[*].name`은 `_TOOL_MAP`에 존재(`mcp` 센티넬 제외).
3. 도구 하나의 `fields`는 한 항목에만.
4. `optionsInputs`는 같은 항목의 필드 key만 가리킨다.
5. `golden_snippet`은 단독으로 `WorkflowValidator` 노드 검증을 통과해야 한다.
6. builder 항목의 match는 서로 달라야 한다(평가 순서: 조건 수 내림차순).

# 노드 템플릿 레지스트리 스키마 (SSOT)

이 디렉토리는 워크플로우 노드 템플릿의 **단일 진실 원천(SSOT)** 이다.
각 `*.json` 파일이 노드 템플릿 1개이며, git으로 리뷰·버전관리된다.
MongoDB `node_templates` 컬렉션은 이 파일들을 seed로 동기화한 사본일 뿐이다(#3).

## 목적

LLM이 노드를 자유 생성하며 도구 키·config 필드를 환각하는 문제를 막는다.
템플릿은 **FIXED(불변)** 영역과 **SLOT(LLM이 채움)** 영역을 분리한다.

- FIXED: `type`, 도구 키(`tools`), `agentType`, `credentialId=""` 등 → 템플릿이 고정 → 환각 클래스 제거
- SLOT: `prompt`, `cron`, `label`, `description`, `llmProvider`, 리소스 ID 등 → LLM이 채움

`description`은 모든 템플릿의 필수 슬롯이며, 개발자용 메모가 아니라 워크플로우 화면에서
사용자에게 그대로 보여줄 자연어 1문장이다(예: "AI가 문의 내용을 읽고 알맞은 유형으로 나눠요.").

## 템플릿 필드

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `id` | string | ✅ | 템플릿 고유 ID. `<node_type소문자>.<이름>` 관례 (예: `ai.notion_create_page`). 파일명과 일치. |
| `node_type` | string | ✅ | `TRIGGER` \| `AI` \| `HTTP` \| `CONDITION` \| `TRANSFORM` |
| `tool_key` | string \| null | ✅ | 이 템플릿이 바인딩하는 `_TOOL_MAP` 키. 구조 노드/능력 기반 AI(GitHub 등)는 `null`. 값이 있으면 `_TOOL_MAP`에 존재해야 함(드리프트 검증). |
| `tags` | string[] | ✅ | 태그/키워드 검색(#4)용. 한/영 동의어 포함. |
| `menu` | string | ✅ | 항상층에 주입되는 1줄 메뉴(노드 존재 인지 → 검색 miss 환각 방지). |
| `fixed` | object | ✅ | 불변 노드 골격. `type`과 `config`의 불변 키만 포함. |
| `slots` | Slot[] | ✅ | LLM이 채우는 필드 명세. 아래 Slot 스펙. |
| `allowed_config_fields` | string[] | ✅ | 이 노드 타입에서 허용되는 config 키 전체(fixed + slot + 선택 필드). #5 화이트리스트 검증의 기준. |
| `golden_snippet` | object | ⬜ | 완성형 노드 JSON 예시(검색층에 주입, few-shot). |

### Slot 스펙

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `name` | string | ✅ | 슬롯 이름 |
| `path` | string | ✅ | 노드 JSON 내 위치. `label` / `description` 또는 `config.<key>` (점 표기) |
| `required` | bool | ✅ | 필수 여부 |
| `kind` | string | ✅ | `string` \| `enum` \| `provider` \| `cron` \| `expr` \| `mapping` \| `http_method` |
| `enum` | string[] | ⬜ | `kind=enum`일 때 허용 값 |
| `description` | string | ⬜ | LLM 작성 가이드 |

`kind=provider`: 요청자 LLM provider(CLAUDE\|OPENAI\|GEMINI)를 계승하는 특수 슬롯.

## 불변 규칙

1. `id`는 전역 고유, 파일명(`<id>.json`)과 일치.
2. `tool_key`가 null이 아니면 `_TOOL_MAP`에 반드시 존재(seed/CI 드리프트 검증).
3. `fixed.config.tools[*].name`은 모두 `_TOOL_MAP`에 존재해야 함.
4. `allowed_config_fields`는 `fixed.config`의 모든 키와 `slots`의 `config.*` path를 포함해야 함.
5. `golden_snippet`이 있으면 그 자체로 `WorkflowValidator` 노드 검증을 통과해야 함(#8 회귀 테스트).

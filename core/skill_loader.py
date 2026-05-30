import os
import logging

logger = logging.getLogger(__name__)

SKILL_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "ieum-workflow-design")
)

# 생성 단계에서 항상 주입하는 설계 레퍼런스 파일 (로드 순서대로).
# 도구 카탈로그(tool-selection.md)를 프롬프트 키워드와 무관하게 상시 주입하여,
# 모델이 "이메일/스프레드시트" 등 키워드 매칭에서 누락되던 도구의 존재를 항상 인지하도록 한다.
_REFERENCE_FILES = [
    "node-types.md",          # 노드 타입/스키마 (공통 기본 지식)
    "tool-selection.md",      # 도구 카탈로그 (정확한 도구 키 — 상시 주입)
    "workflow-patterns.md",   # 대표 워크플로우 패턴
    "good-examples.md",       # 검증 통과 골든 예시 (few-shot 모방용)
    "bad-examples.md",        # 안티패턴 (실수 방지)
    "validation-checklist.md",  # 출력 전 자가 점검 항목
]


def load_design_rules(prompt: str = "") -> str:
    """생성 에이전트(Planner/Builder) instruction에 주입할 설계 지식 레퍼런스를 빌드한다.

    도구 카탈로그를 포함한 모든 레퍼런스를 항상 주입한다. (prompt 인자는 하위 호환을 위해 유지하며
    현재는 사용하지 않는다.)
    """
    rules = []
    for filename in _REFERENCE_FILES:
        path = os.path.join(SKILL_DIR, "references", filename)
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                rules.append(f.read())
        except Exception as e:
            logger.warning("Failed to load %s: %s", filename, e)

    return "\n\n========================================\n\n".join(rules)


def format_mcp_catalog(available_mcp_servers: list | None) -> str:
    """사용자 보유 MCP 서버 목록을 생성 에이전트 instruction에 주입할 텍스트로 포맷한다.

    available_mcp_servers의 각 항목은 dict 또는 catalogId/name/description 속성을 가진 객체.
    비어 있으면 빈 문자열을 반환한다(=MCP 배정 안내 없음).
    """
    if not available_mcp_servers:
        return ""

    def _get(item, key):
        return item.get(key) if isinstance(item, dict) else getattr(item, key, None)

    lines = []
    for m in available_mcp_servers:
        catalog_id = _get(m, "catalogId")
        name = _get(m, "name") or "(이름 없음)"
        desc = _get(m, "description") or ""
        if not catalog_id:
            continue
        lines.append(f"- catalogId={catalog_id} · 이름: {name}" + (f" · 설명: {desc}" if desc else ""))

    if not lines:
        return ""

    catalog = "\n".join(lines)
    return (
        "## 사용 가능한 MCP 서버\n"
        "사용자가 등록한 외부 MCP 서버 목록이다. 사용자의 요청이 아래 MCP 서버 중 하나로 처리하기에 "
        "적합할 때만 해당 AI 노드의 `tools`에 `mcp:<catalogId>` 형식의 문자열을 추가한다.\n"
        "- 반드시 아래 목록에 있는 정확한 catalogId만 사용한다(임의의 server_url이나 이름 사용 금지).\n"
        "- 요청과 무관하면 MCP를 배정하지 않는다.\n"
        f"{catalog}\n"
    )

import os
import re
import logging

logger = logging.getLogger(__name__)

SKILL_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "ieum-workflow-design")
)

def load_design_rules(prompt: str) -> str:
    """사용자 프롬프트 내용을 분석하여, 필요한 설계 지식 레퍼런스만

    추출하여 콤팩트하게 에이전트용 instruction에 주입하기 좋은 형태의 문자열로 빌드한다.
    """
    rules = []
    
    # 1. node-types.md 로드 (모든 요청에서 공통으로 적용되는 기본 지식)
    node_types_path = os.path.join(SKILL_DIR, "references", "node-types.md")
    if os.path.exists(node_types_path):
        try:
            with open(node_types_path, "r", encoding="utf-8") as f:
                rules.append(f.read())
        except Exception as e:
            logger.warning("Failed to load node-types.md: %s", e)
            
    # 2. bad-examples.md 로드 (모든 생성 작업의 실수를 막기 위한 공통 지식)
    bad_examples_path = os.path.join(SKILL_DIR, "references", "bad-examples.md")
    if os.path.exists(bad_examples_path):
        try:
            with open(bad_examples_path, "r", encoding="utf-8") as f:
                rules.append(f.read())
        except Exception as e:
            logger.warning("Failed to load bad-examples.md: %s", e)

    # 3. tool-selection.md 선택적 로딩
    tool_sel_path = os.path.join(SKILL_DIR, "references", "tool-selection.md")
    if os.path.exists(tool_sel_path):
        try:
            with open(tool_sel_path, "r", encoding="utf-8") as f:
                content = f.read()
                
            prompt_lower = prompt.lower()
            selected_sections = []
            
            # ### 대제목을 기준으로 섹션 분리
            sections = re.split(r"(?=### )", content)
            if sections:
                selected_sections.append(sections[0].strip())
                
            for sect in sections[1:]:
                match = re.match(r"###\s*([a-zA-Z가-힣\s/]+)", sect)
                if match:
                    title = match.group(1).lower()
                    
                    # 키워드 맵핑
                    keywords = {
                        "notion": ["notion", "노션"],
                        "google": ["google", "구글", "sheet", "시트", "calendar", "캘린더", "drive", "드라이브", "gmail", "지메일"],
                        "slack": ["slack", "슬랙"],
                        "discord": ["discord", "디스코드"],
                        "github": ["github", "깃허브", "pr"],
                        "알림": ["slack", "슬랙", "discord", "디스코드", "gmail", "지메일", "메시지", "알림"],
                        "메시징": ["slack", "슬랙", "discord", "디스코드", "gmail", "지메일", "메시지", "알림"]
                    }
                    
                    should_include = False
                    for key, kw_list in keywords.items():
                        if key in title:
                            if any(kw in prompt_lower for kw in kw_list):
                                should_include = True
                                break
                                
                    if should_include:
                        selected_sections.append(sect.strip())
                        
            if len(selected_sections) > 1:
                rules.append("\n\n".join(selected_sections))
        except Exception as e:
            logger.warning("Failed to load tool-selection.md: %s", e)

    return "\n\n========================================\n\n".join(rules)

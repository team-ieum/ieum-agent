import re
from typing import Any, Dict, List

# 주요 토큰 패턴 매칭 정규식
GOOGLE_TOKEN_PATTERN = r"ya29\.[a-zA-Z0-9_\-]+"
NOTION_TOKEN_PATTERN = r"secret_[a-zA-Z0-9_\-]+"
GITHUB_TOKEN_PATTERN = r"ghp_[a-zA-Z0-9_]+"
# 일반적인 API Key 및 Bearer 토큰 패턴
API_KEY_PATTERN = r"(?i)(['\"]?(?:api[-_]?key|auth|token|credential)['\"]?)\s*[:=]\s*['\"]([a-zA-Z0-9_\-\.\=\+]{8,})['\"]"
BEARER_TOKEN_PATTERN = r"(?i)bearer\s+([a-zA-Z0-9_\-\.\/\=\+]{10,})"

class OutputValidator:
    """최종 응답 및 데이터 로그 저장 시 민감 정보 유출 방지 마스킹 처리기"""

    @classmethod
    def mask_log_content(cls, data: Any) -> Any:
        """로그용 마스킹: 비교적 타이트하고 넓은 범위의 마스킹 규칙 적용 (DB 누출 방지)"""
        return cls._apply_masking(data, strict=True)

    @classmethod
    def mask_response_content(cls, data: Any) -> Any:
        """응답용 마스킹: 사용자 응답 손상(JSON 망가짐 등)을 최소화하도록 명백한 크레덴셜 토큰만 타겟 마스킹"""
        return cls._apply_masking(data, strict=False)

    @classmethod
    def _apply_masking(cls, data: Any, strict: bool = False) -> Any:
        if isinstance(data, str):
            return cls._mask_string(data, strict)
        elif isinstance(data, dict):
            return {k: cls._apply_masking(v, strict) for k, v in data.items()}
        elif isinstance(data, list):
            return [cls._apply_masking(item, strict) for item in data]
        return data

    @classmethod
    def _mask_string(cls, text: str, strict: bool) -> str:
        # 1. Google OAuth Token 마스킹
        text = re.sub(GOOGLE_TOKEN_PATTERN, "[MASKED_GOOGLE_TOKEN]", text)

        # 2. Notion Secret Token 마스킹
        text = re.sub(NOTION_TOKEN_PATTERN, "[MASKED_NOTION_TOKEN]", text)

        # 3. GitHub Token 마스킹
        text = re.sub(GITHUB_TOKEN_PATTERN, "[MASKED_GITHUB_TOKEN]", text)

        # 4. Bearer 토큰 패턴 마스킹
        text = re.sub(BEARER_TOKEN_PATTERN, "Bearer [MASKED_BEARER_TOKEN]", text)

        # 5. Strict 모드(로그용)인 경우 키-밸류 패턴의 일반 API Key도 탐지하여 마스킹
        if strict:
            def repl_key(match):
                key = match.group(1)
                return f"{key}: '[MASKED_KEY]'"
            text = re.sub(API_KEY_PATTERN, repl_key, text)

        return text

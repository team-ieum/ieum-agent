from enum import Enum


class ErrorCode(Enum):
    INVALID_PROVIDER       = ("AI_001", "지원하지 않는 AI Provider입니다.")
    MISSING_CREDENTIAL     = ("AI_002", "LLM 헤더가 누락되었습니다.")
    AGENT_EXECUTION_FAILED = ("AI_003", "에이전트 실행에 실패했습니다.")
    MONGODB_ERROR          = ("AI_004", "데이터베이스 오류가 발생했습니다.")
    TOOL_EXECUTION_FAILED  = ("AI_005", "Tool 실행에 실패했습니다.")

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message

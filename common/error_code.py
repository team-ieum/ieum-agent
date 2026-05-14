from enum import Enum


class ErrorCode(Enum):
    INVALID_PROVIDER       = ("AI_001", "지원하지 않는 AI Provider입니다.")
    MISSING_CREDENTIAL     = ("AI_002", "LLM 헤더가 누락되었습니다.")
    AGENT_EXECUTION_FAILED = ("AI_003", "에이전트 실행에 실패했습니다.")
    MONGODB_ERROR          = ("AI_004", "데이터베이스 오류가 발생했습니다.")
    TOOL_EXECUTION_FAILED      = ("AI_005", "Tool 실행에 실패했습니다.")
    WORKFLOW_GENERATION_FAILED = ("AI_006", "워크플로우 생성에 실패했습니다.")
    WORKFLOW_PARSE_FAILED      = ("AI_007", "워크플로우 JSON 파싱에 실패했습니다. 다시 시도해주세요.")
    WORKFLOW_MODIFY_FAILED     = ("AI_008", "워크플로우 수정에 실패했습니다.")
    WORKFLOW_MODIFY_PARSE_FAILED = ("AI_009", "수정된 워크플로우 파싱에 실패했습니다. 다시 시도해주세요.")

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message

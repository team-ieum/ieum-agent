from enum import Enum


class ErrorCode(Enum):
    INVALID_PROVIDER             = (400, "지원하지 않는 AI Provider입니다.")
    MISSING_CREDENTIAL           = (401, "LLM 헤더가 누락되었습니다.")
    AGENT_EXECUTION_FAILED       = (500, "에이전트 실행에 실패했습니다.")
    MONGODB_ERROR                = (500, "데이터베이스 오류가 발생했습니다.")
    WORKFLOW_GENERATION_FAILED   = (500, "워크플로우 생성에 실패했습니다.")
    WORKFLOW_PARSE_FAILED        = (502, "워크플로우 JSON 파싱에 실패했습니다. 다시 시도해주세요.")
    WORKFLOW_MODIFY_FAILED       = (500, "워크플로우 수정에 실패했습니다.")
    WORKFLOW_MODIFY_PARSE_FAILED = (502, "수정된 워크플로우 파싱에 실패했습니다. 다시 시도해주세요.")

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message


class ToolErrorCode(Enum):
    EXECUTION_FAILED = "Tool 실행에 실패했습니다."

    def __init__(self, message: str):
        self.message = message

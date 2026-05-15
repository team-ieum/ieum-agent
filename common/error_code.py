from enum import Enum


class ErrorCode(Enum):
    INVALID_PROVIDER             = (400, "지원하지 않는 AI Provider입니다.")
    MISSING_CREDENTIAL           = (400, "LLM 헤더가 누락되었습니다.")
    AGENT_EXECUTION_FAILED       = (500, "에이전트 실행에 실패했습니다.")
    MONGODB_ERROR                = (500, "데이터베이스 오류가 발생했습니다.")
    WORKFLOW_GENERATION_FAILED   = (500, "워크플로우 생성에 실패했습니다.")
    WORKFLOW_PARSE_FAILED        = (502, "워크플로우 JSON 파싱에 실패했습니다. 다시 시도해주세요.")
    WORKFLOW_MODIFY_FAILED       = (500, "워크플로우 수정에 실패했습니다.")
    WORKFLOW_MODIFY_PARSE_FAILED = (502, "수정된 워크플로우 파싱에 실패했습니다. 다시 시도해주세요.")
    CHAT_EXECUTION_FAILED        = (500, "채팅 처리에 실패했습니다.")

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message


class ToolErrorCode(Enum):
    """
    Tool 실행 에러 코드.

    Tool은 HTTP 응답이 아닌 LLM에게 돌아가는 tool result JSON에 에러를 담아 반환한다.
    HTTP status_code가 불필요하므로 ErrorCode와 별도로 관리한다.
    """
    EXECUTION_FAILED = "Tool 실행에 실패했습니다."

    def __init__(self, message: str):
        self.message = message

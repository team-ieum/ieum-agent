from fastapi import HTTPException

from common.error_code import ErrorCode


class AgentException(Exception):
    def __init__(self, error_code: ErrorCode):
        self.error_code = error_code
        self.status_code = error_code.status_code
        self.message = error_code.message
        super().__init__(self.message)


def is_rate_limit_error(e: Exception) -> bool:
    """LLM provider 예외가 429(rate limit/quota)인지 판별.

    genai/ADK는 `.code`, anthropic/openai는 `.status_code`로 HTTP status를 노출한다.
    """
    return (getattr(e, "code", None) == 429) or (getattr(e, "status_code", None) == 429)


def llm_http_exception(e: Exception, fallback: ErrorCode) -> HTTPException:
    """LLM provider 예외를 HTTP status로 매핑. 429는 그대로, 나머지는 fallback."""
    if is_rate_limit_error(e):
        return HTTPException(
            status_code=ErrorCode.RATE_LIMITED.status_code,
            detail=ErrorCode.RATE_LIMITED.message,
        )
    return HTTPException(status_code=fallback.status_code, detail=fallback.message)

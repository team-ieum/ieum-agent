from common.error_code import ErrorCode
from common.exception import is_rate_limit_error, llm_http_exception


class _GenaiError(Exception):
    """genai/ADK 계열: .code 로 HTTP status 노출."""
    def __init__(self, code):
        self.code = code


class _SdkError(Exception):
    """anthropic/openai 계열: .status_code 로 HTTP status 노출."""
    def __init__(self, status_code):
        self.status_code = status_code


def test_is_rate_limit_error_genai_429_True():
    assert is_rate_limit_error(_GenaiError(429)) is True


def test_is_rate_limit_error_sdk_429_True():
    assert is_rate_limit_error(_SdkError(429)) is True


def test_is_rate_limit_error_다른_코드_False():
    assert is_rate_limit_error(_GenaiError(500)) is False
    assert is_rate_limit_error(_SdkError(400)) is False


def test_is_rate_limit_error_속성없음_False():
    assert is_rate_limit_error(Exception("일반 오류")) is False


def test_llm_http_exception_429는_RATE_LIMITED():
    exc = llm_http_exception(_GenaiError(429), ErrorCode.CHAT_EXECUTION_FAILED)
    assert exc.status_code == 429
    assert exc.detail == ErrorCode.RATE_LIMITED.message


def test_llm_http_exception_나머지는_fallback():
    exc = llm_http_exception(Exception("기타"), ErrorCode.CHAT_EXECUTION_FAILED)
    assert exc.status_code == ErrorCode.CHAT_EXECUTION_FAILED.status_code
    assert exc.detail == ErrorCode.CHAT_EXECUTION_FAILED.message

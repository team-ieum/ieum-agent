from common.exception import is_rate_limit_error


class _GenaiError(Exception):
    """genai/ADK 계열: .code 로 HTTP status 노출."""
    def __init__(self, code):
        self.code = code


class _SdkError(Exception):
    """anthropic/openai 계열: .status_code 로 HTTP status 노출."""
    def __init__(self, status_code):
        self.status_code = status_code


def test_is_rate_limit_error_sdk_429_True():
    assert is_rate_limit_error(_SdkError(429)) is True


def test_is_rate_limit_error_다른_코드_False():
    assert is_rate_limit_error(_GenaiError(500)) is False
    assert is_rate_limit_error(_SdkError(400)) is False

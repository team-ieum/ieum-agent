import re
from typing import Iterator

from fastapi import HTTPException

from common.error_code import ErrorCode


class AgentException(Exception):
    def __init__(self, error_code: ErrorCode):
        self.error_code = error_code
        self.status_code = error_code.status_code
        self.message = error_code.message
        super().__init__(self.message)


class CodedHTTPException(HTTPException):
    """errorCode(ErrorCode enum 이름)를 함께 싣는 HTTP 예외.

    BE는 실패 응답 본문의 errorCode로 재시도 여부를 판정한다. HTTP 에러 경로도
    상태코드 추론에 의존하지 않도록 본문에 errorCode를 실어 보낸다(main.py 핸들러)."""

    def __init__(self, error_code: ErrorCode):
        super().__init__(status_code=error_code.status_code, detail=error_code.message)
        self.error_code = error_code.name


# rate limit을 나타내는 예외 타입명(litellm/openai/anthropic/google 계열 공통).
_RATE_LIMIT_TYPE_NAMES = {"RateLimitError", "ResourceExhausted", "TooManyRequests"}
# 상태 속성이 없는 래핑 예외를 위한 메시지 신호.
# 체인의 모든 예외 문자열에 대해 돌기 때문에 맨 숫자 429는 쓰지 않는다 —
# "retry 429 times"·'"count": 429' 같은 무관한 메시지가 rate limit으로 오분류되면
# BE가 헛재시도한다. 429는 상태코드 문맥이 붙은 경우에만 신호로 인정한다.
_RATE_LIMIT_TEXT = re.compile(
    r"RESOURCE_EXHAUSTED"
    r"|rate[ _-]?limit"
    r"|too many requests"
    r"|quota (?:exceeded|exhausted)|exceeded your current quota"
    r"|(?:status|status_code|code|http|error)[\s=:]*429\b",
    re.IGNORECASE,
)
# provider/MCP 계열 예외에 한해 인정하는 느슨한 신호.
# MCP는 upstream 429를 McpError(message="...429...")로만 넘긴다 — .code/.status_code도 없고
# 타입명도 rate limit 계열이 아니라 문자열이 유일한 단서다. 그렇다고 모든 예외에 맨 429를
# 허용하면 무관한 메시지가 걸리므로, 예외가 선언된 모듈로 대상을 좁힌다.
_BARE_429 = re.compile(r"\b429\b")
_PROVIDER_MODULES = ("litellm", "google.genai", "google.api_core", "anthropic", "openai", "mcp")


def _from_provider_module(exc: BaseException) -> bool:
    return (type(exc).__module__ or "").startswith(_PROVIDER_MODULES)


def _iter_chain(e: BaseException | None, seen: set[int] | None = None) -> Iterator[BaseException]:
    """예외 체인(__cause__/__context__)과 ExceptionGroup 하위 예외를 모두 순회한다.

    ADK/MCP 실행 경로는 anyio task group·AsyncExitStack 정리 과정에서 원래 예외를
    ExceptionGroup이나 다른 예외의 __context__로 감싸 올린다. 최상위 예외만 보면
    provider의 429가 사라져 일반 실패로 오분류된다."""
    if e is None:
        return
    seen = seen if seen is not None else set()
    if id(e) in seen:
        return
    seen.add(id(e))
    yield e
    for sub in getattr(e, "exceptions", None) or ():
        yield from _iter_chain(sub, seen)
    yield from _iter_chain(e.__cause__, seen)
    yield from _iter_chain(e.__context__, seen)


def is_rate_limit_error(e: Exception) -> bool:
    """LLM provider 예외가 429(rate limit/quota)인지 판별.

    genai/ADK는 `.code`, litellm/anthropic/openai는 `.status_code`로 HTTP status를 노출한다.
    래핑된 예외도 놓치지 않도록 예외 체인 전체를 훑는다.
    """
    for exc in _iter_chain(e):
        if getattr(exc, "code", None) == 429 or getattr(exc, "status_code", None) == 429:
            return True
        if type(exc).__name__ in _RATE_LIMIT_TYPE_NAMES:
            return True
        if _RATE_LIMIT_TEXT.search(str(exc)):
            return True
        if _from_provider_module(exc) and _BARE_429.search(str(exc)):
            return True
    return False


def llm_http_exception(e: Exception, fallback: ErrorCode) -> HTTPException:
    """LLM provider 예외를 HTTP status로 매핑. 429는 그대로, 나머지는 fallback."""
    if is_rate_limit_error(e):
        return CodedHTTPException(ErrorCode.RATE_LIMITED)
    return CodedHTTPException(fallback)

from api.schemas.response import ToolCallRecord, UsageRecord
from db.models import ToolCallLog, TokenUsage


def test_toolcall_log_필드가_toolcall_record와_일치():
    """db/models.py의 ToolCallLog 필드가 response.py의 ToolCallRecord와 일치한다."""
    record = ToolCallRecord(name="builtin:http_fetch", arguments={"url": "https://test.com"}, result="ok")
    dumped = record.model_dump()

    # ToolCallLog로 생성 가능한지 검증
    log = ToolCallLog(**dumped)
    assert log.name == "builtin:http_fetch"
    assert log.arguments == {"url": "https://test.com"}
    assert log.result == "ok"


def test_token_usage_필드가_usage_record와_일치():
    """db/models.py의 TokenUsage 필드가 response.py의 UsageRecord와 일치한다."""
    record = UsageRecord(promptTokens=100, completionTokens=50, totalTokens=150)
    dumped = record.model_dump()

    # TokenUsage로 생성 가능한지 검증
    usage = TokenUsage(**dumped)
    assert usage.promptTokens == 100
    assert usage.completionTokens == 50
    assert usage.totalTokens == 150


def test_toolcall_log_선택_필드_없어도_생성():
    """arguments, result 없이도 ToolCallLog 생성 가능하다."""
    log = ToolCallLog(name="builtin:notion_create_page")
    assert log.name == "builtin:notion_create_page"
    assert log.arguments is None
    assert log.result is None

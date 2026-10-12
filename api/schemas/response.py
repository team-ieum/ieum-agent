from pydantic import BaseModel, field_validator
from typing import Optional, Dict, Any, List


class ToolCallRecord(BaseModel):
    name: str
    arguments: Optional[Dict[str, Any]] = None
    result: Optional[str] = None


class UsageRecord(BaseModel):
    promptTokens: Optional[int] = None
    completionTokens: Optional[int] = None
    totalTokens: Optional[int] = None


class AgentExecutionResult(BaseModel):
    success: bool
    status: Optional[str] = None
    # 노드 출력 객체 그대로(BE가 복사해 노드 출력으로 저장한다). 출력 칸이 선언되면 그 칸만, 아니면 {"output": 텍스트}.
    output: Optional[Dict[str, Any]] = None
    errorMessage: Optional[str] = None
    errorCode: Optional[str] = None              # ErrorCode enum 이름. BE 실패 분류/재시도 판정용
    toolCalls: Optional[List[ToolCallRecord]] = None
    usage: Optional[UsageRecord] = None

    @field_validator("output", mode="before")
    @classmethod
    def _wrap_text_output(cls, value):
        """옛 output: str을 {"output": str}로 받는다. 배포 직후 멱등 캐시(COMPLETED_TTL 1시간)에 남은 옛
        레코드를 복원하기 위함이다 — 복원이 실패하면 캐시 미스로 BE 재시도가 도구 노드를 다시 실행한다."""
        return {"output": value} if isinstance(value, str) else value


class ActionExecutionResult(BaseModel):
    """POST /v1/actions/execute 응답. AgentExecutionResult와 별개다 — status·toolCalls·usage가 없고 output은 도구 반환 dict다."""
    success: bool
    output: Optional[Dict[str, Any]] = None
    errorMessage: Optional[str] = None
    errorCode: Optional[str] = None              # ErrorCode enum 이름. BE 실패 분류용

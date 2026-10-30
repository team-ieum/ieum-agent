from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


class ToolCallLog(BaseModel):
    """
    AI 에이전트가 실행한 도구 호출 기록.
    api/schemas/response.py의 ToolCallRecord.model_dump() 결과와 동일한 구조.
    """
    name: str                                    # 도구 이름 (예: builtin:http_fetch)
    arguments: Optional[dict] = None             # 도구 호출 인자
    result: Optional[str] = None                 # 도구 실행 결과


class TokenUsage(BaseModel):
    """
    LLM 토큰 사용량.
    api/schemas/response.py의 UsageRecord.model_dump() 결과와 동일한 구조.
    """
    promptTokens: Optional[int] = None          # 입력 토큰 수
    completionTokens: Optional[int] = None      # 출력 토큰 수
    totalTokens: Optional[int] = None           # 전체 토큰 수


class ExecutionLog(BaseModel):
    """
    컬렉션: execution_logs

    /v1/execute 호출 시 AI 노드 실행 결과를 저장한다.
    Spring Boot의 workflow_executions와 workflowExecutionId로 연결된다.
    """
    nodeId: str
    workflowExecutionId: Optional[str] = None
    provider: str                                # CLAUDE | OPENAI | GEMINI (ACTION 실행은 "ACTION")
    model: str                                   # 실제 사용된 모델명 (ACTION 실행은 tool_key)
    agentType: str                               # simple | react (ACTION 실행은 "action")
    status: Optional[str] = None                 # COMPLETED | ERROR | MAX_ITERATIONS
    success: bool
    output: Optional[str] = None
    errorMessage: Optional[str] = None
    errorCode: Optional[str] = None              # ErrorCode enum 이름. BE 실패 분류/재시도 판정 근거
    toolCalls: List[ToolCallLog] = []
    usage: Optional[TokenUsage] = None           # UsageRecord 구조와 동일
    durationMs: int
    createdAt: datetime


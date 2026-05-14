from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel


class ToolCallLog(BaseModel):
    """AI 에이전트가 실행한 도구 호출 기록."""
    tool: str
    input: Optional[dict] = None
    output: Optional[str] = None


class TokenUsage(BaseModel):
    """LLM 토큰 사용량."""
    inputTokens: int = 0
    outputTokens: int = 0


class ExecutionLog(BaseModel):
    """
    컬렉션: execution_logs

    /v1/execute 호출 시 AI 노드 실행 결과를 저장한다.
    Spring Boot의 workflow_executions와 workflowExecutionId로 연결된다.
    """
    nodeId: str
    workflowExecutionId: Optional[str] = None
    provider: str                                # CLAUDE | OPENAI | GEMINI
    model: str                                   # 실제 사용된 모델명
    agentType: str                               # simple | react
    status: Optional[str] = None                 # COMPLETED | ERROR | MAX_ITERATIONS
    success: bool
    output: Optional[str] = None
    errorMessage: Optional[str] = None
    toolCalls: List[ToolCallLog] = []
    usage: Optional[TokenUsage] = None
    durationMs: int
    createdAt: datetime


class GenerateWorkflowLog(BaseModel):
    """
    컬렉션: generate_workflow_logs

    /v1/generate-workflow 호출 시 워크플로우 생성 결과를 저장한다.
    """
    prompt: str                                  # 사용자 자연어 요청
    provider: str                                # CLAUDE | OPENAI | GEMINI
    model: str                                   # 실제 사용된 모델명
    success: bool
    nodeCount: Optional[int] = None              # 생성된 노드 수
    edgeCount: Optional[int] = None              # 생성된 엣지 수
    errorMessage: Optional[str] = None
    durationMs: int
    createdAt: datetime

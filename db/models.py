from datetime import datetime
from typing import Optional, List, Any
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
    provider: str                                # CLAUDE | OPENAI | GEMINI
    model: str                                   # 실제 사용된 모델명
    agentType: str                               # simple | react
    status: Optional[str] = None                 # COMPLETED | ERROR | MAX_ITERATIONS
    success: bool
    output: Optional[str] = None
    errorMessage: Optional[str] = None
    toolCalls: List[ToolCallLog] = []
    usage: Optional[TokenUsage] = None           # UsageRecord 구조와 동일
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


class ModifyWorkflowLog(BaseModel):
    """
    컬렉션: modify_workflow_logs

    /v1/modify-workflow 호출 시 워크플로우 수정 결과를 저장한다.
    """
    prompt: str                                  # 사용자 자연어 수정 요청
    provider: str                                # CLAUDE | OPENAI | GEMINI
    model: str                                   # 실제 사용된 모델명
    success: bool
    nodeCount: Optional[int] = None              # 수정 후 노드 수
    edgeCount: Optional[int] = None              # 수정 후 엣지 수
    errorMessage: Optional[str] = None
    durationMs: int
    createdAt: datetime


class ChatLog(BaseModel):
    """
    컬렉션: chat_logs

    /v1/chat 호출 시 워크플로우 Reasoning 결과를 저장한다.
    디버깅/모니터링 전용. 대화 히스토리는 저장하지 않는다.
    """
    prompt: str                                  # 사용자 자연어 요청
    provider: str                                # CLAUDE | OPENAI | GEMINI
    model: str                                   # 실제 사용된 모델명
    userId: str                                  # X-User-Id 헤더
    type: Optional[str] = None                   # WORKFLOW_GENERATED | WORKFLOW_MODIFIED | INTEGRATION_REQUIRED | CLARIFICATION_NEEDED
    success: bool
    nodeCount: Optional[int] = None              # 응답 노드 수 (WORKFLOW_* 타입만)
    edgeCount: Optional[int] = None              # 응답 엣지 수 (WORKFLOW_* 타입만)
    errorMessage: Optional[str] = None
    durationMs: int
    createdAt: datetime
    rawResponse: Optional[str] = None            # LLM 원문 응답 (hallucination 디버깅용)
    parsedResponse: Optional[Any] = None         # 파싱 성공 시 최종 JSON

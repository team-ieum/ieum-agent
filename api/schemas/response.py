from pydantic import BaseModel
from typing import Optional, Dict, Any, List


class TokenUsage(BaseModel):
    inputTokens: int = 0
    outputTokens: int = 0


class ToolCallLog(BaseModel):
    tool: str
    input: Optional[Dict[str, Any]] = None
    output: Optional[str] = None


class AgentExecutionResult(BaseModel):
    success: bool
    status: Optional[str] = None          # COMPLETED | ERROR | MAX_ITERATIONS 등
    output: Optional[str] = None
    errorMessage: Optional[str] = None
    usage: Optional[TokenUsage] = None
    toolCalls: Optional[List[ToolCallLog]] = None
    metadata: Optional[Dict[str, Any]] = None

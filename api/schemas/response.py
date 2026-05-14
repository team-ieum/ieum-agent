from pydantic import BaseModel
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
    output: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    errorMessage: Optional[str] = None
    toolCalls: Optional[List[ToolCallRecord]] = None
    usage: Optional[UsageRecord] = None

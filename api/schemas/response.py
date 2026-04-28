from pydantic import BaseModel
from typing import Optional, Dict, Any

class AgentExecutionResult(BaseModel):
    success: bool
    output: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    errorMessage: Optional[str] = None

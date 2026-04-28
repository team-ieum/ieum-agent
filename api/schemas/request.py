from pydantic import BaseModel
from typing import Optional, List, Dict, Any

class AgentNodeRequest(BaseModel):
    nodeId: str
    promptTemplateId: Optional[str] = None
    renderedPrompt: str
    tools: Optional[List[Dict[str, Any]]] = None

from fastapi import APIRouter
from pydantic import BaseModel

from core.validators.static_validator import validate_workflow

router = APIRouter()


class ValidateWorkflowRequest(BaseModel):
    """저장된 워크플로우를 그대로 받는다. 검증 대상 자체가 깨진 입력이라 노드·엣지는 느슨한 dict다 —
    WorkflowNode를 쓰면 필수 필드 누락이 422가 되어 BE가 검증을 건너뛴다(fail-open). 원소도 dict로 묶지 않는다 —
    null 원소 하나로 422가 나지 않게, dict 아닌 원소는 validate_workflow가 걸러 낸다."""
    nodes: list | None = None
    edges: list | None = None


class ValidationErrorItem(BaseModel):
    nodeId: str | None
    field: str | None
    code: str
    message: str


class ValidateWorkflowResponse(BaseModel):
    valid: bool
    errors: list[ValidationErrorItem]


@router.post("/workflows/validate", response_model=ValidateWorkflowResponse)
def validate(req: ValidateWorkflowRequest) -> dict:
    """노드 정적 검증(로드맵 ④). LLM을 쓰지 않아 get_llm_credentials를 걸지 않는다(/v1/nodes/catalog와 같은 취급).
    에러가 있어도 200 — 판정은 호출자(BE)가 valid로 한다."""
    errors = validate_workflow(req.nodes or [], req.edges or [])
    return {"valid": not errors, "errors": errors}

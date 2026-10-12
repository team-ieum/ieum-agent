"""POST /v1/workflows/validate — 계약 모양(노드 정적 검증 spec §4.3)."""
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

TRIGGER = {"id": "t", "type": "TRIGGER", "config": {"triggerType": "MANUAL"}}


def test_정상이면_valid_true_빈_목록_LLM_헤더_없이():
    resp = client.post("/v1/workflows/validate", json={"nodes": [TRIGGER], "edges": []})
    assert resp.status_code == 200
    assert resp.json() == {"valid": True, "errors": []}


def test_에러가_있어도_200_valid_false_4키_목록():
    http = {"id": "h", "type": "HTTP", "config": {}}
    resp = client.post("/v1/workflows/validate",
                       json={"nodes": [TRIGGER, http], "edges": [{"source": "t", "target": "h"}]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert body["errors"] == [{"nodeId": "h", "field": "config.url", "code": "REQUIRED",
                               "message": "'URL' 칸을 입력하세요."}]


def test_저장분_모양_그대로_받는다_필수_필드_없는_노드도_422가_아니다():
    """WorkflowNode(label 등 필수)를 쓰지 않는다 — currentNodes 422 함정."""
    resp = client.post("/v1/workflows/validate",
                       json={"nodes": [{"id": "t", "type": "TRIGGER"}, {"id": "x", "position": {"x": 1}}],
                             "edges": None})
    assert resp.status_code == 200
    assert resp.json()["valid"] is False


def test_dict가_아닌_원소가_섞여도_422가_아니다():
    resp = client.post("/v1/workflows/validate", json={"nodes": [None, "x", TRIGGER], "edges": [1]})
    assert resp.status_code == 200
    assert resp.json() == {"valid": True, "errors": []}

"""BE→agent X-Internal-Token 검사 (spec 2026-10-28 §7.4, IEUM-AI-84)."""
import logging

import pytest
from fastapi.testclient import TestClient

from core.config import settings
from main import app

client = TestClient(app)


@pytest.fixture
def token_on(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_INTERNAL_TOKEN", "s3cret")


def test_값이_없으면_검사하지_않는다():
    assert client.get("/v1/nodes/catalog").status_code == 200


def test_토큰이_없거나_다르면_401(token_on):
    for headers in ({}, {"X-Internal-Token": "wrong"}, {"X-Internal-Token": ""}):
        response = client.get("/v1/nodes/catalog", headers=headers)
        assert response.status_code == 401
        assert response.json() == {"detail": "unauthorized"}


def test_토큰이_맞으면_통과(token_on):
    response = client.get("/v1/nodes/catalog", headers={"X-Internal-Token": "s3cret"})
    assert response.status_code == 200


def test_헬스체크는_토큰_없이_통과(token_on):
    assert client.get("/health").status_code == 200


def test_헬스체크_예외는_GET만(token_on):
    assert client.post("/health").status_code == 401


def test_값이_없으면_기동_로그에_경고(caplog):
    with caplog.at_level(logging.WARNING, logger="main"):
        with TestClient(app):
            pass
    assert "internal token not set — agent accepts unauthenticated requests" in caplog.text

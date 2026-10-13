import pytest
from fastapi.testclient import TestClient

from core import template_registry as tr
from main import app


def test_startup_fails_without_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "CATALOG_DIR", str(tmp_path))      # 서브모듈 미초기화 체크아웃 흉내
    monkeypatch.setattr(tr, "_cache", None)
    with pytest.raises(tr.TemplateSchemaError, match="submodule update --init"):
        with TestClient(app):
            pass


def test_startup_fails_on_broken_catalog(tmp_path, monkeypatch):
    from tests.catalog_paths import copy_catalog
    root = copy_catalog(tmp_path, monkeypatch)
    (root / "events" / "manual" / "run.json").write_text("{not json")
    with pytest.raises(tr.TemplateSchemaError, match="JSON 파싱 실패"):
        with TestClient(app):
            pass

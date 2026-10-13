"""테스트용 카탈로그 경로 헬퍼. 카탈로그는 서브모듈 catalog/에 있다(events/<앱>/<이벤트>.json + presets.json)."""
import json
import os
import shutil

from core import template_registry as tr


def event_path(name: str) -> str:
    app, event = name.split(".", 1)
    return os.path.join(tr.CATALOG_DIR, "events", app, f"{event}.json")


def read_raw(name: str) -> dict:
    """로더가 _validate_template에 넘기는 원본 모양. 이벤트는 id·app을, 프리셋은 로더가 정하는 키를 붙인다."""
    path = event_path(name)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return {"id": name, "app": name.split(".", 1)[0], **json.load(f)}
    with open(os.path.join(tr.CATALOG_DIR, "presets.json"), encoding="utf-8") as f:
        p = next(p for p in json.load(f) if p["id"] == name)
    return {**p, "node_type": "AI", "builder": False, "preset": True}  # _read_catalog과 같은 모양


def copy_catalog(tmp_path, monkeypatch):
    """카탈로그를 tmp에 복사하고 로더가 그쪽을 보게 한다. 테스트가 끝나면 monkeypatch가 되돌린다."""
    root = tmp_path / "catalog"
    shutil.copytree(tr.CATALOG_DIR, root, ignore=shutil.ignore_patterns(".git"))
    monkeypatch.setattr(tr, "CATALOG_DIR", str(root))
    monkeypatch.setattr(tr, "_cache", None)
    return root


def edit_entry(root, name: str, change) -> None:
    """복사본 카탈로그(copy_catalog 결과)의 이벤트 파일 또는 presets.json 원소 하나를 change(raw)로 고친다."""
    app, event = name.split(".", 1)
    path = root / "events" / app / f"{event}.json"
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        change(raw)
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        return
    presets_path = root / "presets.json"
    presets = json.loads(presets_path.read_text(encoding="utf-8"))
    change(next(p for p in presets if p["id"] == name))
    presets_path.write_text(json.dumps(presets, ensure_ascii=False), encoding="utf-8")


def apps() -> dict:
    with open(os.path.join(tr.CATALOG_DIR, "apps.json"), encoding="utf-8") as f:
        return json.load(f)

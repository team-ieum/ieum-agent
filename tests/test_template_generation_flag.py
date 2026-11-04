import json

import pytest

from core import template_registry as tr
from tests.catalog_paths import apps, event_path


def test_generation_flag_must_be_bool():
    with open(event_path("manual.run"), encoding="utf-8") as f:
        raw = {"id": "manual.run", "app": "manual", **json.load(f)}
    raw["generation"] = "no"
    with pytest.raises(tr.TemplateSchemaError, match="generation"):
        tr._validate_template(raw, raw["id"], tr._tool_map_keys(), apps())

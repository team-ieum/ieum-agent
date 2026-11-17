---
paths:
  - "catalog"
  - "core/template_registry.py"
  - "core/node_catalog.py"
  - "tools/registry.py"
  - "tests/fixtures/node_catalog_snapshot.json"
  - "tests/test_catalog_loader.py"
  - "tests/test_node_catalog.py"
---
# 노드 카탈로그

- 이벤트 정의 원본은 서브모듈 `catalog/`(ieum-node-catalog). 여기서 JSON을 고치지 않는다 — catalog 레포에서 머지한 뒤 포인터를 올린다
- 포인터를 올릴 때는 포인터, 고정값(`tests/test_catalog_loader.py` 항목 수, `tests/test_node_catalog.py`), 스냅샷, 새 트리거면 `api/schemas/generate_workflow.py` `TRIGGER_TYPES`를 한 커밋에 넣는다 — 나누면 로더 기동 드리프트가 생긴다
- 스냅샷은 `test_catalog_entries_snapshot` docstring의 명령으로 다시 만들고, diff가 의도한 변경뿐인지 확인한다
- 머지 순서: BE 포인터 PR 머지·dev 배포 → 이 레포 → BE 재시작 후 `[CatalogDiff]` 로그가 없는지 확인
- 이벤트 삭제·이름 변경은 이 레포의 참조를 먼저 걷어낸 뒤 catalog에서 한다

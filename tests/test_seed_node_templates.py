import pytest

from db.mongodb import seed_node_templates
from core import template_registry as tr


class FakeCollection:
    """seed 검증용 최소 인메모리 비동기 컬렉션."""

    def __init__(self, initial=None):
        self.docs = {d["id"]: dict(d) for d in (initial or [])}
        self.indexes = []

    async def create_index(self, key, unique=False):
        self.indexes.append((key, unique))

    async def update_one(self, filt, update, upsert=False):
        _id = filt["id"]
        if _id in self.docs:
            self.docs[_id].update(update["$set"])
        elif upsert:
            self.docs[_id] = dict(update["$set"])

    async def delete_many(self, filt):
        # {"id": {"$nin": [...]}} 만 지원
        keep = set(filt["id"]["$nin"])
        stale = [k for k in self.docs if k not in keep]
        for k in stale:
            del self.docs[k]

        class _Res:
            deleted_count = len(stale)
        return _Res()


@pytest.mark.asyncio
async def test_seed_upserts_all_templates():
    col = FakeCollection()
    result = await seed_node_templates(collection=col)

    expected = {t["id"] for t in tr.all_templates()}
    assert set(col.docs.keys()) == expected
    assert result["upserted"] == len(expected)
    assert result["deleted"] == 0
    assert result["total"] == len(expected)
    # id 유니크 인덱스 생성됨
    assert ("id", True) in col.indexes


@pytest.mark.asyncio
async def test_seed_is_idempotent():
    col = FakeCollection()
    await seed_node_templates(collection=col)
    first = dict(col.docs)
    result = await seed_node_templates(collection=col)
    assert col.docs == first  # 두 번째 실행해도 동일
    assert result["deleted"] == 0


@pytest.mark.asyncio
async def test_seed_removes_stale_docs():
    # 파일에 없는 옛 템플릿이 DB에 있으면 삭제된다(파일이 SSOT)
    col = FakeCollection(initial=[{"id": "ai.removed_legacy", "menu": "옛 템플릿"}])
    result = await seed_node_templates(collection=col)
    assert "ai.removed_legacy" not in col.docs
    assert result["deleted"] == 1


@pytest.mark.asyncio
async def test_seed_doc_matches_file():
    col = FakeCollection()
    await seed_node_templates(collection=col)
    src = tr.get_template("ai.notion_create_page")
    assert col.docs["ai.notion_create_page"]["tool_key"] == src["tool_key"]
    assert col.docs["ai.notion_create_page"]["tags"] == src["tags"]

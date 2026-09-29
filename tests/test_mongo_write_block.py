from unittest.mock import AsyncMock

import pytest

from db.mongodb import db
from db.session_service import MongoSessionService


@pytest.mark.parametrize("method", ["insert_one", "update_one", "delete_one", "delete_many"])
def test_writes_blocked_on_any_collection(method):
    """conftest 차단망은 모듈 전역 컬렉션뿐 아니라 새로 만든 컬렉션 인스턴스의 쓰기도 막아야 한다."""
    assert isinstance(getattr(db["arbitrary_collection"], method), AsyncMock)
    assert isinstance(getattr(MongoSessionService().collection, method), AsyncMock)

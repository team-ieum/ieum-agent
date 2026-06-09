import os

# 테스트 DB 격리: 앱 모듈(core.config → db.mongodb)이 import되기 전에
# MONGODB_DB_NAME을 테스트 전용 DB로 강제한다. pydantic-settings는 os.environ을
# .env 파일보다 우선하므로, 어떤 테스트도 실 운영 DB(ieum)에 쓰지 않는다.
os.environ["MONGODB_DB_NAME"] = "ieum_test"

import pytest
from unittest.mock import AsyncMock


@pytest.fixture(autouse=True)
def _block_real_mongo_writes():
    """모든 테스트에서 로그 컬렉션의 insert를 차단한다(이중 안전망).

    DB 격리(ieum_test)와 별개로, generate/modify/execute 로그 저장이 실 mongo로
    새어 나가 운영 데이터를 오염시키는 것을 원천 차단한다."""
    from unittest.mock import patch
    import db.mongodb as mongodb

    patchers = []
    for name in ("generate_workflow_logs", "modify_workflow_logs", "execution_logs"):
        col = getattr(mongodb, name, None)
        if col is None:
            continue
        p = patch.object(col, "insert_one", AsyncMock())
        p.start()
        patchers.append(p)
    try:
        yield
    finally:
        for p in patchers:
            p.stop()

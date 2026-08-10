import os

# 테스트 DB 격리: 앱 모듈(core.config → db.mongodb)이 import되기 전에
# MONGODB_DB_NAME을 테스트 전용 DB로 강제한다. pydantic-settings는 os.environ을
# .env 파일보다 우선하므로, 어떤 테스트도 실 운영 DB(ieum)에 쓰지 않는다.
os.environ["MONGODB_DB_NAME"] = "ieum_test"

# 자체 호스팅 LLM 환경 격리: 테스트는 "자체 LLM 미설정" 상태를 가정하는데(예:
# test_model_factory의 is_self_hosted_eligible False 케이스), 개발자 .env에
# SELF_HOSTED_LLM_BASE_URL/MODEL이 설정돼 있으면(IEUM-AI-35 작업) 오염된다.
# os.environ을 ""로 덮어 .env 파일 값을 무력화한다(env가 .env보다 우선). 설정이
# 필요한 테스트는 monkeypatch.setattr(settings, ...)로 각자 켠다.
# NOTE(부채): 근본 원인은 "테스트가 개발자 .env를 로드한다"는 것. 여기 격리는 증상
# 처치이며, 테스트에서 .env 자동 로드를 끄는 것이 정답 — 별도 이슈로 추적할 것.
for _k in ("SELF_HOSTED_LLM_BASE_URL", "SELF_HOSTED_LLM_MODEL", "SELF_HOSTED_LLM_API_KEY"):
    os.environ[_k] = ""

import pytest
from unittest.mock import AsyncMock


@pytest.fixture(autouse=True)
def _block_real_mongo_writes():
    """모든 테스트에서 로그 컬렉션의 insert를 차단한다(이중 안전망).

    DB 격리(ieum_test)와 별개로, generate/chat/execute 로그 저장이 실 mongo로
    새어 나가 운영 데이터를 오염시키는 것을 원천 차단한다."""
    from unittest.mock import patch
    import db.mongodb as mongodb

    patchers = []
    for name in ("generate_workflow_logs", "chat_logs", "execution_logs"):
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

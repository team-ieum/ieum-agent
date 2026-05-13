import asyncio
from collections import defaultdict

# provider별 환경변수 잠금 — 전체 앱에서 공유되는 단일 인스턴스
# core/agent.py, core/workflow_generator.py 등 os.environ 임시 주입이 필요한
# 모든 모듈은 이 객체를 import하여 사용한다.
_env_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def get_env_lock(env_key: str) -> asyncio.Lock:
    return _env_locks[env_key]

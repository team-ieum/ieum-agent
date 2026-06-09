"""노드 템플릿 파일 SSOT를 MongoDB node_templates 컬렉션으로 수동/CI 동기화한다.

사용:
    python -m scripts.seed_node_templates

파일(ieum-workflow-design/templates/*.json)이 진실의 원천이며, 스키마/드리프트 검증을
통과한 경우에만 upsert + stale 삭제를 수행한다(멱등).
"""
import asyncio
import logging

from db.mongodb import seed_node_templates

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s - %(message)s")


async def _main() -> None:
    result = await seed_node_templates()
    print(f"동기화 완료: {result}")


if __name__ == "__main__":
    asyncio.run(_main())

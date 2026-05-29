import asyncio
from motor.motor_asyncio import AsyncIOMotorClient

async def fetch_recent_logs():
    client = AsyncIOMotorClient("mongodb://ieum:ieum@localhost:27017/ieum?authSource=admin")
    db = client["ieum"]
    logs = db["generate_workflow_logs"]
    
    # 최근 5개 로그 가져오기
    cursor = logs.find().sort("createdAt", -1).limit(5)
    async for doc in cursor:
        print("="*60)
        for k, v in doc.items():
            print(f"{k}: {v}")
        print("="*60)

if __name__ == "__main__":
    asyncio.run(fetch_recent_logs())

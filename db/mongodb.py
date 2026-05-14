from motor.motor_asyncio import AsyncIOMotorClient
from core.config import settings

client = AsyncIOMotorClient(settings.MONGODB_URL)
db = client[settings.MONGODB_DB_NAME]

execution_logs = db["execution_logs"]
generate_workflow_logs = db["generate_workflow_logs"]

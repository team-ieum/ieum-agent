from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    MONGODB_URL: str
    MONGODB_DB_NAME: str = "ieum"
    PORT: int = 8000

    class Config:
        env_file = ".env"

settings = Settings()

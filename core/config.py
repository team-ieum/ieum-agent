from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # MongoDB
    MONGODB_URL: str
    MONGODB_DB_NAME: str
    PORT: int

    # Provider 기본 모델명 (필수 — .env에서 설정)
    CLAUDE_DEFAULT_MODEL: str
    OPENAI_DEFAULT_MODEL: str
    GEMINI_DEFAULT_MODEL: str

    # 지원 프로바이더 목록
    SUPPORTED_PROVIDERS: list[str] = ["CLAUDE", "OPENAI", "GEMINI"]

    class Config:
        env_file = ".env"

settings = Settings()

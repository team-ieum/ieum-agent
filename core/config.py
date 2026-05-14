from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # MongoDB
    MONGODB_URL: str
    MONGODB_DB_NAME: str = "ieum"
    PORT: int = 8000

    # Provider 기본 모델명 (.env에서 오버라이드 가능)
    CLAUDE_DEFAULT_MODEL: str = "claude-sonnet-4-20250514"
    OPENAI_DEFAULT_MODEL: str = "gpt-4o"
    GEMINI_DEFAULT_MODEL: str = "gemini-2.5-flash"

    # Provider 환경변수 키
    CLAUDE_ENV_KEY: str = "ANTHROPIC_API_KEY"
    OPENAI_ENV_KEY: str = "OPENAI_API_KEY"
    GEMINI_ENV_KEY: str = "GOOGLE_API_KEY"

    # 지원 프로바이더 목록
    SUPPORTED_PROVIDERS: list[str] = ["CLAUDE", "OPENAI", "GEMINI"]

    class Config:
        env_file = ".env"

settings = Settings()

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv()

class Settings(BaseSettings):
    # MongoDB
    MONGODB_URL: str
    MONGODB_DB_NAME: str = "ieum"
    PORT: int = 8000

    # Provider 기본 모델명 (.env에서 오버라이드 가능)
    CLAUDE_DEFAULT_MODEL: str = "claude-sonnet-4-20250514"
    OPENAI_DEFAULT_MODEL: str = "gpt-4o"
    GEMINI_DEFAULT_MODEL: str = "gemini-2.5-pro"

    # 지원 프로바이더 목록
    SUPPORTED_PROVIDERS: list[str] = ["CLAUDE", "OPENAI", "GEMINI"]

    # Notion MCP (Streamable HTTP 모드로 self-host한 @notionhq/notion-mcp-server 엔드포인트)
    # stdio(npx) 방식은 컨테이너에 Node가 없어 실패하므로 HTTP 연결로 전환했다.
    # 유저별 토큰은 매 요청 Notion-Token 헤더로 passthrough 되며, 이 URL은 MCP 서버 위치만 가리킨다.
    NOTION_MCP_URL: str = "http://localhost:3000/mcp"

    # 자체 호스팅 LLM 분기 (개발/테스트 계정용)
    # 이 role 집합에 속한 유저가 API 키를 등록하지 않았을 때, 자체 파인튜닝 LLM(OpenAI 호환 엔드포인트)으로 라우팅된다.
    # SELF_HOSTED_LLM_BASE_URL이 비어 있으면 비활성화되어 전원 등록 API 키 경로로 동작한다(모델 준비 전 안전 기본값).
    SELF_HOSTED_ALLOWED_ROLES: set[str] = {"ROLE_ADMIN", "ROLE_TESTER"}
    SELF_HOSTED_LLM_BASE_URL: str = ""   # 예: http://localhost:8001/v1 (vLLM/Ollama 등 OpenAI 호환). 비어 있으면 비활성.
    SELF_HOSTED_LLM_MODEL: str = ""      # 서빙 중인 모델명
    SELF_HOSTED_LLM_API_KEY: str = ""    # 엔드포인트 인증 키 (불필요하면 빈 값)

    # 에이전트 실행 가드
    # react 도구 호출 루프 상한 (ADK max_llm_calls). 광범위 조회로 무한 페이징하는 것을 차단한다.
    AGENT_MAX_LLM_CALLS: int = 50
    # 에이전트 실행 시간 상한(초). backend WebClient 타임아웃(기본 180s)보다 짧게 두어
    # backend가 끊기 전에 agent가 정리된 에러를 반환하도록 한다.
    AGENT_TIMEOUT_SECONDS: int = 150

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()


def get_current_time_info() -> str:
    """한국 시간(KST) 기준 현재 날짜/시간 텍스트를 반환하여 에이전트에게 컨텍스트를 제공합니다."""
    from datetime import datetime, timezone, timedelta
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    day_of_week = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]
    return (
        f"\n\n[현재 시간 정보]\n"
        f"- 기준 날짜: {date_str} ({day_of_week}요일)\n"
        f"- 기준 시각: {time_str}\n"
        f"만약 오늘 날짜나 특정 일자가 필요하다면 이 정보를 활용하십시오."
    )

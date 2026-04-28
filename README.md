# ieum-agent# ieum-agent

IEUM 프로젝트의 AI 노드 실행 전담 서비스입니다.
FastAPI + Google ADK + MongoDB로 구성되며,
Spring Boot 백엔드에서 AI 노드 실행 시 HTTP 위임을 받아 처리합니다.

## 기술 스택
- Python 3.11+
- FastAPI
- Google ADK
- MongoDB (motor)
- AI Provider: Claude, OpenAI, Gemini

## 로컬 실행

\```bash
cp .env.example .env
# .env 파일에 값 입력

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

uvicorn main:app --reload
\```

## API

POST /v1/execute
- Header: X-LLM-Provider
- Header: X-LLM-Api-Key
- Body: AgentNodeRequest

GET /health
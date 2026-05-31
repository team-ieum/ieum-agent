import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient

# ANSI Color constants for beautiful terminal printing
BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
MAGENTA = "\033[95m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

async def watch_sessions(mongodb_url: str, db_name: str):
    client = AsyncIOMotorClient(mongodb_url)
    db = client[db_name]
    collection = db["agent_sessions"]
    
    print(f"{GREEN}{BOLD}=== IEUM LLM Real-time Event Monitor Started ==={RESET}")
    print(f"Monitoring DB: {db_name} / Collection: agent_sessions")
    print("Waiting for new LLM interactions...\n" + "="*80)
    
    # 세션별로 출력한 마지막 이벤트의 인덱스를 저장
    printed_events = {}

    while True:
        try:
            # 최근 1분 이내에 업데이트되었거나 활동 중인 세션 3개 조회
            cursor = collection.find().sort("updatedAt", -1).limit(3)
            async for doc in cursor:
                session_id = doc.get("id")
                events = doc.get("events", [])
                
                if not session_id or not events:
                    continue
                
                # 처음 보는 세션인 경우, 현재 등록되어 있는 이벤트 개수 기록하고 통과 (이후 이벤트부터 모니터링)
                if session_id not in printed_events:
                    printed_events[session_id] = len(events)
                    continue
                
                last_index = printed_events[session_id]
                if len(events) > last_index:
                    new_events = events[last_index:]
                    for event in new_events:
                        author = event.get("author", "unknown").upper()
                        parts = event.get("content", {}).get("parts", [])
                        
                        # 타임스탬프 파싱
                        time_str = datetime.now().strftime("%H:%M:%S")
                        
                        print(f"\n{BOLD}[{time_str}] Session: {session_id} | Author: {author}{RESET}")
                        for p in parts:
                            # 1. 일반 텍스트 (LLM의 생각이나 최종 답변)
                            if "text" in p and p["text"].strip():
                                text = p["text"].strip()
                                # ReAct 모드 등에서 나오는 내부 생각
                                if "Thought:" in text:
                                    print(f"{YELLOW}💭 LLM Thought:{RESET}\n{text}")
                                else:
                                    print(f"{BLUE}✉️ LLM Response:{RESET}\n{text}")
                            
                            # 2. 도구 호출 요청
                            if "function_call" in p:
                                call = p["function_call"]
                                print(f"{MAGENTA}🛠️ Tool Call Request:{RESET} {BOLD}{call.get('name')}{RESET}")
                                args = call.get("args")
                                if args:
                                    print(f"   Args: {args}")
                            
                            # 3. 도구 실행 결과 응답
                            if "function_response" in p:
                                resp = p["function_response"]
                                print(f"{CYAN}📥 Tool Response Received:{RESET} {BOLD}{resp.get('name')}{RESET}")
                                response_val = resp.get("response")
                                # 너무 길 경우 일부만 출력
                                resp_str = str(response_val)
                                if len(resp_str) > 800:
                                    resp_str = resp_str[:800] + f"\n... (truncated, total {len(resp_str)} chars)"
                                print(f"   Output: {resp_str}")
                        
                        print("-" * 80)
                    
                    printed_events[session_id] = len(events)
                    
            await asyncio.sleep(1.0)
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"{RED}Error in log watcher: {e}{RESET}")
            await asyncio.sleep(2.0)

async def main():
    # ieum-agent root 경로 추가하여 settings 로드
    agent_path = "/Users/dobee/WorkSpace/IEUMWorkSpace/ieum-agent"
    if agent_path not in sys.path:
        sys.path.append(agent_path)
        
    try:
        from core.config import settings
        mongodb_url = settings.MONGODB_URL
        db_name = settings.MONGODB_DB_NAME
    except ImportError:
        # fallback
        mongodb_url = os.environ.get("MONGODB_URL", "mongodb://localhost:27017")
        db_name = os.environ.get("MONGODB_DB_NAME", "ieum")

    await watch_sessions(mongodb_url, db_name)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{GREEN}Watcher stopped.{RESET}")

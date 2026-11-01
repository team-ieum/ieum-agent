import pytest
from core.execution_guard import ExecutionGuard, ExecutionGuardError
from core.output_validator import OutputValidator
from api.schemas.request import AgentNodeRequest

# --- 1. ExecutionGuard 단위 테스트 ---

def test_execution_guard_safe_url_passes():
    # 외부 정상 도메인은 패스해야 함
    req = AgentNodeRequest(
        nodeId="node-2",
        agentType="react",
        model="gemini-2.5-flash",
        renderedPrompt="테스트",
        tools=[{
            "name": "builtin:http_fetch",
            "arguments": {"url": "https://api.github.com/repos"}
        }]
    )
    # 예외 없이 통과
    ExecutionGuard.validate_execution(req)

def test_execution_guard_localhost_raises():
    # localhost 직접 차단
    req = AgentNodeRequest(
        nodeId="node-2",
        agentType="react",
        model="gemini-2.5-flash",
        renderedPrompt="테스트",
        tools=[{
            "name": "builtin:http_fetch",
            "arguments": {"url": "http://localhost:8000/admin"}
        }]
    )
    with pytest.raises(ExecutionGuardError) as excinfo:
        ExecutionGuard.validate_execution(req)
    assert "사설망 또는 로컬호스트 주소" in str(excinfo.value)

def test_execution_guard_private_ip_raises():
    # 192.168 대역 차단
    req = AgentNodeRequest(
        nodeId="node-2",
        agentType="react",
        model="gemini-2.5-flash",
        renderedPrompt="테스트",
        tools=[{
            "name": "builtin:http_fetch",
            "arguments": {"url": "http://192.168.1.100/health"}
        }]
    )
    with pytest.raises(ExecutionGuardError) as excinfo:
        ExecutionGuard.validate_execution(req)
    assert "사설망 또는 로컬호스트 주소" in str(excinfo.value)

def test_execution_guard_tools_http_fetch_safety_raises():
    # AI 노드의 http_fetch 도구 내부 URL 차단
    req = AgentNodeRequest(
        nodeId="node-2",
        agentType="react",
        model="gemini-2.5-flash",
        renderedPrompt="테스트",
        tools=[{
            "name": "builtin:http_fetch",
            "arguments": {"url": "http://127.0.0.1:27017"}
        }]
    )
    with pytest.raises(ExecutionGuardError) as excinfo:
        ExecutionGuard.validate_execution(req)
    assert "로컬호스트 주소" in str(excinfo.value)

def test_execution_guard_missing_notion_token_raises():
    # notion 툴 사용하는데 토큰 누락
    req = AgentNodeRequest(
        nodeId="node-2",
        agentType="react",
        model="gemini-2.5-flash",
        renderedPrompt="테스트",
        tools=[{"name": "builtin:notion_create_page"}]
    )
    with pytest.raises(ExecutionGuardError) as excinfo:
        ExecutionGuard.validate_execution(req, notion_token=None)
    assert "Notion API Token이 주입되지 않았습니다" in str(excinfo.value)

def test_execution_guard_nested_config_url_raises():
    # config 내부 깊숙이 숨어 있는 사설 대역 URL 차단
    req = AgentNodeRequest(
        nodeId="node-2",
        agentType="react",
        model="gemini-2.5-flash",
        renderedPrompt="테스트",
        tools=[{
            "name": "custom:mcp_tool",
            "config": {
                "connection": {
                    "endpoint_url": "http://127.0.0.1:8080/api"
                }
            }
        }]
    )
    with pytest.raises(ExecutionGuardError) as excinfo:
        ExecutionGuard.validate_execution(req)
    assert "사설망 또는 로컬호스트 주소" in str(excinfo.value)


# --- 2. OutputValidator 단위 테스트 ---

def test_output_validator_token_masking():
    raw_text = "Here is Google ya29.aBc123XyZ and Notion secret_12345secret and GitHub ghp_myGitTokenSecret."
    
    # 1) 응답용 마스킹 (strict = False)
    masked_res = OutputValidator.mask_response_content(raw_text)
    assert "ya29.aBc123XyZ" not in masked_res
    assert "secret_12345secret" not in masked_res
    assert "ghp_myGitTokenSecret" not in masked_res
    assert "[MASKED_GOOGLE_TOKEN]" in masked_res
    assert "[MASKED_NOTION_TOKEN]" in masked_res
    assert "[MASKED_GITHUB_TOKEN]" in masked_res

def test_output_validator_github_token_variants_masking():
    # ghp_ 외 gho_/ghu_/ghs_/github_pat_ 포맷도 모두 마스킹되어야 함
    raw_text = (
        "tokens: ghp_classic111 gho_oauth222 ghu_user333 ghs_server444 "
        "github_pat_fineGrained_555AAA"
    )
    masked = OutputValidator.mask_response_content(raw_text)
    for tok in [
        "ghp_classic111", "gho_oauth222", "ghu_user333",
        "ghs_server444", "github_pat_fineGrained_555AAA",
    ]:
        assert tok not in masked
    assert "[MASKED_GITHUB_TOKEN]" in masked


def test_output_validator_slack_token_masking():
    # 봇(xoxb-), refresh(xoxe-), 회전 봇 토큰(xoxe.xoxb-), 사용자(xoxp-) — 응답·로그 마스킹 둘 다
    raw_text = "bot xoxb-1-AbC9 refresh xoxe-1-XyZ8 rotated xoxe.xoxb-1-RoT7 user xoxp-9-Qq6"
    for masked in (OutputValidator.mask_response_content(raw_text), OutputValidator.mask_log_content(raw_text)):
        for secret in ("xoxb-1-AbC9", "xoxe-1-XyZ8", "xoxb-1-RoT7", "xoxp-9-Qq6"):
            assert secret not in masked
        assert "[MASKED_SLACK_TOKEN]" in masked


DISCORD_BOT_TOKEN = "MTIzNDU2Nzg5MDEyMzQ1Njc4OTA" + ".GAbC-d." + "x9_Yz" * 8   # <id base64 26>.<6>.<40>


def test_output_validator_discord_token_masking():
    raw_text = f"Authorization: Bot {DISCORD_BOT_TOKEN}\nheaders={{'Authorization': 'Bot {DISCORD_BOT_TOKEN}'}}"
    for masked in (OutputValidator.mask_response_content(raw_text), OutputValidator.mask_log_content(raw_text)):
        assert DISCORD_BOT_TOKEN not in masked and "[MASKED_DISCORD_TOKEN]" in masked
    # 메시지 링크·웹훅 URL·평범한 점 문장은 그대로
    for keep in ("https://discord.com/channels/111111111111111111/333333333333333333/444444444444444444",
                 "release v1.2.3 notes", "a.b.c"):
        assert OutputValidator.mask_response_content(keep) == keep


@pytest.mark.parametrize("text", ["A" * 1_000_000, ("A" * 30 + ".") * 30_000, ("A" * 30 + ".AAAAAA") * 30_000])
def test_output_validator_긴_영숫자_문자열도_1초_안에_끝난다(text):
    """lookbehind 없는 Discord 패턴은 점 없는 긴 문자열에서 길이의 제곱이다 — 1MB Drive 읽기 하나로 이벤트 루프가 멈춘다.
    4만 자 앞부분을 먼저 잰다 — lookbehind가 빠지면 여기서 수 초로 실패한다(1MB부터 재면 실패 대신 수십 분 멈춘다)."""
    import time
    for sample in (text[:40_000], text):
        t = time.monotonic()
        OutputValidator.mask_response_content(sample)
        OutputValidator.mask_log_content(sample)
        assert time.monotonic() - t < 1, len(sample)


def test_output_validator_bearer_masking():
    raw_text = "Authorization: Bearer mySecretTokenValue123!!"
    masked_res = OutputValidator.mask_response_content(raw_text)
    assert "mySecretTokenValue123!!" not in masked_res
    assert "Bearer [MASKED_BEARER_TOKEN]" in masked_res

def test_output_validator_strict_vs_soft_masking():
    # JSON 형태의 api_key 정의 구문
    raw_text = '{"api_key": "superSecretKey123", "data": "normalValue"}'
    
    # 1) 소프트 마스킹 (클라이언트 응답용) ➡️ JSON 구조 보존을 위해 마스킹 건너뜀
    soft_res = OutputValidator.mask_response_content(raw_text)
    assert "superSecretKey123" in soft_res
    
    # 2) 스트릭트 마스킹 (로그용) ➡️ 중요 정보 차단
    strict_res = OutputValidator.mask_log_content(raw_text)
    assert "superSecretKey123" not in strict_res
    assert "[MASKED_KEY]" in strict_res

import httpx

from common.error_code import ErrorCode


async def send_discord_webhook(webhook_url: str, content: str, username: str = "IEUM Bot") -> str:
    """
    Discord Webhook으로 메시지를 발송합니다.

    Args:
        webhook_url: Discord Webhook URL
        content: 전송할 메시지 텍스트
        username: 봇 사용자명 (optional, 기본값: IEUM Bot)

    Returns:
        발송 성공 여부 메시지
    """
    try:
        payload = {"content": content, "username": username}

        async with httpx.AsyncClient() as client:
            response = await client.post(webhook_url, json=payload, timeout=10.0)
            response.raise_for_status()
            return "Discord 메시지 발송 성공"
    except httpx.HTTPStatusError as e:
        return f"{ErrorCode.TOOL_EXECUTION_FAILED.message} (Discord: HTTP {e.response.status_code})"
    except Exception as e:
        return f"{ErrorCode.TOOL_EXECUTION_FAILED.message} (Discord: {str(e)})"

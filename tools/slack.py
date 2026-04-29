import httpx


async def send_slack_message(webhook_url: str, message: str, channel: str = None) -> str:
    """
    Slack으로 메시지를 발송합니다.

    Args:
        webhook_url: Slack Incoming Webhook URL
        message: 발송할 메시지 텍스트
        channel: 채널명 (optional, Webhook 기본값 사용 시 생략)

    Returns:
        발송 성공 여부 메시지
    """
    try:
        payload = {"text": message}
        if channel:
            payload["channel"] = channel

        async with httpx.AsyncClient() as client:
            response = await client.post(webhook_url, json=payload, timeout=10.0)
            response.raise_for_status()
            return "Slack 메시지 발송 성공"
    except httpx.HTTPStatusError as e:
        return f"Slack 발송 실패: HTTP {e.response.status_code}"
    except Exception as e:
        return f"Slack 발송 실패: {str(e)}"

import json

import httpx

from common.error_code import ToolErrorCode
from tools.http_client import get_http_client


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

        client = get_http_client()
        response = await client.post(webhook_url, json=payload, timeout=10.0)
        response.raise_for_status()
        return json.dumps({"success": True, "message": "Slack 메시지 발송 성공"}, ensure_ascii=False)
    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"{ToolErrorCode.EXECUTION_FAILED.message} (Slack: HTTP {e.response.status_code})"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"{ToolErrorCode.EXECUTION_FAILED.message} (Slack: {str(e)})"}, ensure_ascii=False)

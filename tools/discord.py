import json

import httpx

from common.error_code import ToolErrorCode
from tools.http_client import get_http_client


async def send_discord_webhook(webhook_url: str, content: str, username: str = "IEUM Bot") -> str:
    """
    Discord Webhook으로 메시지를 발송합니다.

    Args:
        webhook_url: Discord Webhook URL
        content: 전송할 메시지 텍스트
        username: 봇 사용자명 (optional, 기본값: IEUM Bot)

    Returns:
        보낸 메시지의 id·channel_id를 담은 JSON 문자열
    """
    try:
        payload = {"content": content, "username": username}
        # wait=true여야 Discord가 204 대신 보낸 메시지 객체를 돌려준다(spec 2026-10-21 §8)
        # 기존 쿼리(thread_id)는 살리고 #fragment 앞에 넣는다 — 문자열로 이으면 fragment 안으로 들어가 전송되지 않는다
        url = str(httpx.URL(webhook_url).copy_merge_params({"wait": "true"}))

        client = get_http_client()
        response = await client.post(url, json=payload, timeout=10.0)
        response.raise_for_status()
        try:
            sent = response.json()
        except ValueError:  # 이미 보냈다 — 본문이 이상해도 실패로 돌리면 재시도가 메시지를 두 번 보낸다
            sent = {}
        sent = sent if isinstance(sent, dict) else {}
        return json.dumps({"success": True, "id": str(sent.get("id") or ""),
                           "channel_id": str(sent.get("channel_id") or "")}, ensure_ascii=False)
    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"{ToolErrorCode.EXECUTION_FAILED.message} (Discord: HTTP {e.response.status_code})"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"{ToolErrorCode.EXECUTION_FAILED.message} (Discord: {str(e)})"}, ensure_ascii=False)

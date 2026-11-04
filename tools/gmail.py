import base64
import json
import logging
from email.headerregistry import Address
from email.message import EmailMessage
from html.parser import HTMLParser
from typing import Optional

from tools.google_calendar import _blank, _emails, _fail, _headers
from tools.http_client import get_http_client

logger = logging.getLogger(__name__)

_GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
_TIMEOUT = 30.0
_SCOPE_HINT = "Google 연결에서 Gmail 권한을 켜 주세요"
_ALIAS_MISSING = "보낸 사람 주소를 Gmail 별칭에서 찾을 수 없어요"


def _header(value) -> str:
    """헤더 값의 줄바꿈을 공백으로 — 새 헤더(Bcc 등) 주입을 막는다."""
    return str(value).replace("\r", " ").replace("\n", " ")


class _SignatureText(HTMLParser):
    """Gmail 서명 HTML → 일반 텍스트. 줄바꿈 태그는 줄바꿈, 링크는 주소를 괄호로 살린다."""

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.hrefs: list[Optional[str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.parts.append("\n")
        elif tag == "a":
            self.hrefs.append(dict(attrs).get("href"))

    def handle_endtag(self, tag):
        if tag in ("div", "p"):
            self.parts.append("\n")
        elif tag == "a" and self.hrefs:
            href = self.hrefs.pop()
            if href:
                self.parts.append(f" ({href})")

    def handle_data(self, data):
        self.parts.append(data)


def _signature_text(html: str) -> str:
    parser = _SignatureText()
    parser.feed(html)
    parser.close()
    return "".join(parser.parts).strip()


def _api_error(response) -> str:
    try:
        message = response.json().get("error", {}).get("message", "알 수 없는 오류")
    except Exception:
        message = "알 수 없는 오류"
    text = f"Gmail API 오류 ({response.status_code}): {message}"
    if response.status_code == 403:
        text += f" — {_SCOPE_HINT}"
    return json.dumps({"error": text}, ensure_ascii=False)


async def gmail_send_email(
    access_token: str,
    *,
    to: Optional[str] = None,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    from_email: Optional[str] = None,
    from_name: Optional[str] = None,
    reply_to: Optional[str] = None,
    subject: str,
    body_format: str = "text/plain",
    body: str,
    include_signature: bool = False,
    label_ids: Optional[list[str]] = None,
) -> str:
    """연결한 Google 계정의 Gmail로 메일을 보낸다.

    Args:
        to: 받는 사람(쉼표 구분 또는 목록)
        cc: 참조(쉼표 구분 또는 목록)
        bcc: 숨은 참조(쉼표 구분 또는 목록)
        from_email: 보낸 사람 주소(Gmail 별칭). 비우면 기본 주소
        from_name: 보낸 사람 이름
        reply_to: 답장 받을 주소
        subject: 제목
        body_format: "text/plain" 또는 "text/html"
        body: 본문
        include_signature: Gmail 서명 붙이기
        label_ids: 보낸 메일에 붙일 라벨 ID 목록

    Returns:
        성공 {"success": true, "messageId", "threadId"}, 실패 {"error"}
    """
    tool = "gmail_send_email"
    to_list, cc_list, bcc_list = ([] if _blank(v) else _emails(v) for v in (to, cc, bcc))
    labels = [] if _blank(label_ids) else _emails(label_ids)
    if isinstance(include_signature, str) and not _blank(include_signature):
        flag = include_signature.strip().lower()
        if flag not in ("true", "false"):
            return _fail(tool, "include_signature는 true 또는 false여야 합니다")
        include_signature = flag == "true"
    signed = False if _blank(include_signature) else bool(include_signature)
    from_email = None if _blank(from_email) else str(from_email).strip()
    from_name = None if _blank(from_name) else str(from_name).strip()
    if not (to_list or cc_list or bcc_list):
        return _fail(tool, "받는 사람(to·cc·bcc)이 하나는 있어야 합니다")

    try:
        client = get_http_client()
        headers = _headers(access_token)

        sender = None
        if from_email or from_name or signed:
            response = await client.get(f"{_GMAIL_API}/settings/sendAs", headers=headers, timeout=_TIMEOUT)
            if not response.is_success:
                return _api_error(response)
            aliases = response.json().get("sendAs", [])
            if from_email:
                wanted = from_email.casefold()
                sender = next((a for a in aliases
                               if str(a.get("sendAsEmail", "")).strip().casefold() == wanted
                               and a.get("verificationStatus") != "pending"), None)
                if sender is None:
                    return _fail(tool, _ALIAS_MISSING)
            else:
                sender = next((a for a in aliases if a.get("isDefault")), None)

        signature = sender.get("signature") if signed and sender else None
        if body_format == "text/html":
            if (signature or "").strip():  # 이미지만 있는 서명도 HTML엔 붙인다
                body = f"{body}<br>-- <br>{signature}"
        elif signature_text := (_signature_text(signature) if signature else ""):
            body = f"{body}\n-- \n{signature_text}"

        msg = EmailMessage()
        if to_list:
            msg["To"] = _header(", ".join(to_list))
        if cc_list:
            msg["Cc"] = _header(", ".join(cc_list))
        if bcc_list:
            msg["Bcc"] = _header(", ".join(bcc_list))
        # 보낸 사람 이름만 줬으면 기본 주소, 둘 다 비우면 From 없이(Gmail이 기본 주소로 채운다)
        if sender and (from_email or from_name):
            msg["From"] = Address(display_name=_header(from_name or sender.get("displayName") or ""),
                                  addr_spec=_header(str(sender["sendAsEmail"]).strip()))
        if not _blank(reply_to):
            msg["Reply-To"] = _header(reply_to)
        msg["Subject"] = _header(subject)
        msg.set_content(body, subtype="html" if body_format == "text/html" else "plain")
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

        response = await client.post(f"{_GMAIL_API}/messages/send", headers=headers,
                                     json={"raw": raw}, timeout=_TIMEOUT)
        if not response.is_success:
            return _api_error(response)
        sent = response.json()
        message_id, thread_id = sent.get("id"), sent.get("threadId")
    except Exception as e:
        return _fail(tool, str(e))

    # 라벨 실패를 실패로 돌리면 재시도가 메일을 두 번 보낸다 — 경고만 남기고 성공으로 둔다
    if labels:
        try:
            response = await client.post(f"{_GMAIL_API}/messages/{message_id}/modify", headers=headers,
                                         json={"addLabelIds": labels}, timeout=_TIMEOUT)
            if not response.is_success:
                logger.warning("Gmail 라벨 붙이기 실패 messageId=%s status=%s", message_id, response.status_code)
        except Exception as e:
            logger.warning("Gmail 라벨 붙이기 실패 messageId=%s error=%s", message_id, e)

    return json.dumps({"success": True, "messageId": message_id, "threadId": thread_id}, ensure_ascii=False)

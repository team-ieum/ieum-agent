import asyncio
import base64
import binascii
import email
import email.policy
import html
import json
import logging
import re
from datetime import datetime, timezone
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
_BAD_MESSAGE_ID = "Gmail 메일 ID가 아니에요"
_BAD_THREAD_ID = "Gmail 대화 ID가 아니에요"
_BAD_LABELS = "라벨을 1~100개 골라 주세요"
_FIND_DEFAULT = 20
_FIND_MAX = 100
_LABEL_MAX = 100
_BODY_TEXT_MAX = 10_000  # BE GmailMessageNormalizer.BODY_TEXT_MAX
_HTML_MAX = 200_000  # 태그 제거 전 원문 HTML 상한 — 거대한 메일 한 통이 agent를 붙잡지 않게
_HEADER_MAX = 1_000  # from·to·subject 자르기(받는 사람 수백 명·긴 제목)
_OUTPUT_MAX = 2_000_000  # 찾기·대화 items 전체 UTF-8 바이트 예산(최종 리뷰 I1 사용자 결정)


def _header(value) -> str:
    """헤더 값의 줄바꿈을 공백으로 — 새 헤더(Bcc 등) 주입을 막는다."""
    return " ".join(str(value).splitlines())


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


def _flag(value) -> Optional[bool]:
    """불리언 칸 — bool 또는 "true"/"false"(대소문자 무시), 비면 False. 그 밖의 문자열은 None."""
    if isinstance(value, str) and not _blank(value):
        return {"true": True, "false": False}.get(value.strip().lower())
    return False if _blank(value) else bool(value)


def _path_id(value) -> Optional[str]:
    """URL 경로에 넣을 메일·대화 ID. `../labels`·RFC Message-ID 같은 값이 다른 엔드포인트를 부르지 못하게 거른다."""
    text = "" if value is None else str(value).strip()
    return text if re.fullmatch(r"[A-Za-z0-9_-]+", text, re.ASCII) else None


def _label_list(value) -> Optional[list]:
    """라벨 칸(쉼표·목록·JSON 문자열) → 목록. 비었거나 100개를 넘으면 None."""
    labels = [] if _blank(value) else _emails(value)
    return labels if 0 < len(labels) <= _LABEL_MAX else None


# ── 메일 정규화(BE GmailMessageNormalizer 이식 — 트리거 출력과 같은 키) ──────────

def _find_part(part: dict, mime_type: str) -> Optional[str]:
    """DFS 첫 일치 파트의 본문. 깨진 base64 파트는 건너뛴다(BE와 같다)."""
    data = (part.get("body") or {}).get("data")
    if part.get("mimeType") == mime_type and data is not None:
        try:
            raw = base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)
        except (binascii.Error, ValueError):
            return None
        # ponytail: charset 헤더 무시(UTF-8 가정, BE와 같다) — 비UTF-8 메일은 대체 문자로
        return raw.decode("utf-8", errors="replace")
    for child in part.get("parts") or []:
        found = _find_part(child, mime_type)
        if found is not None:
            return found
    return None


_SPACE = " \t\n\x0b\f\r"  # 공백류 — BE(Java \s)와 같은 ASCII 집합
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_ENTITIES = (("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&amp;", "&"))


def _strip_html(html: str) -> str:
    """HTML 본문 → 텍스트. BE GmailMessageNormalizer와 같은 str.find 스캐너(orca-specs 2026-11-10-html-scanner-algo).
    정규식은 제곱 시간, stdlib HTMLParser는 Python 3.11.13 미만에서 제곱·예외(CVE-2025-6069)라 쓰지 않는다.
    검색은 앞으로만 간다 — 선형."""
    html = html[:_HTML_MAX]
    lower = html.translate(_ASCII_LOWER)  # str.lower()는 'İ'처럼 길이가 바뀌어 위치가 어긋난다
    parts: list[str] = []
    i, n = 0, len(html)
    while i < n:
        lt = html.find("<", i)
        if lt < 0:
            parts.append(html[i:])
            break
        parts.append(html[i:lt])
        gt = html.find(">", lt + 1)
        if gt < 0:  # 닫히지 않은 <는 끝까지 글자
            parts.append(html[lt:])
            break
        if gt == lt + 1:
            parts.append("<>")
            i = gt + 1
            continue
        raw = next((name for name in ("script", "style")
                    if lower.startswith(name, lt + 1)
                    and (lower[lt + 1 + len(name)] in _SPACE + ">/")), None)
        parts.append(" ")
        if raw is None:
            i = gt + 1
            continue
        close = lower.find("</" + raw, gt + 1)
        end = -1 if close < 0 else html.find(">", close)
        if end < 0:  # 닫는 태그가 없으면 나머지는 script·style 안이다
            break
        i = end + 1
    text = "".join(parts)
    for entity, char in _ENTITIES:
        text = text.replace(entity, char)
    return re.sub(f"[{re.escape(_SPACE)}]+", " ", text).strip(_SPACE)


def _received_at(internal_date) -> str:
    """epoch ms → Java Instant.toString 꼴(ms가 0이면 생략)."""
    ms = int(internal_date or 0)
    text = datetime.fromtimestamp(ms // 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{text}.{ms % 1000:03d}Z" if ms % 1000 else f"{text}Z"


def _normalize(message: dict) -> dict:
    """messages.get(format=full) → 트리거 출력 9키."""
    payload = message.get("payload") or {}
    headers: dict = {}
    for h in payload.get("headers") or []:
        headers.setdefault(str(h.get("name", "")).lower(), h.get("value", ""))
    plain = _find_part(payload, "text/plain")
    if plain is not None:
        body = plain.strip()
    else:
        html = _find_part(payload, "text/html")
        body = "" if html is None else _strip_html(html)
    return {
        "messageId": message.get("id", ""),
        "threadId": message.get("threadId", ""),
        "from": headers.get("from", "")[:_HEADER_MAX],
        "to": headers.get("to", "")[:_HEADER_MAX],
        "subject": headers.get("subject", "")[:_HEADER_MAX],
        "snippet": message.get("snippet", ""),
        "bodyText": body[:_BODY_TEXT_MAX],
        "receivedAt": _received_at(message.get("internalDate")),
        "labels": list(message.get("labelIds") or []),
    }


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
    labels = [] if _blank(label_ids) else _emails(label_ids)
    inputs = _send_inputs(tool, to=to, cc=cc, bcc=bcc, from_email=from_email, from_name=from_name,
                          include_signature=include_signature)
    if isinstance(inputs, str):
        return inputs
    try:
        client = get_http_client()
        headers = _headers(access_token)
        msg = await _mime(tool, client, headers, inputs, reply_to=reply_to, subject=subject,
                          body_format=body_format, body=body)
        if isinstance(msg, str):
            return msg
        sent = await _deliver(client, headers, msg)
        if isinstance(sent, str):
            return sent
    except Exception as e:
        return _fail(tool, str(e))
    await _label_after_send(client, headers, sent["messageId"], labels)
    return json.dumps({"success": True, "messageId": sent["messageId"], "threadId": sent["threadId"]},
                      ensure_ascii=False)


# ── 보내기 계열 공유 헬퍼(보내기·답장·전달·초안·찾고 없으면 보내기) ─────────────

_UPLOAD_SEND = "https://gmail.googleapis.com/upload/gmail/v1/users/me/messages/send"


def _send_inputs(tool: str, *, to, cc, bcc, from_email, from_name, include_signature,
                 need_recipient: bool = True):
    """보내기 칸 검사·정리(HTTP 없음). 성공 dict, 실패 error JSON."""
    to_list, cc_list, bcc_list = ([] if _blank(v) else _emails(v) for v in (to, cc, bcc))
    signed = _flag(include_signature)
    if signed is None:
        return _fail(tool, "include_signature는 true 또는 false여야 합니다")
    if need_recipient and not (to_list or cc_list or bcc_list):
        return _fail(tool, "받는 사람(to·cc·bcc)이 하나는 있어야 합니다")
    return {"to": to_list, "cc": cc_list, "bcc": bcc_list,
            "from_email": None if _blank(from_email) else str(from_email).strip(),
            "from_name": None if _blank(from_name) else str(from_name).strip(),
            "signed": signed}


async def _mime(tool: str, client, headers: dict, inputs: dict, *, reply_to, subject, body_format: str,
                body: str, tail: str = ""):
    """별칭·서명을 골라 메일을 만든다. 본문은 body + 서명 + tail. 실패면 error JSON."""
    from_email, from_name, signed = inputs["from_email"], inputs["from_name"], inputs["signed"]
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
    if inputs["to"]:
        msg["To"] = _header(", ".join(inputs["to"]))
    if inputs["cc"]:
        msg["Cc"] = _header(", ".join(inputs["cc"]))
    if inputs["bcc"]:
        msg["Bcc"] = _header(", ".join(inputs["bcc"]))
    # 보낸 사람 이름만 줬으면 기본 주소, 둘 다 비우면 From 없이(Gmail이 기본 주소로 채운다)
    if sender and (from_email or from_name):
        msg["From"] = Address(display_name=_header(from_name or sender.get("displayName") or ""),
                              addr_spec=_header(str(sender["sendAsEmail"]).strip()))
    if not _blank(reply_to):
        msg["Reply-To"] = _header(reply_to)
    if not _blank(subject):
        msg["Subject"] = _header(subject)
    msg.set_content(body + tail, subtype="html" if body_format == "text/html" else "plain")
    return msg


def _raw(msg: EmailMessage) -> str:
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


async def _deliver(client, headers: dict, msg: EmailMessage, *, thread_id: Optional[str] = None,
                   upload: bool = False, timeout: float = _TIMEOUT, content: Optional[bytes] = None):
    """발송 한 번. 2xx면 {messageId, threadId, labelIds}, 아니면 error JSON. 네트워크 예외는 올린다.
    content = 이미 만든 MIME 바이트(업로드 경로, 다시 직렬화하지 않는다)."""
    if upload:
        response = await client.post(_UPLOAD_SEND, headers={**headers, "Content-Type": "message/rfc822"},
                                     params={"uploadType": "media"},
                                     content=msg.as_bytes() if content is None else content, timeout=timeout)
    else:
        body = {"raw": _raw(msg)}
        if thread_id:
            body["threadId"] = thread_id
        response = await client.post(f"{_GMAIL_API}/messages/send", headers=headers, json=body, timeout=timeout)
    if not response.is_success:
        return _api_error(response)
    # 2xx면 메일은 이미 나갔다 — 이 뒤의 실패를 실패로 돌리면 재시도가 메일을 두 번 보낸다. 경고만 남기고 성공으로 둔다
    try:
        sent = response.json()
        return {"messageId": sent.get("id"), "threadId": sent.get("threadId"), "labelIds": sent.get("labelIds")}
    except Exception as e:
        logger.warning("Gmail 발송 응답 파싱 실패 error=%s", e)
        return {"messageId": "", "threadId": "", "labelIds": None}


async def _label_after_send(client, headers: dict, message_id, labels: list, timeout: float = _TIMEOUT):
    """보낸 메일에 라벨. 성공이면 modify 응답 labelIds, 그 밖은 None(경고만)."""
    if not (labels and message_id):
        return None
    try:
        response = await client.post(f"{_GMAIL_API}/messages/{message_id}/modify", headers=headers,
                                     json={"addLabelIds": labels}, timeout=timeout)
        if not response.is_success:
            logger.warning("Gmail 라벨 붙이기 실패 messageId=%s status=%s", message_id, response.status_code)
            return None
        return response.json().get("labelIds")
    except Exception as e:
        logger.warning("Gmail 라벨 붙이기 실패 messageId=%s error=%s", message_id, e)
        return None


_REPLY_HEADERS = ["Message-ID", "References", "Subject", "Reply-To", "From", "To", "Cc"]


async def _reply_target(tool: str, client, headers: dict, thread: str):
    """답장 대상 = 초안을 뺀 메일 중 내가 보낸 것(SENT)이 아닌 마지막 메일, 없으면 마지막 메일(Gmail UI 답장 기본)."""
    response = await client.get(f"{_GMAIL_API}/threads/{thread}", headers=headers,
                                params={"format": "metadata", "metadataHeaders": _REPLY_HEADERS}, timeout=_TIMEOUT)
    if not response.is_success:
        return _api_error(response)
    # threads.get은 대화 안의 내 초안도 돌려준다 — 초안에 답하면 In-Reply-To가 틀어진다
    messages = [m for m in response.json().get("messages") or [] if "DRAFT" not in (m.get("labelIds") or [])]
    if not messages:
        return _fail(tool, "답장할 메일이 대화에 없어요")
    target = next((m for m in reversed(messages) if "SENT" not in (m.get("labelIds") or [])), messages[-1])
    values: dict = {}
    for h in (target.get("payload") or {}).get("headers") or []:
        values.setdefault(str(h.get("name", "")).lower(), str(h.get("value") or ""))
    return {"message_id": values.get("message-id", ""), "references": values.get("references", ""),
            "subject": values.get("subject", ""), "reply_to": values.get("reply-to") or values.get("from", ""),
            "sent": "SENT" in (target.get("labelIds") or []), "to": values.get("to", ""), "cc": values.get("cc", "")}


def _reply_headers(msg: EmailMessage, target: dict) -> None:
    message_id = target["message_id"].strip()
    if message_id:
        msg["In-Reply-To"] = _header(message_id)
        msg["References"] = _header(f"{target['references']} {message_id}".strip())


def _re_subject(subject: str) -> str:
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


async def _reply_mime(tool: str, access_token: str, *, thread_id, to, cc, bcc, from_email, from_name, reply_to,
                      body_format: str, body: str, include_signature):
    """답장·답장 초안 공통 — (client, headers, thread, msg) 또는 error JSON."""
    thread = _path_id(thread_id)
    if thread is None:
        return _fail(tool, _BAD_THREAD_ID)
    inputs = _send_inputs(tool, to=to, cc=cc, bcc=bcc, from_email=from_email, from_name=from_name,
                          include_signature=include_signature, need_recipient=False)
    if isinstance(inputs, str):
        return inputs
    client = get_http_client()
    headers = _headers(access_token)
    target = await _reply_target(tool, client, headers, thread)
    if isinstance(target, str):
        return target
    if not (inputs["to"] or inputs["cc"] or inputs["bcc"]):
        # 내 메일에 답장하면 그 메일의 원래 받는 사람에게(Gmail 화면과 같음, 2026-11-10 사용자 결정)
        if target["sent"]:
            inputs["to"], inputs["cc"] = ([] if _blank(v) else [v] for v in (target["to"], target["cc"]))
        elif not _blank(target["reply_to"]):
            inputs["to"] = [target["reply_to"]]
        if not (inputs["to"] or inputs["cc"]):
            return _fail(tool, "답장 받을 주소를 원래 메일에서 찾지 못했어요")
    msg = await _mime(tool, client, headers, inputs, reply_to=reply_to, subject=_re_subject(target["subject"]),
                      body_format=body_format, body=body)
    if isinstance(msg, str):
        return msg
    _reply_headers(msg, target)
    return client, headers, thread, msg


async def _save_draft(client, headers: dict, msg: EmailMessage, thread: Optional[str] = None) -> str:
    message = {"raw": _raw(msg)}
    if thread:
        message["threadId"] = thread
    response = await client.post(f"{_GMAIL_API}/drafts", headers=headers, json={"message": message}, timeout=_TIMEOUT)
    if not response.is_success:
        return _api_error(response)
    data = response.json()
    saved = data.get("message") or {}
    return _ok(draftId=data.get("id", ""), messageId=saved.get("id", ""), threadId=saved.get("threadId", ""))


async def gmail_reply(
    access_token: str,
    *,
    thread_id: str,
    to: Optional[str] = None,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    from_email: Optional[str] = None,
    from_name: Optional[str] = None,
    reply_to: Optional[str] = None,
    body_format: str = "text/plain",
    body: str,
    include_signature: bool = False,
    label_ids: Optional[list[str]] = None,
) -> str:
    """Gmail 대화에 답장한다(같은 대화로 묶인다).

    Args:
        thread_id: 대화 ID
        to: 받는 사람. 셋 다 비우면 원래 메일의 답장 주소
        cc: 참조
        bcc: 숨은 참조
        from_email: 보낸 사람 주소(Gmail 별칭). 비우면 기본 주소
        from_name: 보낸 사람 이름
        reply_to: 답장 받을 주소
        body_format: "text/plain" 또는 "text/html"
        body: 본문
        include_signature: Gmail 서명 붙이기
        label_ids: 보낸 메일에 붙일 라벨 ID 목록

    Returns:
        성공 {"success": true, "messageId", "threadId"}, 실패 {"error"}
    """
    tool = "gmail_reply"
    labels = [] if _blank(label_ids) else _emails(label_ids)
    try:
        built = await _reply_mime(tool, access_token, thread_id=thread_id, to=to, cc=cc, bcc=bcc,
                                  from_email=from_email, from_name=from_name, reply_to=reply_to,
                                  body_format=body_format, body=body, include_signature=include_signature)
        if isinstance(built, str):
            return built
        client, headers, thread, msg = built
        sent = await _deliver(client, headers, msg, thread_id=thread)
        if isinstance(sent, str):
            return sent
    except Exception as e:
        return _fail(tool, str(e))
    await _label_after_send(client, headers, sent["messageId"], labels)
    return _ok(messageId=sent["messageId"], threadId=sent["threadId"])


async def gmail_create_reply_draft(
    access_token: str,
    *,
    thread_id: str,
    to: Optional[str] = None,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    from_email: Optional[str] = None,
    from_name: Optional[str] = None,
    reply_to: Optional[str] = None,
    body_format: str = "text/plain",
    body: str,
    include_signature: bool = False,
) -> str:
    """Gmail 대화에 답장 초안을 만든다(보내지 않는다).

    Args:
        thread_id: 대화 ID
        to: 받는 사람. 셋 다 비우면 원래 메일의 답장 주소
        cc: 참조
        bcc: 숨은 참조
        from_email: 보낸 사람 주소(Gmail 별칭). 비우면 기본 주소
        from_name: 보낸 사람 이름
        reply_to: 답장 받을 주소
        body_format: "text/plain" 또는 "text/html"
        body: 본문
        include_signature: Gmail 서명 붙이기

    Returns:
        성공 {"success": true, "draftId", "messageId", "threadId"}, 실패 {"error"}
    """
    tool = "gmail_create_reply_draft"
    try:
        built = await _reply_mime(tool, access_token, thread_id=thread_id, to=to, cc=cc, bcc=bcc,
                                  from_email=from_email, from_name=from_name, reply_to=reply_to,
                                  body_format=body_format, body=body, include_signature=include_signature)
        if isinstance(built, str):
            return built
        client, headers, thread, msg = built
        return await _save_draft(client, headers, msg, thread)
    except Exception as e:
        return _fail(tool, str(e))


async def gmail_create_draft(
    access_token: str,
    *,
    to: Optional[str] = None,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    from_email: Optional[str] = None,
    from_name: Optional[str] = None,
    reply_to: Optional[str] = None,
    subject: Optional[str] = None,
    body_format: str = "text/plain",
    body: Optional[str] = None,
    include_signature: bool = False,
) -> str:
    """Gmail에 새 초안을 만든다(보내지 않는다. 받는 사람·제목·본문 모두 비워도 된다).

    Args:
        to: 받는 사람(쉼표 구분 또는 목록)
        cc: 참조
        bcc: 숨은 참조
        from_email: 보낸 사람 주소(Gmail 별칭). 비우면 기본 주소
        from_name: 보낸 사람 이름
        reply_to: 답장 받을 주소
        subject: 제목
        body_format: "text/plain" 또는 "text/html"
        body: 본문
        include_signature: Gmail 서명 붙이기

    Returns:
        성공 {"success": true, "draftId", "messageId", "threadId"}, 실패 {"error"}
    """
    tool = "gmail_create_draft"
    inputs = _send_inputs(tool, to=to, cc=cc, bcc=bcc, from_email=from_email, from_name=from_name,
                          include_signature=include_signature, need_recipient=False)
    if isinstance(inputs, str):
        return inputs
    try:
        client = get_http_client()
        headers = _headers(access_token)
        msg = await _mime(tool, client, headers, inputs, reply_to=reply_to, subject=subject,
                          body_format=body_format, body="" if body is None else str(body))
        if isinstance(msg, str):
            return msg
        return await _save_draft(client, headers, msg)
    except Exception as e:
        return _fail(tool, str(e))


_GMAIL_MAX = 35_000_000  # Gmail 메일 크기 상한(업로드 경로)
_FORWARD_DEADLINE = 150.0  # 원본 받기~발송 전체 — BE→agent 180초보다 먼저 끝나야 BE 재시도가 두 번 보내지 않는다
_FORWARD_TIMEOUT = 120.0  # 전달의 요청 하나
_FORWARDED = "---------- Forwarded message ---------"


def _mb(n: float) -> str:
    return f"{n / 1_000_000:.1f}MB"


def _attachments(part) -> list:
    """message/rfc822는 통째로 하나(안으로 내려가지 않는다), multipart는 자식으로, 그 밖은 파일명이 있을 때만."""
    if part.get_content_type() == "message/rfc822":
        return [part]
    if part.is_multipart():
        return [a for child in part.iter_parts() for a in _attachments(child)]
    return [part] if part.get_filename() else []


def _part_text(part) -> str:
    """파트 본문. charset을 모르면(unknown-8bit 등) 전달 전체가 실패하지 않게 UTF-8로 읽는다."""
    try:
        return part.get_content()
    except LookupError:
        return (part.get_payload(decode=True) or b"").decode("utf-8", "replace")


def _forward_tail(original, as_html: bool) -> str:
    """전달 블록(보낸 사람·날짜·제목·받는 사람) + 원본 본문."""
    html_part = original.get_body(preferencelist=("html",))
    plain_part = original.get_body(preferencelist=("plain",))
    lines = [_FORWARDED] + [f"{name}: {original[name] or ''}" for name in ("From", "Date", "Subject", "To")]
    if as_html:
        head = "<br>".join(html.escape(line) for line in lines)
        if html_part is not None:
            content = _part_text(html_part)
        else:
            content = html.escape(_part_text(plain_part) if plain_part is not None else "").replace("\n", "<br>")
        return f"<br><br>{head}<br><br>{content}"
    if plain_part is not None:
        content = _part_text(plain_part)
    else:
        content = _strip_html(_part_text(html_part)) if html_part is not None else ""
    return "\n\n" + "\n".join(lines) + "\n\n" + content


def _parse_original(raw: str, as_html: bool):
    """원본 파싱 + 전달 블록(동기 CPU — 스레드에서 돈다). (원본, 원본 제목, 전달 블록)."""
    original = email.message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)),
                                        policy=email.policy.default)
    return original, str(original["Subject"] or ""), _forward_tail(original, as_html)


def _attach_and_serialize(msg: EmailMessage, original) -> bytes:
    """원본 첨부를 다시 붙이고 MIME 바이트를 한 번 만든다(동기 CPU — 스레드에서 돈다).
    파일명·Content-ID는 _header로 줄바꿈을 지운다 — 줄바꿈 든 파일명도 전달되고 새 헤더를 만들지 못한다."""
    for part in _attachments(original):
        filename = part.get_filename()
        options = {"filename": _header(filename) if filename else None,
                   "disposition": part.get_content_disposition() or "attachment"}
        if part["Content-ID"]:
            options["cid"] = _header(part["Content-ID"])  # 원본 HTML의 cid: 참조가 이어지게
        if part.get_content_type() == "message/rfc822":
            options["filename"] = options["filename"] or "message.eml"
            msg.add_attachment(part.get_payload(0), **options)
            continue
        if part.get_content_maintype() == "text" and part.get_content_charset():
            options["params"] = {"charset": _header(part.get_content_charset())}
        msg.add_attachment(part.get_payload(decode=True) or b"", part.get_content_maintype(),
                           part.get_content_subtype(), **options)
    return msg.as_bytes()


async def gmail_forward(
    access_token: str,
    *,
    message_id: str,
    to: Optional[str] = None,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    from_email: Optional[str] = None,
    from_name: Optional[str] = None,
    reply_to: Optional[str] = None,
    body_format: str = "text/plain",
    body: Optional[str] = None,
    include_signature: bool = False,
    label_ids: Optional[list[str]] = None,
) -> str:
    """Gmail 메일을 원본 첨부까지 전달한다(새 대화).

    Args:
        message_id: 전달할 메일 ID
        to: 받는 사람(쉼표 구분 또는 목록)
        cc: 참조
        bcc: 숨은 참조
        from_email: 보낸 사람 주소(Gmail 별칭). 비우면 기본 주소
        from_name: 보낸 사람 이름
        reply_to: 답장 받을 주소
        body_format: "text/plain" 또는 "text/html"
        body: 전달 블록 위에 덧붙일 말
        include_signature: Gmail 서명 붙이기
        label_ids: 보낸 메일에 붙일 라벨 ID 목록

    Returns:
        성공 {"success": true, "messageId", "threadId"}, 실패 {"error"}
    """
    tool = "gmail_forward"
    message = _path_id(message_id)
    if message is None:
        return _fail(tool, _BAD_MESSAGE_ID)
    labels = [] if _blank(label_ids) else _emails(label_ids)
    inputs = _send_inputs(tool, to=to, cc=cc, bcc=bcc, from_email=from_email, from_name=from_name,
                          include_signature=include_signature)
    if isinstance(inputs, str):
        return inputs
    limit = f"{_GMAIL_MAX / 1_000_000:g}MB"
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _FORWARD_DEADLINE
    try:
        client = get_http_client()
        headers = _headers(access_token)
        async with asyncio.timeout_at(deadline):
            url = f"{_GMAIL_API}/messages/{message}"
            response = await client.get(url, headers=headers, params={"format": "minimal"}, timeout=_FORWARD_TIMEOUT)
            if not response.is_success:
                return _api_error(response)
            size = int(response.json().get("sizeEstimate") or 0)
            if size > _GMAIL_MAX:
                return _fail(tool, f"원본 메일이 {_mb(size)}라 Gmail 상한 {limit}를 넘어 전달할 수 없어요")
            response = await client.get(url, headers=headers, params={"format": "raw"}, timeout=_FORWARD_TIMEOUT)
            if not response.is_success:
                return _api_error(response)
            raw = str(response.json().get("raw") or "")
            # 수십 MB 파싱·조립·직렬화는 수 초 CPU — 스레드로 빼야 다른 요청이 멈추지 않고 마감도 끊을 수 있다
            original, subject, tail = await asyncio.to_thread(_parse_original, raw, body_format == "text/html")
            msg = await _mime(tool, client, headers, inputs, reply_to=reply_to,
                              subject=subject if subject.lower().startswith("fwd:") else f"Fwd: {subject}",
                              body_format=body_format, body="" if body is None else str(body), tail=tail)
            if isinstance(msg, str):
                return msg
            data = await asyncio.to_thread(_attach_and_serialize, msg, original)
            if len(data) > _GMAIL_MAX:
                return _fail(tool, f"전달할 메일이 원본 첨부까지 {_mb(len(data))}라 Gmail 상한 {limit}를 넘어 보낼 수 없어요")
            sent = await _deliver(client, headers, msg, upload=True, timeout=_FORWARD_TIMEOUT, content=data)
            if isinstance(sent, str):
                return sent
    except TimeoutError:
        return _fail(tool, f"원본 받기·보내기가 제한 시간 {_FORWARD_DEADLINE:g}초 안에 끝나지 않았어요")
    except Exception as e:
        return _fail(tool, str(e))
    # 라벨은 마감 밖(실패해도 성공)이지만 남은 시간 안에서만 — BE→agent 180초를 넘기면 BE 재시도가 두 번 보낸다
    remaining = deadline - loop.time()
    if labels and remaining <= 0:
        logger.warning("Gmail 전달 라벨 건너뜀(마감 지남) messageId=%s", sent["messageId"])
    else:
        try:
            async with asyncio.timeout_at(deadline):  # httpx timeout은 단계별이라 전체 상한이 아니다
                await _label_after_send(client, headers, sent["messageId"], labels,
                                        timeout=min(_TIMEOUT, remaining))
        except TimeoutError:
            logger.warning("Gmail 전달 라벨 붙이기 마감 초과 messageId=%s", sent["messageId"])
    return _ok(messageId=sent["messageId"], threadId=sent["threadId"])


async def gmail_find_or_send(
    access_token: str,
    *,
    query: str,
    search_label_ids: Optional[list[str]] = None,
    include_spam_trash: bool = False,
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
    """Gmail에서 검색어에 맞는 메일을 찾고, 없으면 메일을 보낸다.

    Args:
        query: Gmail 검색창 문법의 검색어
        search_label_ids: 찾을 때 이 라벨을 모두 가진 메일만(라벨 ID 목록)
        include_spam_trash: 찾을 때 스팸·휴지통도 보기
        to: 받는 사람(쉼표 구분 또는 목록)
        cc: 참조
        bcc: 숨은 참조
        from_email: 보낸 사람 주소(Gmail 별칭). 비우면 기본 주소
        from_name: 보낸 사람 이름
        reply_to: 답장 받을 주소
        subject: 제목
        body_format: "text/plain" 또는 "text/html"
        body: 본문
        include_signature: Gmail 서명 붙이기
        label_ids: 보낸 메일에 붙일 라벨 ID 목록

    Returns:
        성공 {"success", "created", 메일 9키}(찾았으면 created false), 실패 {"error"}
    """
    tool = "gmail_find_or_send"
    if _blank(query):
        return _fail(tool, "검색어를 입력하세요")
    labels = [] if _blank(label_ids) else _emails(label_ids)
    inputs = _send_inputs(tool, to=to, cc=cc, bcc=bcc, from_email=from_email, from_name=from_name,
                          include_signature=include_signature)
    if isinstance(inputs, str):
        return inputs
    # 1건만 본다 — 찾기 100건은 Google 호출 101회
    found = await gmail_find_email(access_token, query=query, label_ids=search_label_ids,
                                   include_spam_trash=include_spam_trash, max_results=1)
    data = json.loads(found)
    if "error" in data:
        return found
    if data["items"]:
        return _ok(created=False, **data["items"][0])

    body = "" if body is None else str(body)
    try:
        client = get_http_client()
        headers = _headers(access_token)
        msg = await _mime(tool, client, headers, inputs, reply_to=reply_to, subject=subject,
                          body_format=body_format, body=body)
        if isinstance(msg, str):
            return msg
        sent = await _deliver(client, headers, msg)
        if isinstance(sent, str):
            return sent
    except Exception as e:
        return _fail(tool, str(e))
    applied = await _label_after_send(client, headers, sent["messageId"], labels)
    # 보낸 메일을 다시 조회하지 않고 보낸 값으로 채운다(호출 1회 절약)
    text = _strip_html(body) if body_format == "text/html" else body
    return _ok(**{
        "created": True,
        "messageId": sent["messageId"],
        "threadId": sent["threadId"],
        "from": str(msg["From"] or "")[:_HEADER_MAX],
        "to": str(msg["To"] or "")[:_HEADER_MAX],
        "subject": str(msg["Subject"] or "")[:_HEADER_MAX],
        "snippet": "",
        "bodyText": text[:_BODY_TEXT_MAX],
        "receivedAt": _received_at(int(datetime.now(timezone.utc).timestamp() * 1000)),
        "labels": applied or sent["labelIds"] or [],
    })


def _json_size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _fit(items: list) -> list:
    """items 누적 크기가 2MB를 넘으면 그 항목의 bodyText를 남은 예산만큼 자르고, 그 뒤 항목은 bodyText를 비운다
    (항목 수·키 불변, 앞 항목이 본문을 갖고 뒤 항목이 비는 순서가 뒤집히지 않는다)."""
    used, full = 0, False
    for item in items:
        if full:
            item["bodyText"] = ""
            continue
        size = _json_size(item)
        if used + size > _OUTPUT_MAX:
            full = True
            # 본문 JSON(따옴표 2바이트 포함)을 뺀 나머지는 그대로 — 원문은 room - 2바이트까지
            room = _OUTPUT_MAX - used - (size - _json_size(item["bodyText"])) - 2
            body = item["bodyText"].encode("utf-8")[:max(room, 0)].decode("utf-8", errors="ignore")
            item["bodyText"] = body if _json_size(body) - 2 <= room else ""  # 이스케이프로 커지면 비운다
            size = _json_size(item)
        used += size
    return items


def _ok(**fields) -> str:
    return json.dumps({"success": True, **fields}, ensure_ascii=False)


async def gmail_find_email(
    access_token: str,
    *,
    query: str,
    label_ids: Optional[list[str]] = None,
    include_spam_trash: bool = False,
    max_results: int = _FIND_DEFAULT,
) -> str:
    """Gmail에서 검색어에 맞는 메일을 최근 순으로 찾는다.

    Args:
        query: Gmail 검색창 문법의 검색어
        label_ids: 이 라벨을 모두 가진 메일만(라벨 ID 목록)
        include_spam_trash: 스팸·휴지통도 찾기
        max_results: 최대 개수(기본 20, 최대 100)

    Returns:
        성공 {"success", "found", "total", "items"}(0건도 성공), 실패 {"error"}
    """
    tool = "gmail_find_email"
    if _blank(query):
        return _fail(tool, "검색어를 입력하세요")
    if _blank(max_results):  # BE는 없는 참조를 ""로 보낸다
        limit = _FIND_DEFAULT
    else:
        try:
            limit = min(max(int(max_results), 1), _FIND_MAX)
        except (TypeError, ValueError):
            return _fail(tool, "최대 개수는 숫자여야 합니다")
    spam_trash = _flag(include_spam_trash)
    if spam_trash is None:
        return _fail(tool, "include_spam_trash는 true 또는 false여야 합니다")
    labels = [] if _blank(label_ids) else _emails(label_ids)

    try:
        client = get_http_client()
        headers = _headers(access_token)
        ids: list = []
        page_token = None
        while len(ids) < limit:
            params = {"q": str(query).strip(), "maxResults": limit - len(ids)}
            if labels:
                params["labelIds"] = labels
            if spam_trash:
                params["includeSpamTrash"] = "true"
            if page_token:
                params["pageToken"] = page_token
            response = await client.get(f"{_GMAIL_API}/messages", headers=headers, params=params, timeout=_TIMEOUT)
            if not response.is_success:
                return _api_error(response)
            page = response.json()
            ids += [m.get("id") for m in page.get("messages") or []]
            page_token = page.get("nextPageToken")
            if not page_token:
                break

        items = []
        for message_id in filter(None, map(_path_id, ids[:limit])):
            response = await client.get(f"{_GMAIL_API}/messages/{message_id}", headers=headers,
                                        params={"format": "full"}, timeout=_TIMEOUT)
            if response.status_code == 404:  # 목록과 상세 사이에 지워진 메일
                continue
            if not response.is_success:
                return _api_error(response)
            items.append(_normalize(response.json()))
    except Exception as e:
        return _fail(tool, str(e))
    return _ok(found=bool(items), total=len(items), items=_fit(items))


async def gmail_get_thread(access_token: str, *, thread_id: str) -> str:
    """Gmail 대화의 메일을 모두 가져온다.

    Args:
        thread_id: 대화 ID

    Returns:
        성공 {"success", "threadId", "total", "items"}, 실패 {"error"}
    """
    tool = "gmail_get_thread"
    thread = _path_id(thread_id)
    if thread is None:
        return _fail(tool, _BAD_THREAD_ID)
    try:
        response = await get_http_client().get(f"{_GMAIL_API}/threads/{thread}", headers=_headers(access_token),
                                               params={"format": "full"}, timeout=_TIMEOUT)
        if not response.is_success:
            return _api_error(response)
        items = [_normalize(m) for m in response.json().get("messages") or []]
    except Exception as e:
        return _fail(tool, str(e))
    return _ok(threadId=thread, total=len(items), items=_fit(items))


async def _post(tool: str, access_token: str, url: str, body: Optional[dict] = None):
    """POST 한 번 — 성공이면 응답 dict, 실패면 error JSON 문자열."""
    try:
        response = await get_http_client().post(url, headers=_headers(access_token), json=body, timeout=_TIMEOUT)
        if not response.is_success:
            return _api_error(response)
        return response.json()
    except Exception as e:
        return _fail(tool, str(e))


async def _modify_message(tool: str, access_token: str, message_id, body: dict) -> str:
    message = _path_id(message_id)
    if message is None:
        return _fail(tool, _BAD_MESSAGE_ID)
    data = await _post(tool, access_token, f"{_GMAIL_API}/messages/{message}/modify", body)
    if isinstance(data, str):
        return data
    return _ok(messageId=data.get("id", message), threadId=data.get("threadId", ""), labels=data.get("labelIds") or [])


async def gmail_add_label(access_token: str, *, message_id: str, label_ids: list[str]) -> str:
    """Gmail 메일에 라벨을 붙인다(별표 = STARRED).

    Args:
        message_id: 메일 ID
        label_ids: 붙일 라벨 ID 목록(최대 100개)

    Returns:
        성공 {"success", "messageId", "threadId", "labels"}, 실패 {"error"}
    """
    labels = _label_list(label_ids)
    if labels is None:
        return _fail("gmail_add_label", _BAD_LABELS)
    return await _modify_message("gmail_add_label", access_token, message_id, {"addLabelIds": labels})


async def gmail_remove_label(access_token: str, *, message_id: str, label_ids: list[str]) -> str:
    """Gmail 메일에서 라벨을 뗀다(읽음 처리 = UNREAD 떼기).

    Args:
        message_id: 메일 ID
        label_ids: 뗄 라벨 ID 목록(최대 100개)

    Returns:
        성공 {"success", "messageId", "threadId", "labels"}, 실패 {"error"}
    """
    labels = _label_list(label_ids)
    if labels is None:
        return _fail("gmail_remove_label", _BAD_LABELS)
    return await _modify_message("gmail_remove_label", access_token, message_id, {"removeLabelIds": labels})


async def gmail_remove_thread_label(access_token: str, *, thread_id: str, label_ids: list[str]) -> str:
    """Gmail 대화의 모든 메일에서 라벨을 뗀다.

    Args:
        thread_id: 대화 ID
        label_ids: 뗄 라벨 ID 목록(최대 100개)

    Returns:
        성공 {"success", "threadId"}, 실패 {"error"}
    """
    tool = "gmail_remove_thread_label"
    thread = _path_id(thread_id)
    if thread is None:
        return _fail(tool, _BAD_THREAD_ID)
    labels = _label_list(label_ids)
    if labels is None:
        return _fail(tool, _BAD_LABELS)
    data = await _post(tool, access_token, f"{_GMAIL_API}/threads/{thread}/modify", {"removeLabelIds": labels})
    return data if isinstance(data, str) else _ok(threadId=data.get("id", thread))


async def gmail_archive(access_token: str, *, message_id: str) -> str:
    """Gmail 메일을 받은편지함에서 빼서 보관한다.

    Args:
        message_id: 메일 ID

    Returns:
        성공 {"success", "messageId", "threadId", "labels"}, 실패 {"error"}
    """
    return await _modify_message("gmail_archive", access_token, message_id, {"removeLabelIds": ["INBOX"]})


async def gmail_trash(access_token: str, *, message_id: str) -> str:
    """Gmail 메일을 휴지통으로 옮긴다(30일 안에 되돌릴 수 있다. 이미 휴지통이어도 성공).

    Args:
        message_id: 메일 ID

    Returns:
        성공 {"success", "messageId", "threadId"}, 실패 {"error"}
    """
    tool = "gmail_trash"
    message = _path_id(message_id)
    if message is None:
        return _fail(tool, _BAD_MESSAGE_ID)
    data = await _post(tool, access_token, f"{_GMAIL_API}/messages/{message}/trash")
    return data if isinstance(data, str) else _ok(messageId=data.get("id", message), threadId=data.get("threadId", ""))


async def gmail_create_label(
    access_token: str,
    *,
    name: str,
    label_list_visibility: Optional[str] = None,
    message_list_visibility: Optional[str] = None,
    text_color: Optional[str] = None,
    background_color: Optional[str] = None,
) -> str:
    """Gmail에 새 라벨을 만든다.

    Args:
        name: 라벨 이름('상위/하위'면 하위 라벨)
        label_list_visibility: 라벨 목록 표시(labelShow·labelShowIfUnread·labelHide)
        message_list_visibility: 메일 목록 표시(show·hide)
        text_color: 글자색 hex(배경색과 함께)
        background_color: 배경색 hex(글자색과 함께)

    Returns:
        성공 {"success", "labelId", "name"}, 실패 {"error"}(같은 이름이면 Google 409)
    """
    tool = "gmail_create_label"
    if _blank(name):
        return _fail(tool, "라벨 이름을 입력하세요")
    text, background = (None if _blank(c) else str(c).strip() for c in (text_color, background_color))
    if (text is None) != (background is None):
        return _fail(tool, "글자색과 배경색은 둘 다 고르거나 둘 다 비워 주세요")
    body = {"name": str(name).strip()}
    for key, value in (("labelListVisibility", label_list_visibility), ("messageListVisibility", message_list_visibility)):
        if not _blank(value):
            body[key] = str(value).strip()
    if text:
        body["color"] = {"textColor": text, "backgroundColor": background}
    data = await _post(tool, access_token, f"{_GMAIL_API}/labels", body)
    return data if isinstance(data, str) else _ok(labelId=data.get("id", ""), name=data.get("name", body["name"]))

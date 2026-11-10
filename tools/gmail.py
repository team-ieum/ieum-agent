import base64
import binascii
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


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"<[^>]+>", " ", text)
    for entity, char in (("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&amp;", "&")):
        text = text.replace(entity, char)
    return re.sub(r"\s+", " ", text).strip()


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
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
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
    to_list, cc_list, bcc_list = ([] if _blank(v) else _emails(v) for v in (to, cc, bcc))
    labels = [] if _blank(label_ids) else _emails(label_ids)
    signed = _flag(include_signature)
    if signed is None:
        return _fail(tool, "include_signature는 true 또는 false여야 합니다")
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
    except Exception as e:
        return _fail(tool, str(e))

    # 2xx면 메일은 이미 나갔다 — 이 뒤의 실패를 실패로 돌리면 재시도가 메일을 두 번 보낸다. 경고만 남기고 성공으로 둔다
    try:
        sent = response.json()
        message_id, thread_id = sent.get("id"), sent.get("threadId")
    except Exception as e:
        logger.warning("Gmail 발송 응답 파싱 실패 error=%s", e)
        message_id = thread_id = ""

    if labels and message_id:
        try:
            response = await client.post(f"{_GMAIL_API}/messages/{message_id}/modify", headers=headers,
                                         json={"addLabelIds": labels}, timeout=_TIMEOUT)
            if not response.is_success:
                logger.warning("Gmail 라벨 붙이기 실패 messageId=%s status=%s", message_id, response.status_code)
        except Exception as e:
            logger.warning("Gmail 라벨 붙이기 실패 messageId=%s error=%s", message_id, e)

    return json.dumps({"success": True, "messageId": message_id, "threadId": thread_id}, ensure_ascii=False)


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
    return _ok(found=bool(items), total=len(items), items=items)


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
    return _ok(threadId=thread, total=len(items), items=items)


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

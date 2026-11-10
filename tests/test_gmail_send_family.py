"""Gmail 답장·답장 초안·초안·전달·찾고 없으면 보내기(spec 2026-11-10 gmail-forms-9 §3). HTTP는 목으로 대신한다."""
import asyncio, base64, email, email.policy, json, time
from email.message import EmailMessage
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from tests.test_gmail import API, SEND_AS, _b64, _client, _msg, _posts, _sendas, _sent
from tests.test_google_tools import _make_mock_response
from tools.gmail import (
    _FORWARDED,
    _SCOPE_HINT,
    _normalize,
    _re_subject,
    gmail_create_draft,
    gmail_create_reply_draft,
    gmail_find_or_send,
    gmail_forward,
    gmail_reply,
)

REPLY_HEADERS = ["Message-ID", "References", "Subject", "Reply-To", "From", "To", "Cc"]


def _m(mid: str, labels: list, headers: dict | None = None) -> dict:
    """threads.get(format=metadata)의 메일 하나."""
    return {"id": mid, "threadId": "t1", "labelIds": labels,
            "payload": {"headers": [{"name": k, "value": v} for k, v in (headers or {}).items()]}}


M1 = _m("m1", ["INBOX"], {"Message-ID": "<a@x>", "Subject": "회의", "From": "홍길동 <a@b.c>"})


def _thread(*messages) -> dict:
    return {"id": "t1", "messages": list(messages or [M1, _m("m2", ["SENT"])])}


def _get(thread=None, status: int = 200) -> AsyncMock:
    """URL로 응답을 고르는 GET 목 — 대화 t1과 sendAs."""
    async def get(url, **kwargs):
        if url == f"{API}/threads/t1":
            return _make_mock_response(status, thread if thread is not None else _thread())
        if url == f"{API}/settings/sendAs":
            return _make_mock_response(200, SEND_AS)
        raise AssertionError(f"예상 못 한 GET {url}")
    return AsyncMock(side_effect=get)


def _parse(raw: str):
    return email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=email.policy.default)


DRAFT = {"id": "d1", "message": {"id": "m9", "threadId": "t1"}}


def _draft_post(response=None) -> AsyncMock:
    async def post(url, **kwargs):
        if url == f"{API}/drafts":
            return response if response is not None else _make_mock_response(200, DRAFT)
        raise AssertionError(f"예상 못 한 POST {url}")
    return AsyncMock(side_effect=post)


def _drafted(post: AsyncMock):
    call = next(c for c in post.await_args_list if c.args[0] == f"{API}/drafts")
    return call.kwargs["json"]["message"], _parse(call.kwargs["json"]["message"]["raw"])


async def _call(fn, client, **kwargs) -> dict:
    with patch("tools.gmail.get_http_client", return_value=client):
        return json.loads(await fn(access_token="token", **kwargs))


async def _reply(client, **kwargs) -> dict:
    kwargs.setdefault("thread_id", "t1")
    kwargs.setdefault("body", "확인")
    return await _call(gmail_reply, client, **kwargs)


# ── 답장(C2)·답장 초안(C5)·초안(C4) ──────────────────────────────────────

@pytest.mark.asyncio
async def test_답장은_대상_메일_헤더로_같은_대화에_보낸다():
    get, post = _get(), _posts()
    result = await _reply(_client(get=get, post=post))
    assert result == {"success": True, "messageId": "m-1", "threadId": "t-1"}
    send = next(c for c in post.await_args_list if c.args[0] == f"{API}/messages/send")
    assert send.kwargs["json"]["threadId"] == "t1"
    msg = _sent(post)
    assert (msg["In-Reply-To"], msg["References"], msg["Subject"], msg["To"]) == \
        ("<a@x>", "<a@x>", "Re: 회의", "홍길동 <a@b.c>")
    params = get.await_args_list[0].kwargs["params"]
    assert params == {"format": "metadata", "metadataHeaders": REPLY_HEADERS}


@pytest.mark.asyncio
async def test_답장_References는_원본_References_뒤에_Message_ID를_붙인다():
    m1 = _m("m1", ["INBOX"], {"Message-ID": "<a@x>", "References": "<r0@x>", "Subject": "회의", "From": "a@b.c"})
    post = _posts()
    await _reply(_client(get=_get(_thread(m1)), post=post))
    assert _sent(post)["References"] == "<r0@x> <a@x>"


@pytest.mark.parametrize("subject, expected", [("RE: 회의", "RE: 회의"), ("re:회의", "re:회의"),
                                               ("회의", "Re: 회의"), ("", "Re: ")])
def test_답장_제목은_Re가_있으면_그대로(subject, expected):
    assert _re_subject(subject) == expected


@pytest.mark.asyncio
async def test_답장_받는_사람은_Reply_To가_From보다_먼저():
    m1 = _m("m1", ["INBOX"], {"Message-ID": "<a@x>", "Subject": "회의", "From": "a@b.c", "reply-to": "r@x.com"})
    post = _posts()
    await _reply(_client(get=_get(_thread(m1)), post=post))
    assert _sent(post)["To"] == "r@x.com"


@pytest.mark.asyncio
async def test_받는_사람을_주면_자동으로_채우지_않는다():
    post = _posts()
    await _reply(_client(get=_get(), post=post), cc="c@x.com")
    msg = _sent(post)
    assert msg["To"] is None and msg["Cc"] == "c@x.com"


@pytest.mark.asyncio
@pytest.mark.parametrize("messages, target", [
    ([M1, _m("m2", ["SENT"], {"Message-ID": "<b@x>"}), _m("m3", ["DRAFT"], {"Message-ID": "<c@x>"})], "<a@x>"),
    ([_m("m1", ["SENT"], {"Message-ID": "<a@x>", "To": "a@x.com"}),
      _m("m2", ["SENT"], {"Message-ID": "<b@x>", "To": "b@x.com"})], "<b@x>"),
])
async def test_답장_대상은_초안과_내가_보낸_메일을_건너뛴다(messages, target):   # Review Focus 1
    post = _posts()
    await _reply(_client(get=_get(_thread(*messages)), post=post))
    assert _sent(post)["In-Reply-To"] == target


@pytest.mark.asyncio
async def test_내가_보낸_메일뿐이면_원래_받는_사람에게_답장():
    messages = [_m("m1", ["SENT"], {"Message-ID": "<a@x>", "From": "me@gmail.com", "To": "a@x.com", "Cc": "b@x.com"}),
                _m("m2", ["SENT"], {"Message-ID": "<b@x>", "From": "me@gmail.com", "To": "c@x.com", "Cc": "d@x.com"})]
    post = _posts()
    await _reply(_client(get=_get(_thread(*messages)), post=post))
    msg = _sent(post)
    assert (msg["To"], msg["Cc"]) == ("c@x.com", "d@x.com")


@pytest.mark.asyncio
async def test_답장_받을_주소가_원래_메일에_없으면_보내지_않는다():
    post = AsyncMock()
    result = await _reply(_client(get=_get(_thread(_m("m1", ["INBOX"], {"Message-ID": "<a@x>"}))), post=post))
    assert "답장 받을 주소를 원래 메일에서 찾지 못했어요" in result["error"] and post.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("sep", ["\r\n", " "])
async def test_원본_헤더의_줄바꿈은_새_헤더를_만들지_못한다(sep):   # Review Focus 5
    m1 = _m("m1", ["INBOX"], {"Message-ID": f"<a@x>{sep}Bcc: e1@x.com", "References": f"<r@x>{sep}Bcc: e2@x.com",
                              "Subject": f"회의{sep}Bcc: evil@x.com", "Reply-To": f"r@x.com{sep}Bcc: e3@x.com"})
    post = _posts()
    await _reply(_client(get=_get(_thread(m1)), post=post))
    msg = _sent(post)
    assert msg["Bcc"] is None
    assert msg["Subject"].startswith("Re: 회의")


@pytest.mark.asyncio
async def test_내_메일의_원래_받는_사람_줄바꿈도_새_헤더를_만들지_못한다():
    m1 = _m("m1", ["SENT"], {"Message-ID": "<a@x>", "To": "a@x.com\r\nBcc: x@y", "Cc": "c@x.com\r\nBcc: z@y"})
    post = _posts()
    await _reply(_client(get=_get(_thread(m1)), post=post))
    msg = _sent(post)
    assert msg["Bcc"] is None and msg["To"].startswith("a@x.com")


@pytest.mark.asyncio
async def test_답장_라벨은_발송_뒤_붙이고_실패해도_성공이다():   # Review Focus 4
    post = _posts(modify=_make_mock_response(400, {"error": {"message": "bad label"}}))
    result = await _reply(_client(get=_get(), post=post), label_ids="L1, L2")
    assert result == {"success": True, "messageId": "m-1", "threadId": "t-1"}
    modify = post.await_args_list[-1]
    assert modify.args[0] == f"{API}/messages/m-1/modify" and modify.kwargs["json"] == {"addLabelIds": ["L1", "L2"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("fn", [gmail_reply, gmail_create_reply_draft])
@pytest.mark.parametrize("bad", ["../x", "<a@x>", "  "])
async def test_대화_ID_형식이_아니면_HTTP_없이_실패한다(fn, bad):
    get, post = AsyncMock(), AsyncMock()
    result = await _call(fn, _client(get=get, post=post), thread_id=bad, body="x")
    assert "Gmail 대화 ID가 아니에요" in result["error"] and get.await_count == post.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("fn, kwargs", [(gmail_reply, {"thread_id": "t1", "body": "x"}),
                                        (gmail_create_reply_draft, {"thread_id": "t1", "body": "x"}),
                                        (gmail_create_draft, {})])
async def test_include_signature가_잘못이면_HTTP_없이_실패한다(fn, kwargs):
    get, post = AsyncMock(), AsyncMock()
    result = await _call(fn, _client(get=get, post=post), include_signature="yes", **kwargs)
    assert "error" in result and get.await_count == post.await_count == 0


@pytest.mark.asyncio
async def test_답장_초안은_drafts에_threadId와_함께():
    post = _draft_post()
    result = await _call(gmail_create_reply_draft, _client(get=_get(), post=post), thread_id="t1", body="확인")
    assert result == {"success": True, "draftId": "d1", "messageId": "m9", "threadId": "t1"}
    message, msg = _drafted(post)
    assert message["threadId"] == "t1"
    assert (msg["In-Reply-To"], msg["Subject"], msg["To"]) == ("<a@x>", "Re: 회의", "홍길동 <a@b.c>")


@pytest.mark.asyncio
async def test_초안은_받는_사람_없이도_만든다():
    post = _draft_post()
    result = await _call(gmail_create_draft, _client(post=post), subject="제목", body="본문")
    assert result == {"success": True, "draftId": "d1", "messageId": "m9", "threadId": "t1"}
    assert post.await_count == 1
    message, msg = _drafted(post)
    assert "threadId" not in message
    assert msg["Subject"] == "제목" and msg["To"] is None
    assert msg.get_content().strip() == "본문"


@pytest.mark.asyncio
async def test_빈_초안도_만든다():
    post = _draft_post()
    await _call(gmail_create_draft, _client(post=post))
    assert post.await_count == 1
    assert _drafted(post)[1]["Subject"] is None


@pytest.mark.asyncio
async def test_초안은_보낸_사람과_서명을_C1처럼():
    post = _draft_post()
    await _call(gmail_create_draft, _client(get=_sendas(), post=post), to="a@x.com", from_email="alias@ieum.io",
                include_signature=True, body="본문")
    msg = _drafted(post)[1]
    assert msg["From"].addresses[0].addr_spec == "alias@ieum.io" and msg["To"] == "a@x.com"


@pytest.mark.asyncio
async def test_초안_403은_Gmail_권한_안내():
    post = _draft_post(_make_mock_response(403, {"error": {"message": "insufficient"}}))
    result = await _call(gmail_create_draft, _client(post=post), body="x")
    assert _SCOPE_HINT in result["error"]


@pytest.mark.asyncio
async def test_대화가_404면_실패():
    post = AsyncMock()
    result = await _reply(_client(get=_get(status=404, thread={"error": {"message": "not found"}}), post=post))
    assert "Gmail API 오류 (404)" in result["error"] and post.await_count == 0


@pytest.mark.asyncio
async def test_대화에_메일이_없으면_실패():
    result = await _reply(_client(get=_get(_thread(_m("m3", ["DRAFT"])))))
    assert "답장할 메일이 대화에 없어요" in result["error"]


# ── 전달(C3) ─────────────────────────────────────────────────────────────

UPLOAD = "https://gmail.googleapis.com/upload/gmail/v1/users/me/messages/send"
PDF, PNG = b"%PDF-1.4 a", b"\x89PNG b"


def _original(subject: str = "회의", attachments: bool = True) -> EmailMessage:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "홍길동 <a@b.c>", "me@gmail.com", subject
    msg["Date"] = "Mon, 10 Nov 2026 09:00:00 +0900"
    msg.set_content("원본 본문")
    if attachments:
        msg.add_attachment(PDF, "application", "pdf", filename="a.pdf")
        msg.add_attachment(PNG, "image", "png", filename="b.png")
    return msg


def _fwd_get(original: EmailMessage | bytes, size: int | None = None, sleep: float = 0) -> AsyncMock:
    """메일 m1 GET — params format(minimal → 크기, raw → 원본)으로 나눈다."""
    data = original if isinstance(original, bytes) else original.as_bytes()

    async def get(url, **kwargs):
        if url == f"{API}/settings/sendAs":
            return _make_mock_response(200, SEND_AS)
        assert url == f"{API}/messages/m1", url
        if kwargs["params"]["format"] == "minimal":
            return _make_mock_response(200, {"id": "m1", "sizeEstimate": size or len(data)})
        assert kwargs["params"] == {"format": "raw"}
        if sleep:
            await asyncio.sleep(sleep)
        return _make_mock_response(200, {"id": "m1", "raw": _b64(data)})
    return AsyncMock(side_effect=get)


def _fwd_post(modify=None, sleep: float = 0, block: float = 0) -> AsyncMock:
    """sleep = 업로드 응답 전 await 지연(마감에 걸린다), block = 루프를 막는 지연(마감이 끊지 못한다)."""
    async def post(url, **kwargs):
        if url == UPLOAD:
            if sleep:
                await asyncio.sleep(sleep)
            if block:
                time.sleep(block)
            return _make_mock_response(200, {"id": "m-1", "threadId": "t-9", "labelIds": ["SENT"]})
        if url == f"{API}/messages/m-1/modify":
            if isinstance(modify, Exception):
                raise modify
            labeled = _make_mock_response(200, {"id": "m-1", "labelIds": ["SENT", "L1"]})
            if isinstance(modify, (int, float)):   # httpx timeout을 무시하고 늦게 오는 응답
                await asyncio.sleep(modify)
                return labeled
            return modify or labeled
        raise AssertionError(f"예상 못 한 POST {url}")
    return AsyncMock(side_effect=post)


def _uploaded(post: AsyncMock):
    call = next(c for c in post.await_args_list if c.args[0] == UPLOAD)
    return call, email.message_from_bytes(call.kwargs["content"], policy=email.policy.default)


async def _forward(client, **kwargs) -> dict:
    kwargs.setdefault("message_id", "m1")
    kwargs.setdefault("to", "kim@x.com")
    return await _call(gmail_forward, client, **kwargs)


def _gets_by_format(get: AsyncMock, fmt: str) -> list:
    return [c for c in get.await_args_list if (c.kwargs.get("params") or {}).get("format") == fmt]


@pytest.mark.asyncio
async def test_전달은_원본_첨부를_다시_붙여_업로드_경로로_보낸다():
    get, post = _fwd_get(_original()), _fwd_post()
    result = await _forward(_client(get=get, post=post))
    assert result == {"success": True, "messageId": "m-1", "threadId": "t-9"}
    call, msg = _uploaded(post)
    assert call.kwargs["params"] == {"uploadType": "media"}
    assert call.kwargs["headers"]["Content-Type"] == "message/rfc822"
    assert "json" not in call.kwargs
    assert msg["Subject"] == "Fwd: 회의" and msg["To"] == "kim@x.com"
    files = [(a.get_filename(), a.get_content()) for a in msg.iter_attachments()]
    assert files == [("a.pdf", PDF), ("b.png", PNG)]
    text = msg.get_body(preferencelist=("plain",)).get_content()
    marks = [text.index(m) for m in (_FORWARDED, "From: ", "Date: ", "Subject: ", "To: ", "원본 본문")]
    assert marks == sorted(marks)


@pytest.mark.asyncio
async def test_덧붙일_말과_서명은_전달_블록_위에():
    post = _fwd_post()
    await _forward(_client(get=_fwd_get(_original()), post=post), body="참고하세요", include_signature=True)
    text = _uploaded(post)[1].get_body(preferencelist=("plain",)).get_content()
    assert text.index("참고하세요") < text.index("-- ") < text.index(_FORWARDED)


@pytest.mark.asyncio
async def test_이미_Fwd면_제목을_그대로():
    post = _fwd_post()
    await _forward(_client(get=_fwd_get(_original("FWD: 회의")), post=post))
    assert _uploaded(post)[1]["Subject"] == "FWD: 회의"


def _alternative(plain: bool = True) -> EmailMessage:
    msg = _original(attachments=False)
    if plain:
        msg.add_alternative("<p>원본 <b>HTML</b></p>", subtype="html")
    else:
        msg.set_content("<p>원본 <b>HTML</b></p>", subtype="html")
    return msg


@pytest.mark.asyncio
@pytest.mark.parametrize("plain, body_format, content_type, expected", [
    (True, "text/html", "text/html", "<b>HTML</b>"),
    (True, "text/plain", "text/plain", "원본 본문"),
    (False, "text/plain", "text/plain", "원본 HTML"),
])
async def test_HTML_형식이면_원본_HTML을_살린다(plain, body_format, content_type, expected):
    post = _fwd_post()
    await _forward(_client(get=_fwd_get(_alternative(plain)), post=post), body_format=body_format)
    msg = _uploaded(post)[1]
    assert msg.get_content_type() == content_type
    assert expected in msg.get_content()


@pytest.mark.asyncio
async def test_원본이_텍스트뿐이면_HTML_전달에서_escape한다():
    original = _original(attachments=False)
    original.set_content("a < b")
    post = _fwd_post()
    await _forward(_client(get=_fwd_get(original), post=post), body_format="text/html")
    assert "a &lt; b" in _uploaded(post)[1].get_content()


@pytest.mark.asyncio
async def test_전달은_첨부된_메일을_통째로_인라인_이미지까지_붙인다():   # Review Focus 2
    inner = EmailMessage()
    inner["Subject"] = "안쪽"
    inner.set_content("안쪽 본문")
    inner.add_attachment(b"inner", "text", "plain", filename="inner.txt")
    original = _original(attachments=False)
    original.add_attachment(inner, filename="fw.eml")
    original.add_attachment(PNG, "image", "png", filename="logo.png", cid="<logo@x>", disposition="inline")
    post = _fwd_post()
    await _forward(_client(get=_fwd_get(original), post=post))
    msg = _uploaded(post)[1]
    parts = [p for p in msg.iter_parts() if p.get_filename()]
    assert [(p.get_filename(), p.get_content_type()) for p in parts] == \
        [("fw.eml", "message/rfc822"), ("logo.png", "image/png")]
    forwarded = parts[0].get_payload(0)
    assert forwarded.get_body(preferencelist=("plain",)).get_content().strip() == "안쪽 본문"
    assert [a.get_filename() for a in forwarded.iter_attachments()] == ["inner.txt"]
    assert parts[1]["Content-ID"] == "<logo@x>"   # 원본 HTML의 cid: 참조가 이어지게


@pytest.mark.asyncio
async def test_text_첨부는_원본_charset을_유지한다():
    data = "한글 메모".encode("euc-kr")
    original = _original(attachments=False)
    original.add_attachment(data, "text", "plain", filename="k.txt", params={"charset": "euc-kr"})
    post = _fwd_post()
    await _forward(_client(get=_fwd_get(original), post=post))
    part = next(_uploaded(post)[1].iter_attachments())
    assert part.get_content_charset() == "euc-kr" and part.get_payload(decode=True) == data


@pytest.mark.asyncio
async def test_첨부_파일명의_줄바꿈은_정리해서_전달한다():
    raw = ("From: a@b.c\r\nTo: me@gmail.com\r\nSubject: s\r\nMIME-Version: 1.0\r\n"
           'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
           "--B\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
           "--B\r\nContent-Type: application/pdf\r\n"
           "Content-Disposition: attachment; filename*=utf-8''a.pdf%0D%0ABcc%3A%20evil%40x.com\r\n"
           "Content-Transfer-Encoding: base64\r\n\r\nJVBERg==\r\n--B--\r\n").encode()
    post = _fwd_post()
    result = await _forward(_client(get=_fwd_get(raw), post=post))
    assert result["success"] is True
    msg = _uploaded(post)[1]
    assert [a.get_filename() for a in msg.iter_attachments()] == ["a.pdf Bcc: evil@x.com"]
    assert msg["Bcc"] is None


@pytest.mark.asyncio
async def test_첨부_charset의_줄바꿈도_정리해서_전달한다():
    raw = ("From: a@b.c\r\nTo: me@gmail.com\r\nSubject: s\r\nMIME-Version: 1.0\r\n"
           'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
           "--B\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
           "--B\r\nContent-Type: text/plain; charset*=us-ascii''utf-8%0D%0ABcc%3A%20evil%40x.com\r\n"
           "Content-Disposition: attachment; filename=n.txt\r\n\r\nmemo\r\n--B--\r\n").encode()
    post = _fwd_post()
    result = await _forward(_client(get=_fwd_get(raw), post=post))
    assert result["success"] is True
    msg = _uploaded(post)[1]
    assert [a.get_filename() for a in msg.iter_attachments()] == ["n.txt"]
    assert msg["Bcc"] is None


@pytest.mark.asyncio
async def test_원본이_35MB를_넘으면_받지_않고_실패한다():   # Review Focus 3
    get, post = _fwd_get(_original(), size=36_000_000), AsyncMock()
    result = await _forward(_client(get=get, post=post))
    assert "36.0MB" in result["error"] and "35MB" in result["error"]
    assert _gets_by_format(get, "raw") == [] and post.await_count == 0


@pytest.mark.asyncio
async def test_완성_메일이_35MB를_넘으면_보내지_않는다(monkeypatch):   # Review Focus 3
    monkeypatch.setattr("tools.gmail._GMAIL_MAX", 2_000)
    original = _original(attachments=False)
    original.add_attachment(b"x" * 1_000, "application", "octet-stream", filename="1.bin")
    original.add_attachment(b"y" * 1_000, "application", "octet-stream", filename="2.bin")
    post = AsyncMock()
    result = await _forward(_client(get=_fwd_get(original, size=100), post=post))
    assert "MB" in result["error"] and post.await_count == 0


@pytest.mark.asyncio
async def test_전달_요청은_C3_전용_타임아웃():
    get, post = _fwd_get(_original()), _fwd_post()
    await _forward(_client(get=get, post=post))
    assert _gets_by_format(get, "raw")[0].kwargs["timeout"] == 120.0
    assert _uploaded(post)[0].kwargs["timeout"] == 120.0


@pytest.mark.asyncio
async def test_150초_마감을_넘으면_보내지_않고_실패한다(monkeypatch):
    monkeypatch.setattr("tools.gmail._FORWARD_DEADLINE", 0.01)
    post = AsyncMock()
    result = await _forward(_client(get=_fwd_get(_original(), sleep=1), post=post))
    assert "시간" in result["error"] and post.await_count == 0


@pytest.mark.asyncio
async def test_업로드_POST도_150초_마감_안에_있다(monkeypatch):
    monkeypatch.setattr("tools.gmail._FORWARD_DEADLINE", 0.05)
    post = _fwd_post(sleep=1)
    result = await _forward(_client(get=_fwd_get(_original()), post=post), label_ids=["L1"])
    assert "시간" in result["error"]
    assert [c.args[0] for c in post.await_args_list] == [UPLOAD]   # 라벨도 붙이지 않는다


@pytest.mark.asyncio
async def test_전달_라벨은_마감까지_남은_시간_안에서_붙인다(monkeypatch):   # BE→agent 180초를 넘지 않게
    monkeypatch.setattr("tools.gmail._FORWARD_DEADLINE", 5.0)
    post = _fwd_post()
    await _forward(_client(get=_fwd_get(_original()), post=post), label_ids=["L1"])
    modify = post.await_args_list[-1]
    assert modify.args[0].endswith("/modify") and 0 < modify.kwargs["timeout"] <= 5.0


@pytest.mark.asyncio
async def test_마감이_다_되면_라벨은_건너뛰고_성공이다(monkeypatch):
    monkeypatch.setattr("tools.gmail._FORWARD_DEADLINE", 0.3)
    post = _fwd_post(block=0.4)   # 업로드가 루프를 막아 마감이 끊지 못한 채 2xx — 메일은 이미 나갔다
    result = await _forward(_client(get=_fwd_get(_original()), post=post), label_ids=["L1"])
    assert result == {"success": True, "messageId": "m-1", "threadId": "t-9"}
    assert [c.args[0] for c in post.await_args_list] == [UPLOAD]


@pytest.mark.asyncio
async def test_라벨_요청도_마감에서_끊고_성공이다(monkeypatch):   # httpx timeout은 단계별이라 전체 상한이 아니다
    monkeypatch.setattr("tools.gmail._FORWARD_DEADLINE", 0.3)
    post = _fwd_post(modify=2)
    started = time.monotonic()
    result = await _forward(_client(get=_fwd_get(_original()), post=post), label_ids=["L1"])
    assert result == {"success": True, "messageId": "m-1", "threadId": "t-9"}
    assert time.monotonic() - started < 1.0


@pytest.mark.asyncio
async def test_전달_라벨_실패는_성공이다():   # Review Focus 4
    post = _fwd_post(modify=httpx.ReadTimeout("slow"))
    result = await _forward(_client(get=_fwd_get(_original()), post=post), label_ids=["L1"])
    assert result == {"success": True, "messageId": "m-1", "threadId": "t-9"}
    modify = post.await_args_list[-1]
    assert modify.args[0] == f"{API}/messages/m-1/modify" and modify.kwargs["timeout"] == 30.0


@pytest.mark.asyncio
async def test_받는_사람이_전부_비면_HTTP_없이_실패한다():
    get, post = AsyncMock(), AsyncMock()
    result = await _forward(_client(get=get, post=post), to=" , ", cc="", bcc=None)
    assert "error" in result and get.await_count == post.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["../x", "<a@x>"])
async def test_메일_ID_형식이_아니면_HTTP_없이_실패한다(bad):
    get, post = AsyncMock(), AsyncMock()
    result = await _forward(_client(get=get, post=post), message_id=bad)
    assert "Gmail 메일 ID가 아니에요" in result["error"] and get.await_count == post.await_count == 0


# ── 찾고 없으면 보내기(S4) ───────────────────────────────────────────────

def _find_get(found: bool = True, status: int = 200) -> AsyncMock:
    async def get(url, **kwargs):
        if url == f"{API}/messages":
            body = {"messages": [{"id": "f1"}]} if found else {}
            return _make_mock_response(status, body if status == 200 else {"error": {"message": "insufficient"}})
        if url == f"{API}/messages/f1":
            return _make_mock_response(200, _msg("f1"))
        raise AssertionError(f"예상 못 한 GET {url}")
    return AsyncMock(side_effect=get)


async def _find_or_send(client, **kwargs) -> dict:
    kwargs.setdefault("query", "from:boss")
    kwargs.setdefault("to", "a@x.com")
    kwargs.setdefault("subject", "보고")
    kwargs.setdefault("body", "안녕")
    return await _call(gmail_find_or_send, client, **kwargs)


@pytest.mark.asyncio
async def test_찾으면_보내지_않고_created_false():
    get, post = _find_get(), AsyncMock()
    result = await _find_or_send(_client(get=get, post=post), search_label_ids=["L_S"], label_ids=["L_X"])
    assert post.await_count == 0
    assert get.await_args_list[0].kwargs["params"] == {"q": "from:boss", "maxResults": 1, "labelIds": ["L_S"]}
    assert result == {"success": True, "created": False, **_normalize(_msg("f1"))}


@pytest.mark.asyncio
async def test_없으면_보내고_created_true_보낸_값으로():
    get, post = _find_get(found=False), _posts()
    result = await _find_or_send(_client(get=get, post=post))
    assert [c.args[0] for c in post.await_args_list] == [f"{API}/messages/send"]
    assert [c.args[0] for c in get.await_args_list] == [f"{API}/messages"]
    assert result["created"] is True
    assert (result["messageId"], result["threadId"], result["to"], result["subject"], result["bodyText"],
            result["snippet"], result["from"]) == ("m-1", "t-1", "a@x.com", "보고", "안녕", "", "")
    assert result["receivedAt"].endswith("Z") and result["labels"] == ["SENT"]


@pytest.mark.asyncio
async def test_보낸_경로_출력도_헤더를_1000자로_자른다():   # 찾은 경로(_normalize)와 같다
    to = ", ".join(f"user{i}@example.com" for i in range(100))
    result = await _find_or_send(_client(get=_find_get(found=False), post=_posts()), to=to, subject="가" * 1500)
    assert len(result["to"]) == 1000 and result["to"] == to[:1000]
    assert result["subject"] == "가" * 1000


@pytest.mark.asyncio
async def test_보낸_HTML_본문은_bodyText에서_태그를_뺀다():
    result = await _find_or_send(_client(get=_find_get(found=False), post=_posts()),
                                 body_format="text/html", body="<p>안녕 <b>하세요</b></p>")
    assert result["bodyText"] == "안녕 하세요"


@pytest.mark.asyncio
@pytest.mark.parametrize("found", [True, False])
async def test_찾기_라벨과_보낸_뒤_라벨은_섞이지_않는다(found):
    get, post = _find_get(found=found), _posts()
    result = await _find_or_send(_client(get=get, post=post), search_label_ids=["L_S"], label_ids=["L_X"])
    assert get.await_args_list[0].kwargs["params"]["labelIds"] == ["L_S"]
    modify = [c for c in post.await_args_list if c.args[0].endswith("/modify")]
    assert [c.kwargs["json"] for c in modify] == ([] if found else [{"addLabelIds": ["L_X"]}])
    if not found:
        assert result["labels"] == ["SENT"]   # SENT 목은 modify 응답도 labelIds ["SENT"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"to": " , ", "cc": "", "bcc": None}, {"include_signature": "yes"}])
async def test_보내기_칸_검사는_찾기_전에(kwargs):
    get, post = AsyncMock(), AsyncMock()
    result = await _find_or_send(_client(get=get, post=post), **kwargs)
    assert "error" in result and get.await_count == post.await_count == 0


@pytest.mark.asyncio
async def test_검색어가_비면_HTTP_없이_실패():
    get, post = AsyncMock(), AsyncMock()
    result = await _find_or_send(_client(get=get, post=post), query="  ")
    assert "error" in result and get.await_count == post.await_count == 0


@pytest.mark.asyncio
async def test_보내기_라벨_실패도_created_true():   # Review Focus 4
    post = _posts(modify=_make_mock_response(400, {"error": {"message": "bad"}}))
    result = await _find_or_send(_client(get=_find_get(found=False), post=post), label_ids=["L_X"])
    assert result["success"] is True and result["created"] is True and result["labels"] == ["SENT"]


@pytest.mark.asyncio
async def test_찾기_403은_Gmail_권한_안내():
    post = AsyncMock()
    result = await _find_or_send(_client(get=_find_get(status=403), post=post))
    assert _SCOPE_HINT in result["error"] and post.await_count == 0

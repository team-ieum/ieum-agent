"""Gmail 답장·답장 초안·초안·전달·찾고 없으면 보내기(spec 2026-11-10 gmail-forms-9 §3). HTTP는 목으로 대신한다."""
import base64, email, email.policy, json
from unittest.mock import AsyncMock, patch

import pytest

from tests.test_gmail import API, SEND_AS, _client, _posts, _sendas, _sent
from tests.test_google_tools import _make_mock_response
from tools.gmail import (
    _SCOPE_HINT,
    _re_subject,
    gmail_create_draft,
    gmail_create_reply_draft,
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

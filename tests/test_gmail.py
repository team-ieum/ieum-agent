"""Gmail 메일 보내기 도구(spec 2026-11-04). HTTP는 목으로 대신한다."""
import base64, email, email.policy, json, logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from tests.action_outputs import assert_matches_outputs
from tests.test_google_tools import _make_async_client, _make_mock_response
from tools.gmail import gmail_send_email

API = "https://gmail.googleapis.com/gmail/v1/users/me"
SIG = '<div>윤도훈<br><a href="https://ieum.io">이음</a></div>'
SEND_AS = {"sendAs": [
    {"sendAsEmail": "me@gmail.com", "displayName": "윤도훈", "isDefault": True, "isPrimary": True, "signature": SIG},
    {"sendAsEmail": "alias@ieum.io", "displayName": "이음 팀", "verificationStatus": "accepted", "signature": ""},
    {"sendAsEmail": "pending@ieum.io", "verificationStatus": "pending"},
]}
SENT = {"id": "m-1", "threadId": "t-1", "labelIds": ["SENT"]}


def _client(get=None, post=None):
    """넘기지 않은 메서드는 불리면 실패한다(호출 없음 단언용)."""
    return _make_async_client(get_mock=get or AsyncMock(side_effect=AssertionError("GET 없음")),
                              post_mock=post or AsyncMock(side_effect=AssertionError("POST 없음")))


def _posts(send=None, modify=None) -> AsyncMock:
    async def post(url, **kwargs):
        if url == f"{API}/messages/send":
            return send if send is not None else _make_mock_response(200, SENT)
        if url == f"{API}/messages/m-1/modify":
            if isinstance(modify, Exception):
                raise modify
            return modify if modify is not None else _make_mock_response(200, SENT)
        raise AssertionError(f"예상 못 한 POST {url}")
    return AsyncMock(side_effect=post)


def _sendas(body=SEND_AS):
    return AsyncMock(return_value=_make_mock_response(200, body) if isinstance(body, dict) else body)


async def _run(client, **kwargs) -> dict:
    kwargs.setdefault("subject", "주간 보고")
    kwargs.setdefault("body", "안녕")
    with patch("tools.gmail.get_http_client", return_value=client):
        return json.loads(await gmail_send_email(access_token="token", **kwargs))


def _sent(post: AsyncMock):
    """messages/send 호출의 raw를 풀어 메시지로."""
    call = next(c for c in post.await_args_list if c.args[0].endswith("/messages/send"))
    raw = call.kwargs["json"]["raw"]
    return email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=email.policy.default)


@pytest.mark.asyncio
async def test_보내면_messageId와_threadId를_돌려준다():
    post = _posts()
    result = await _run(_client(post=post), to="a@x.com")
    assert result == {"success": True, "messageId": "m-1", "threadId": "t-1"}
    assert post.await_args_list[0].kwargs["headers"]["Authorization"] == "Bearer token"
    assert_matches_outputs("gmail.send_email", json.dumps(result))


@pytest.mark.asyncio
async def test_한글_제목과_보낸_사람_이름():
    post = _posts()
    await _run(_client(get=_sendas(), post=post), to="a@x.com", from_email="alias@ieum.io", from_name="이음 봇")
    msg = _sent(post)
    assert msg["Subject"] == "주간 보고"
    addr = msg["From"].addresses[0]
    assert (addr.display_name, addr.addr_spec) == ("이음 봇", "alias@ieum.io")


@pytest.mark.asyncio
@pytest.mark.parametrize("to", ["a@x.com, b@x.com", ["a@x.com", "b@x.com"], '["a@x.com", "b@x.com"]', "a@x.com,, b@x.com ,"])
async def test_받는_사람은_쉼표_리스트_JSON_모두_받는다(to):
    post = _posts()
    await _run(_client(post=post), to=to, cc="c@x.com", bcc="d@x.com")
    msg = _sent(post)
    assert (msg["To"], msg["Cc"], msg["Bcc"]) == ("a@x.com, b@x.com", "c@x.com", "d@x.com")


@pytest.mark.asyncio
async def test_받는_사람이_전부_비면_HTTP_없이_실패한다():          # Review Focus 5
    # 도구가 예외를 error JSON으로 바꾸므로 "불리면 실패" 목만으로는 호출 여부를 못 잡는다 — 횟수로 단언
    for to, cc, bcc in ((" , ", "[]", None), ('[""]', "", "  "), (None, None, None)):
        get, post = AsyncMock(), AsyncMock()
        result = await _run(_client(get=get, post=post), to=to, cc=cc, bcc=bcc,
                            from_email="alias@ieum.io", include_signature=True)
        assert "error" in result and get.await_count == post.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("from_email", ["other@x.com", "pending@ieum.io"])
async def test_보낸_사람이_별칭에_없거나_pending이면_보내지_않는다(from_email):
    result = await _run(_client(get=_sendas()), to="a@x.com", from_email=from_email)
    assert "보낸 사람 주소를 Gmail 별칭에서 찾을 수 없어요" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [_make_mock_response(403, {"error": {"message": "insufficient"}}),
                                 _make_mock_response(500, {"error": {"message": "backend"}}),
                                 httpx.ReadTimeout("t")])
async def test_sendAs_조회가_실패하면_보내지_않는다(bad):           # Review Focus 2
    get = AsyncMock(side_effect=bad) if isinstance(bad, Exception) else _sendas(bad)
    post = AsyncMock()   # fail-open이면 send가 불린다 — 예외 목은 error JSON에 묻히므로 횟수로 본다
    result = await _run(_client(get=get, post=post), to="a@x.com", from_email="alias@ieum.io")
    assert "error" in result and post.await_count == 0


@pytest.mark.asyncio
async def test_보낸_사람_주소는_대소문자와_공백을_무시하고_sendAs_주소로_보낸다():   # Review Focus 3
    post = _posts()
    await _run(_client(get=_sendas(), post=post), to="a@x.com", from_email=" Alias@IEUM.io ")
    addr = _sent(post)["From"].addresses[0]
    assert (addr.display_name, addr.addr_spec) == ("이음 팀", "alias@ieum.io")   # from_name 비면 sendAs displayName


@pytest.mark.asyncio
async def test_보낸_사람_이름만_있으면_기본_주소로_From을_만든다():
    post = _posts()
    await _run(_client(get=_sendas(), post=post), to="a@x.com", from_name="이음")
    addr = _sent(post)["From"].addresses[0]
    assert (addr.display_name, addr.addr_spec) == ("이음", "me@gmail.com")


@pytest.mark.asyncio
async def test_보낸_사람이_비면_From이_없고_sendAs도_조회하지_않는다():
    post = _posts()
    await _run(_client(post=post), to="a@x.com")
    assert _sent(post)["From"] is None


@pytest.mark.asyncio
async def test_답장_주소와_HTML_본문():
    post = _posts()
    await _run(_client(post=post), to="a@x.com", reply_to="r@x.com", body_format="text/html", body="<p>안녕</p>")
    msg = _sent(post)
    assert msg["Reply-To"] == "r@x.com" and msg.get_content_type() == "text/html"


@pytest.mark.asyncio
async def test_서명_HTML_본문은_br_구분자와_서명_HTML을_붙인다():
    post = _posts()
    await _run(_client(get=_sendas(), post=post), to="a@x.com", include_signature=True,
               body_format="text/html", body="<p>안녕</p>")
    assert _sent(post).get_content().rstrip("\n") == "<p>안녕</p><br>-- <br>" + SIG


@pytest.mark.asyncio
async def test_서명_일반_텍스트는_태그를_없애고_링크_주소를_살린다():
    post = _posts()
    await _run(_client(get=_sendas(), post=post), to="a@x.com", include_signature=True)
    assert _sent(post).get_content().rstrip("\n") == "안녕\n-- \n윤도훈\n이음 (https://ieum.io)"


@pytest.mark.asyncio
async def test_서명이_비어_있으면_아무것도_붙이지_않는다():
    post = _posts()
    await _run(_client(get=_sendas(), post=post), to="a@x.com", from_email="alias@ieum.io", include_signature=True)
    assert _sent(post).get_content().rstrip("\n") == "안녕"


@pytest.mark.asyncio
@pytest.mark.parametrize("body_format", ["text/plain", "text/html"])
@pytest.mark.parametrize("signature", ["  ", "<div><br></div>", "<p> </p>"])
async def test_서명이_공백이나_빈_마크업이면_구분자도_붙이지_않는다(signature, body_format):
    body = {"sendAs": [{"sendAsEmail": "me@gmail.com", "isDefault": True, "signature": signature}]}
    post = _posts()
    await _run(_client(get=_sendas(body), post=post), to="a@x.com", include_signature=True, body_format=body_format)
    assert _sent(post).get_content().rstrip("\n") == "안녕"


@pytest.mark.asyncio
async def test_서명_일반_텍스트는_p_닫는_태그를_줄바꿈으로():
    body = {"sendAs": [{"sendAsEmail": "me@gmail.com", "isDefault": True, "signature": "<p>윤도훈</p><p>이음</p>"}]}
    post = _posts()
    await _run(_client(get=_sendas(body), post=post), to="a@x.com", include_signature=True)
    assert _sent(post).get_content().rstrip("\n") == "안녕\n-- \n윤도훈\n이음"


@pytest.mark.asyncio
@pytest.mark.parametrize("value, signed", [("false", False), ("False", False), ("TRUE", True), ("", False)])
async def test_include_signature는_문자열도_받는다(value, signed):
    post = _posts()
    await _run(_client(get=_sendas(), post=post), to="a@x.com", include_signature=value)
    assert ("-- " in _sent(post).get_content()) is signed


@pytest.mark.asyncio
async def test_include_signature가_true_false가_아니면_실패한다():
    assert "include_signature" in (await _run(_client(), to="a@x.com", include_signature="yes"))["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("labels", [["Label_1", "STARRED"], '["Label_1", "STARRED"]', "Label_1, STARRED"])
async def test_라벨은_발송_뒤_modify로_붙인다(labels):
    post = _posts()
    result = await _run(_client(post=post), to="a@x.com", label_ids=labels)
    modify = post.await_args_list[1]
    assert modify.args[0] == f"{API}/messages/m-1/modify"
    assert modify.kwargs["json"] == {"addLabelIds": ["Label_1", "STARRED"]}
    assert result["messageId"] == "m-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("modify", [_make_mock_response(400, {"error": {"message": "Invalid label"}}),
                                    httpx.ReadTimeout("t")])
async def test_라벨_실패는_성공으로_두고_경고를_남긴다(modify, caplog):   # Review Focus 4
    caplog.set_level(logging.WARNING, logger="tools.gmail")
    result = await _run(_client(post=_posts(modify=modify)), to="a@x.com", label_ids=["Label_1"])
    assert result == {"success": True, "messageId": "m-1", "threadId": "t-1"}
    assert any("m-1" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_발송_403은_Gmail_권한_안내():
    send = _make_mock_response(403, {"error": {"message": "Request had insufficient authentication scopes."}})
    result = await _run(_client(post=_posts(send=send)), to="a@x.com")
    assert "insufficient authentication scopes" in result["error"]
    assert "Google 연결에서 Gmail 권한을 켜 주세요" in result["error"]


@pytest.mark.asyncio
async def test_발송_예외도_error_JSON이다():
    result = await _run(_client(post=AsyncMock(side_effect=httpx.ConnectTimeout("t"))), to="a@x.com")
    assert "error" in result


@pytest.mark.asyncio
async def test_헤더_값의_줄바꿈은_공백으로_바뀌어_새_헤더를_만들지_못한다():   # Review Focus 1
    post = _posts()
    result = await _run(_client(post=post), to="a@x.com", subject="주간\r\nBcc: evil@x.com")
    msg = _sent(post)
    assert result["messageId"] == "m-1"
    assert msg["Bcc"] is None and msg["Subject"].startswith("주간")

"""Gmail 도구(보내기 spec 2026-11-04, 찾기·라벨·보관·휴지통 spec 2026-11-10). HTTP는 목으로 대신한다."""
import base64, email, email.policy, json, logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from tests.action_outputs import assert_matches_outputs
from tests.test_google_tools import _make_async_client, _make_mock_response
from tools.gmail import (
    _SCOPE_HINT,
    _OUTPUT_MAX,
    _fit,
    _normalize,
    _strip_html,
    gmail_add_label,
    gmail_archive,
    gmail_create_label,
    gmail_find_email,
    gmail_get_thread,
    gmail_remove_label,
    gmail_remove_thread_label,
    gmail_send_email,
    gmail_trash,
)

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
@pytest.mark.parametrize("signature, body_format", [
    ("  ", "text/plain"), ("<div><br></div>", "text/plain"), ("<p> </p>", "text/plain"),
    ('<div><img src="https://x/logo.png"></div>', "text/plain"),  # 글자 없는 서명은 일반 텍스트에 못 싣는다
    ("  ", "text/html"),
])
async def test_서명이_공백이나_글자_없는_마크업이면_구분자도_붙이지_않는다(signature, body_format):
    body = {"sendAs": [{"sendAsEmail": "me@gmail.com", "isDefault": True, "signature": signature}]}
    post = _posts()
    await _run(_client(get=_sendas(body), post=post), to="a@x.com", include_signature=True, body_format=body_format)
    assert _sent(post).get_content().rstrip("\n") == "안녕"


@pytest.mark.asyncio
@pytest.mark.parametrize("signature", ['<div><img src="https://x/logo.png"></div>', "<div><br></div>", "<p> </p>"])
async def test_HTML_본문은_글자_없는_서명_마크업도_붙인다(signature):
    body = {"sendAs": [{"sendAsEmail": "me@gmail.com", "isDefault": True, "signature": signature}]}
    post = _posts()
    await _run(_client(get=_sendas(body), post=post), to="a@x.com", include_signature=True,
               body_format="text/html", body="<p>안녕</p>")
    assert _sent(post).get_content().rstrip("\n") == "<p>안녕</p><br>-- <br>" + signature


@pytest.mark.asyncio
@pytest.mark.parametrize("signature", ["<p>윤도훈</p><p>이음</p>", "<div>윤도훈</div><div>이음</div>"])
async def test_서명_일반_텍스트는_블록_닫는_태그를_줄바꿈으로(signature):
    body = {"sendAs": [{"sendAsEmail": "me@gmail.com", "isDefault": True, "signature": signature}]}
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
@pytest.mark.parametrize("sep", ["\r\n", "\u2028", "\x0c"])
async def test_헤더_값의_줄바꿈은_공백으로_바뀌어_새_헤더를_만들지_못한다(sep):   # Review Focus 1
    post = _posts()
    result = await _run(_client(post=post), to="a@x.com", subject=f"주간{sep}Bcc: evil@x.com")
    msg = _sent(post)
    assert result["messageId"] == "m-1"
    assert msg["Bcc"] is None and msg["Subject"].startswith("주간")


@pytest.mark.asyncio
async def test_발송_2xx_뒤_본문_파싱이_실패해도_성공이다():   # 이미 나간 메일 — 실패로 돌리면 재처리가 두 번 보낸다
    send = _make_mock_response(200, {})
    send.json = lambda: (_ for _ in ()).throw(ValueError("not json"))
    post = _posts(send=send)
    result = await _run(_client(post=post), to="a@x.com", label_ids=["Label_1"])
    assert result == {"success": True, "messageId": "", "threadId": ""}
    assert not any(c.args[0].endswith("/modify") for c in post.await_args_list)


# ── 찾기·대화(S1·S2) ─────────────────────────────────────────────────────

def _b64(data) -> str:
    """Gmail 본문 꼴 — urlsafe, 패딩 없음."""
    return base64.urlsafe_b64encode(data.encode() if isinstance(data, str) else data).decode().rstrip("=")


def _msg(mid: str, thread: str = "t1") -> dict:
    """format=full 응답. html 파트가 먼저 와도 plain이 이긴다."""
    return {"id": mid, "threadId": thread, "labelIds": ["INBOX", "UNREAD"], "snippet": "미리보기",
            "internalDate": "1762732800000",
            "payload": {"mimeType": "multipart/alternative",
                        "headers": [{"name": "SUBJECT", "value": "회의"}, {"name": "From", "value": "홍길동 <a@b.c>"},
                                    {"name": "To", "value": "me@x.com"}, {"name": "Subject", "value": "두 번째"}],
                        "parts": [{"mimeType": "text/html", "body": {"data": _b64("<b>html</b>")}},
                                  {"mimeType": "text/plain", "body": {"data": _b64(f" 본문 {mid} ")}}]}}


def _gets(routes: dict) -> AsyncMock:
    async def get(url, **kwargs):
        if url not in routes:
            raise AssertionError(f"예상 못 한 GET {url}")
        return routes[url]
    return AsyncMock(side_effect=get)


async def _tool(fn, client, **kwargs) -> dict:
    with patch("tools.gmail.get_http_client", return_value=client):
        return json.loads(await fn(access_token="token", **kwargs))


@pytest.mark.asyncio
async def test_메일_찾기는_목록_뒤_full로_가져와_정규화한다():
    get = _gets({f"{API}/messages": _make_mock_response(200, {"messages": [{"id": "m1", "threadId": "t1"}]}),
                 f"{API}/messages/m1": _make_mock_response(200, _msg("m1"))})
    result = await _tool(gmail_find_email, _client(get=get), query=" from:boss ")
    assert (result["found"], result["total"]) == (True, 1)
    assert result["items"][0] == {"messageId": "m1", "threadId": "t1", "from": "홍길동 <a@b.c>", "to": "me@x.com",
                                  "subject": "회의", "snippet": "미리보기", "bodyText": "본문 m1",
                                  "receivedAt": "2025-11-10T00:00:00Z", "labels": ["INBOX", "UNREAD"]}
    listing, detail = get.await_args_list
    assert listing.kwargs["params"] == {"q": "from:boss", "maxResults": 20}
    assert detail.kwargs["params"] == {"format": "full"}
    assert listing.kwargs["headers"]["Authorization"] == "Bearer token"
    assert_matches_outputs("gmail.find_email", json.dumps(result))


@pytest.mark.asyncio
async def test_메일_찾기_0건은_성공이다():
    get = _gets({f"{API}/messages": _make_mock_response(200, {"resultSizeEstimate": 0})})
    result = await _tool(gmail_find_email, _client(get=get), query="from:nobody")
    assert result == {"success": True, "found": False, "total": 0, "items": []}
    assert get.await_count == 1
    assert_matches_outputs("gmail.find_email", json.dumps(result))


@pytest.mark.asyncio
async def test_찾기_중_지워진_메일은_건너뛴다():          # Review Focus 2
    get = _gets({f"{API}/messages": _make_mock_response(200, {"messages": [{"id": "m1"}, {"id": "m2"}]}),
                 f"{API}/messages/m1": _make_mock_response(404, {"error": {"message": "Not Found"}}),
                 f"{API}/messages/m2": _make_mock_response(200, _msg("m2"))})
    result = await _tool(gmail_find_email, _client(get=get), query="x")
    assert result["total"] == 1 and result["items"][0]["messageId"] == "m2"


@pytest.mark.asyncio
async def test_찾기_중_상세_조회가_404가_아니면_실패한다():
    get = _gets({f"{API}/messages": _make_mock_response(200, {"messages": [{"id": "m1"}]}),
                 f"{API}/messages/m1": _make_mock_response(503, {"error": {"message": "backend"}})})
    result = await _tool(gmail_find_email, _client(get=get), query="x")
    assert "Gmail API 오류 (503)" in result["error"]


@pytest.mark.asyncio
async def test_라벨_스팸휴지통_조건을_쿼리로_보낸다():
    get = _gets({f"{API}/messages": _make_mock_response(200, {})})
    await _tool(gmail_find_email, _client(get=get), query="x", label_ids="INBOX,STARRED", include_spam_trash="true")
    params = get.await_args_list[0].kwargs["params"]
    assert params["labelIds"] == ["INBOX", "STARRED"] and params["includeSpamTrash"] == "true"


@pytest.mark.asyncio
async def test_스팸휴지통_포함이_true_false가_아니면_실패한다():
    get = AsyncMock()
    result = await _tool(gmail_find_email, _client(get=get), query="x", include_spam_trash="yes")
    assert "include_spam_trash" in result["error"] and get.await_count == 0


def _paged_gets():
    """목록은 한 페이지 최대 30개, 끝없이 다음 페이지가 있다. 상세는 아무 ID나 돌려준다."""
    counter = iter(range(10_000))

    async def get(url, **kwargs):
        if url == f"{API}/messages":
            size = min(kwargs["params"]["maxResults"], 30)
            return _make_mock_response(200, {"messages": [{"id": f"m{next(counter)}"} for _ in range(size)],
                                             "nextPageToken": "next"})
        return _make_mock_response(200, _msg(url.rsplit("/", 1)[1]))
    return AsyncMock(side_effect=get)


@pytest.mark.asyncio
@pytest.mark.parametrize("value, expected", [("0", 1), ("500", 100), (None, 20), ("", 20), ("  ", 20), ("7", 7), (45, 45)])
async def test_최대_개수는_1에서_100으로_자르고_숫자가_아니면_실패한다(value, expected):   # Review Focus 4
    get = _paged_gets()
    result = await _tool(gmail_find_email, _client(get=get), query="x", max_results=value)
    assert result["total"] == expected
    listings = [c for c in get.await_args_list if c.args[0] == f"{API}/messages"]
    assert all(c.kwargs["params"]["maxResults"] <= 100 for c in listings)
    assert listings[-1].kwargs["params"].get("pageToken", None) == (None if len(listings) == 1 else "next")


@pytest.mark.asyncio
async def test_최대_개수가_숫자가_아니면_HTTP_없이_실패한다():
    get = AsyncMock()
    result = await _tool(gmail_find_email, _client(get=get), query="x", max_results="abc")
    assert "error" in result and get.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["  ", "", None])
async def test_빈_검색어는_HTTP_없이_실패한다(query):
    get = AsyncMock()
    result = await _tool(gmail_find_email, _client(get=get), query=query)
    assert "error" in result and get.await_count == 0


def _huge(mid: str) -> dict:
    """이모지 본문 10,000자(40KB) + 받는 사람 100명 + 1,500자 제목."""
    message = _msg(mid)
    message["payload"] = {"mimeType": "text/plain", "body": {"data": _b64("😀" * 10_000)},
                          "headers": [{"name": "To", "value": ", ".join(f"user{i}@example.com" for i in range(100))},
                                      {"name": "Subject", "value": "가" * 1_500}]}
    return message


@pytest.mark.asyncio
async def test_찾기_출력은_헤더_1000자_전체_2MB로_묶는다():   # 최종 리뷰 I1
    ids = [f"m{i}" for i in range(100)]
    get = _gets({f"{API}/messages": _make_mock_response(200, {"messages": [{"id": i} for i in ids]}),
                 **{f"{API}/messages/{i}": _make_mock_response(200, _huge(i)) for i in ids}})
    with patch("tools.gmail.get_http_client", return_value=_client(get=get)):
        raw = await gmail_find_email(access_token="token", query="x", max_results=100)
    assert len(raw.encode("utf-8")) < 4_194_304
    result = json.loads(raw)
    assert result["total"] == 100 and len(result["items"]) == 100
    first, last = result["items"][0], result["items"][-1]
    assert first["bodyText"] == "😀" * 10_000
    assert len(first["subject"]) == 1_000 and len(first["to"]) == 1_000
    assert len(last["bodyText"]) < 10_000 and set(last) == set(first)
    assert_matches_outputs("gmail.find_email", raw)


@pytest.mark.asyncio
async def test_대화_출력도_2MB로_묶는다():
    thread = {"id": "t1", "messages": [_huge(f"m{i}") for i in range(100)]}
    get = _gets({f"{API}/threads/t1": _make_mock_response(200, thread)})
    with patch("tools.gmail.get_http_client", return_value=_client(get=get)):
        raw = await gmail_get_thread(access_token="token", thread_id="t1")
    result = json.loads(raw)
    assert len(raw.encode("utf-8")) < 4_194_304 and result["total"] == 100
    assert result["items"][0]["bodyText"] == "😀" * 10_000


@pytest.mark.parametrize("html", ["<style>" * 40_000, "<a " * 66_667, "<!--" * 50_000, "a<b" * 66_667,
                                  "<" * 80_000, "<script>" * 40_000 + "a" * 300_000],
                         ids=["unclosed-style", "open-attr", "comment", "a-lt-b", "lt-only", "unclosed-script"])
def test_적대적_HTML_본문도_1초_안에_끝난다(html):   # C1 + 재리뷰 — HTMLParser는 3.11.13 미만에서 제곱(CVE-2025-6069)
    import time
    message = {"payload": {"mimeType": "text/html", "body": {"data": _b64(html)}}}
    start = time.perf_counter()
    _normalize(message)
    assert time.perf_counter() - start < 1.0


# BE GmailMessageNormalizer와 같은 입력·기대값(orca-specs 2026-11-10-html-scanner-algo.md)
@pytest.mark.parametrize("html, text", [
    ("<p>안녕</p><style>p{}</style>&amp;반가워", "안녕 &반가워"),
    ("a<style>p{}</style >b", "a b"),
    ("a<styles>b</styles>c", "a b c"),
    ("a<STYLE>x</STYLE>b", "a b"),
    ("a<script src=x>alert(1)</SCRIPT>b", "a b"),
    ("a<style/>x</style>b", "a b"),
    ("a<!--[if mso]>x<![endif]-->b", "a x b"),
    ("a<![foo[x]]>b", "a b"),
    ("a&rsquo;b", "a&rsquo;b"),
    ("&amp;lt; &nbsp;&lt;&gt;&quot;&#39;", "&lt; <>\"'"),
    ("a<>b", "a<>b"),
    ("a<b", "a<b"),
    ("a<style>끝없음", "a"),
    ("a<style>x</style", "a"),
])
def test_HTML_스캐너는_BE와_같은_결과를_낸다(html, text):
    assert _strip_html(html) == text


def test_HTML_본문은_200000자까지만_읽는다():
    text = _strip_html("가" * 200_000 + "나")
    assert len(text) == 200_000 and "나" not in text


def test_출력_예산을_1바이트_넘으면_마지막_본문만_1바이트_준다():   # 재리뷰 M — 따옴표 2바이트
    item = _normalize({"id": "m1"})
    rest = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))   # bodyText ""
    item["bodyText"] = "a" * (_OUTPUT_MAX + 1 - rest)
    original = len(item["bodyText"])
    assert len(_fit([item])[0]["bodyText"]) == original - 1


def test_출력_예산을_넘으면_뒤쪽만_비고_순서가_뒤집히지_않는다():
    items = [dict(_normalize({"id": f"m{i}"}), bodyText="가" * 10_000) for i in range(100)]
    bodies = [len(i["bodyText"]) for i in _fit(items)]
    full = bodies.index(next(b for b in bodies if b < 10_000))
    assert all(b == 10_000 for b in bodies[:full])
    assert all(b == 0 for b in bodies[full + 1:])          # 줄어든 항목 뒤에 본문 있는 항목이 없다
    # 경계 항목까지가 예산 안(뒤 항목은 본문만 비고 헤더는 남는다)
    used = sum(len(json.dumps(i, ensure_ascii=False).encode("utf-8")) for i in items[:full + 1])
    assert _OUTPUT_MAX - 3 <= used <= _OUTPUT_MAX and bodies[full] > 0


def test_정규화는_plain_우선_html_대체_본문_자르기_receivedAt():
    html = {"mimeType": "text/html", "body": {"data": _b64(
        "<style>p{}</style><p>안녕&nbsp;&amp;\n  <b>반가워</b></p><SCRIPT>x()</SCRIPT>")}}
    assert _normalize({"payload": html})["bodyText"] == "안녕 & 반가워"
    long = {"mimeType": "text/plain", "body": {"data": _b64("가" * 20_000)}}
    assert len(_normalize({"payload": long})["bodyText"]) == 10_000
    assert _normalize({"internalDate": "1762732800000"})["receivedAt"] == "2025-11-10T00:00:00Z"
    assert _normalize({"internalDate": "1762732800123"})["receivedAt"] == "2025-11-10T00:00:00.123Z"
    assert _normalize({})["receivedAt"] == "1970-01-01T00:00:00Z"
    euc = {"mimeType": "text/plain", "body": {"data": _b64("한글".encode("euc-kr"))}}
    assert "�" in _normalize({"payload": euc})["bodyText"]
    nested = {"mimeType": "multipart/mixed", "parts": [
        {"mimeType": "text/plain", "body": {"data": "!!!깨짐"}},
        {"mimeType": "multipart/alternative", "parts": [{"mimeType": "text/plain", "body": {"data": _b64("abcd")}}]}]}
    assert _normalize({"payload": nested})["bodyText"] == "abcd"   # 깨진 파트는 건너뛰고 다음 plain(BE와 같다)
    assert _normalize({}) == {"messageId": "", "threadId": "", "from": "", "to": "", "subject": "", "snippet": "",
                              "bodyText": "", "receivedAt": "1970-01-01T00:00:00Z", "labels": []}


@pytest.mark.asyncio
async def test_대화는_메일을_모두_정규화한다():
    thread = {"id": "t1", "messages": [_msg("m1"), _msg("m2")]}
    get = _gets({f"{API}/threads/t1": _make_mock_response(200, thread)})
    result = await _tool(gmail_get_thread, _client(get=get), thread_id=" t1 ")
    assert (result["threadId"], result["total"]) == ("t1", 2)
    assert [i["messageId"] for i in result["items"]] == ["m1", "m2"]
    assert get.await_args_list[0].kwargs["params"] == {"format": "full"}
    assert_matches_outputs("gmail.get_thread", json.dumps(result))


@pytest.mark.asyncio
async def test_대화_404는_실패다():
    get = _gets({f"{API}/threads/t1": _make_mock_response(404, {"error": {"message": "Requested entity was not found."}})})
    result = await _tool(gmail_get_thread, _client(get=get), thread_id="t1")
    assert "Gmail API 오류 (404)" in result["error"]


@pytest.mark.asyncio
async def test_403은_Gmail_권한_안내를_붙인다():
    get = _gets({f"{API}/messages": _make_mock_response(403, {"error": {"message": "insufficient"}})})
    result = await _tool(gmail_find_email, _client(get=get), query="x")
    assert "Gmail API 오류 (403)" in result["error"] and _SCOPE_HINT in result["error"]


# ── 라벨·보관·휴지통·라벨 만들기(C6~C11) ─────────────────────────────────

MODIFIED = {"id": "m1", "threadId": "t1", "labelIds": ["INBOX", "STARRED"]}


def _post_ok(body=MODIFIED) -> AsyncMock:
    return AsyncMock(return_value=_make_mock_response(200, body))


@pytest.mark.asyncio
@pytest.mark.parametrize("fn, key", [(gmail_add_label, "addLabelIds"), (gmail_remove_label, "removeLabelIds")])
async def test_라벨_붙이기와_떼기는_modify를_부른다(fn, key):
    post = _post_ok()
    result = await _tool(fn, _client(post=post), message_id="m1", label_ids=["STARRED"])
    assert post.await_args.args[0] == f"{API}/messages/m1/modify"
    assert post.await_args.kwargs["json"] == {key: ["STARRED"]}
    assert result == {"success": True, "messageId": "m1", "threadId": "t1", "labels": ["INBOX", "STARRED"]}
    # test_every_action_has_output_match_assertion이 event 리터럴을 찾는다
    if fn is gmail_add_label:
        assert_matches_outputs("gmail.add_label", json.dumps(result))
    else:
        assert_matches_outputs("gmail.remove_label", json.dumps(result))


@pytest.mark.asyncio
@pytest.mark.parametrize("labels", ["A, B", ["A", "B"], '["A","B"]', "A,,B ,"])
async def test_라벨은_쉼표_목록_JSON을_받는다(labels):
    post = _post_ok()
    await _tool(gmail_add_label, _client(post=post), message_id="m1", label_ids=labels)
    assert post.await_args.kwargs["json"] == {"addLabelIds": ["A", "B"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("fn, kwargs", [(gmail_add_label, {"message_id": "m1"}),
                                        (gmail_remove_label, {"message_id": "m1"}),
                                        (gmail_remove_thread_label, {"thread_id": "t1"})])
@pytest.mark.parametrize("labels", [" , ", "[]", '[""]', None, [], [f"L{i}" for i in range(101)]])
async def test_라벨은_쉼표_목록_JSON을_받고_비면_HTTP_없이_실패한다(fn, kwargs, labels):   # Review Focus 5
    post = AsyncMock()
    result = await _tool(fn, _client(post=post), label_ids=labels, **kwargs)
    assert "error" in result and post.await_count == 0


@pytest.mark.asyncio
async def test_대화에서_라벨_떼기():
    post = _post_ok({"id": "t1", "messages": [MODIFIED]})
    result = await _tool(gmail_remove_thread_label, _client(post=post), thread_id="t1", label_ids="UNREAD")
    assert post.await_args.args[0] == f"{API}/threads/t1/modify"
    assert post.await_args.kwargs["json"] == {"removeLabelIds": ["UNREAD"]}
    assert result == {"success": True, "threadId": "t1"}
    assert_matches_outputs("gmail.remove_thread_label", json.dumps(result))


@pytest.mark.asyncio
async def test_보관은_INBOX를_뗀다():
    post = _post_ok({"id": "m1", "threadId": "t1", "labelIds": ["IMPORTANT"]})
    result = await _tool(gmail_archive, _client(post=post), message_id="m1")
    assert post.await_args.args[0] == f"{API}/messages/m1/modify"
    assert post.await_args.kwargs["json"] == {"removeLabelIds": ["INBOX"]}
    assert result == {"success": True, "messageId": "m1", "threadId": "t1", "labels": ["IMPORTANT"]}
    assert_matches_outputs("gmail.archive", json.dumps(result))


@pytest.mark.asyncio
async def test_라벨이_하나도_안_남으면_labels는_빈_목록():
    result = await _tool(gmail_archive, _client(post=_post_ok({"id": "m1", "threadId": "t1"})), message_id="m1")
    assert result["labels"] == []


@pytest.mark.asyncio
async def test_휴지통_이동():
    post = _post_ok({"id": "m1", "threadId": "t1", "labelIds": ["TRASH"]})
    result = await _tool(gmail_trash, _client(post=post), message_id="m1")
    assert post.await_args.args[0] == f"{API}/messages/m1/trash"
    assert result == {"success": True, "messageId": "m1", "threadId": "t1"}
    assert_matches_outputs("gmail.trash", json.dumps(result))


_ID_TOOLS = [(gmail_get_thread, "thread_id", {}, "Gmail 대화 ID가 아니에요"),
             (gmail_add_label, "message_id", {"label_ids": ["A"]}, "Gmail 메일 ID가 아니에요"),
             (gmail_remove_label, "message_id", {"label_ids": ["A"]}, "Gmail 메일 ID가 아니에요"),
             (gmail_remove_thread_label, "thread_id", {"label_ids": ["A"]}, "Gmail 대화 ID가 아니에요"),
             (gmail_archive, "message_id", {}, "Gmail 메일 ID가 아니에요"),
             (gmail_trash, "message_id", {}, "Gmail 메일 ID가 아니에요")]


@pytest.mark.asyncio
@pytest.mark.parametrize("fn, key, extra, message", _ID_TOOLS)
@pytest.mark.parametrize("bad", ["../labels", "a/b", "<x@mail.gmail.com>", "m1?x=1", "  ", None, "메일1", "m 1"])
async def test_경로_ID_형식이_아니면_HTTP_없이_실패한다(fn, key, extra, message, bad):   # Review Focus 1
    get, post = AsyncMock(), AsyncMock()
    result = await _tool(fn, _client(get=get, post=post), **{key: bad}, **extra)
    assert message in result["error"] and get.await_count == post.await_count == 0


@pytest.mark.asyncio
async def test_라벨_만들기는_빈_칸을_빼고_보낸다():
    post = _post_ok({"id": "Label_9", "name": "프로젝트/A"})
    result = await _tool(gmail_create_label, _client(post=post), name=" 프로젝트/A ", label_list_visibility="",
                         message_list_visibility=None, text_color="  ", background_color="")
    assert post.await_args.args[0] == f"{API}/labels"
    assert post.await_args.kwargs["json"] == {"name": "프로젝트/A"}
    assert result == {"success": True, "labelId": "Label_9", "name": "프로젝트/A"}
    assert_matches_outputs("gmail.create_label", json.dumps(result))


@pytest.mark.asyncio
async def test_라벨_만들기_전_칸():
    post = _post_ok({"id": "Label_9", "name": "A"})
    await _tool(gmail_create_label, _client(post=post), name="A", label_list_visibility="labelHide",
                message_list_visibility="hide", text_color="#000000", background_color="#ffffff")
    assert post.await_args.kwargs["json"] == {"name": "A", "labelListVisibility": "labelHide",
                                              "messageListVisibility": "hide",
                                              "color": {"textColor": "#000000", "backgroundColor": "#ffffff"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("colors", [{"text_color": "#000000"}, {"background_color": "#ffffff", "text_color": " "}])
async def test_색은_둘_다_있거나_둘_다_없어야_한다(colors):
    post = AsyncMock()
    result = await _tool(gmail_create_label, _client(post=post), name="A", **colors)
    assert "error" in result and post.await_count == 0


@pytest.mark.asyncio
async def test_라벨_이름이_비면_HTTP_없이_실패한다():
    post = AsyncMock()
    result = await _tool(gmail_create_label, _client(post=post), name="  ")
    assert "error" in result and post.await_count == 0


@pytest.mark.asyncio
async def test_라벨_이름_중복_409는_Google_메시지로_실패():
    post = AsyncMock(return_value=_make_mock_response(409, {"error": {"message": "Label name exists or conflicts"}}))
    result = await _tool(gmail_create_label, _client(post=post), name="A")
    assert "Gmail API 오류 (409): Label name exists or conflicts" in result["error"]


@pytest.mark.asyncio
async def test_쓰기_403은_Gmail_권한_안내를_붙인다():
    post = AsyncMock(return_value=_make_mock_response(403, {"error": {"message": "insufficient"}}))
    result = await _tool(gmail_add_label, _client(post=post), message_id="m1", label_ids=["A"])
    assert "Gmail API 오류 (403)" in result["error"] and _SCOPE_HINT in result["error"]


@pytest.mark.asyncio
async def test_쓰기_예외도_error_JSON이다():
    post = AsyncMock(side_effect=httpx.ConnectTimeout("t"))
    assert "error" in await _tool(gmail_trash, _client(post=post), message_id="m1")

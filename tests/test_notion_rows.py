"""Notion DB 행 도구(spec 2026-10-26 §7). HTTP는 test_notion_tools.py와 같은 꼴 — 공유 클라이언트를 mock으로 바꾸고
메서드별 응답을 차례로 준다."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.error_code import ToolErrorCode
from tests.action_outputs import assert_matches_outputs
from tools.notion_rows import notion_create_row, notion_update_row

API = "https://api.notion.com/v1"
DS = "1f2e3d4c5b6a47988796a5b4c3d2e1f0"
ROW = "2a2b2c2d-3e3f-4a4b-8c8d-9e9f0a0b0c0d"
SCHEMA = {"object": "data_source", "id": DS, "properties": {
    "이름": {"id": "title", "name": "이름", "type": "title", "title": {}},
    "메모": {"id": "m%3Bx", "name": "메모", "type": "rich_text", "rich_text": {}},
    "점수": {"id": "num", "name": "점수", "type": "number", "number": {"format": "number"}},
    "완료": {"id": "chk", "name": "완료", "type": "checkbox", "checkbox": {}},
    "분류": {"id": "sel", "name": "분류", "type": "select",
           "select": {"options": [{"id": "opt1", "name": "업무", "color": "red"}]}},
    "상태": {"id": "sts", "name": "상태", "type": "status",
           "status": {"options": [{"id": "st1", "name": "진행 중"}, {"id": "st2", "name": "완료"}]}},
    "태그": {"id": "Xy.z", "name": "태그", "type": "multi_select",
           "multi_select": {"options": [{"id": "tg1", "name": "긴급"}]}},
    "담당": {"id": "ppl", "name": "담당", "type": "people", "people": {}},
    "프로젝트": {"id": "rel", "name": "프로젝트", "type": "relation",
             "relation": {"data_source_id": "9f8e7d6c5b4a39281706f5e4d3c2b1a0", "type": "single_property"}},
    "마감": {"id": "due", "name": "마감", "type": "date", "date": {}},
    "링크": {"id": "lnk", "name": "링크", "type": "url", "url": {}},
    "메일": {"id": "eml", "name": "메일", "type": "email", "email": {}},
    "전화": {"id": "tel", "name": "전화", "type": "phone_number", "phone_number": {}},
    "합계": {"id": "fx", "name": "합계", "type": "formula", "formula": {"expression": "1"}},
}}
PAGE = {"object": "page", "id": ROW, "url": f"https://www.notion.so/{ROW}",
        "parent": {"type": "data_source_id", "data_source_id": DS, "database_id": "d" * 32}}
CREATED = {"object": "page", "id": "new-page", "url": "https://www.notion.so/new-page"}


def _make_response(status_code: int, data: dict, headers: dict | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = data
    response.headers = headers or {}
    return response


def _client(get=None, post=None, patch_=None) -> MagicMock:
    """메서드별 응답 목록(차례로 준다). 부르지 않을 메서드는 부르면 실패한다."""
    client = MagicMock()
    for name, responses in (("get", get), ("post", post), ("patch", patch_)):
        setattr(client, name, AsyncMock(side_effect=list(responses) if responses is not None
                                        else AssertionError(f"{name} 호출 없음")))
    return client


def _ok(data):
    return _make_response(200, data)


async def _create(client, **kwargs) -> dict:
    kwargs.setdefault("data_source_id", DS)
    with patch("tools.notion_rows.get_http_client", return_value=client):
        return json.loads(await notion_create_row(token="secret_test", **kwargs))


async def _update(client, **kwargs) -> dict:
    kwargs.setdefault("row_id", ROW)
    with patch("tools.notion_rows.get_http_client", return_value=client):
        return json.loads(await notion_update_row(token="secret_test", **kwargs))


def _sent(client, method="post", index=-1) -> dict:
    return getattr(client, method).await_args_list[index].kwargs["json"]


# --- 만들기 흐름 -----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_만들기는_스키마를_읽고_data_source_부모로_행을_만든다():
    client = _client(get=[_ok(SCHEMA)], post=[_ok(CREATED)])

    result = await _create(client, properties={"이름": "회의록", "점수": 3})

    assert result == {"success": True, "pageId": "new-page", "url": "https://www.notion.so/new-page"}
    assert_matches_outputs("notion.create_row", result)
    assert client.get.await_args.args[0] == f"{API}/data_sources/{DS}"
    assert client.post.await_args.args[0] == f"{API}/pages"
    assert client.post.await_args.kwargs["headers"]["Notion-Version"] == "2026-03-11"
    assert _sent(client) == {
        "parent": {"type": "data_source_id", "data_source_id": DS},
        "properties": {"title": {"title": [{"type": "text", "text": {"content": "회의록"}}]},
                       "num": {"number": 3}},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("key, value, expected", [
    ("이름", "안녕", {"title": {"title": [{"type": "text", "text": {"content": "안녕"}}]}}),
    ("메모", "가" * 2001, {"m%3Bx": {"rich_text": [{"type": "text", "text": {"content": "가" * 2000}},
                                                {"type": "text", "text": {"content": "가"}}]}}),
    ("메모", 42, {"m%3Bx": {"rich_text": [{"type": "text", "text": {"content": "42"}}]}}),
    ("점수", 2.5, {"num": {"number": 2.5}}),
    ("점수", " 7 ", {"num": {"number": 7}}),                      # 참조식 치환 결과는 문자열이다
    ("점수", "-1.25", {"num": {"number": -1.25}}),
    ("완료", True, {"chk": {"checkbox": True}}),
    ("완료", "TRUE", {"chk": {"checkbox": True}}),
    ("완료", "false", {"chk": {"checkbox": False}}),
    ("분류", "opt1", {"sel": {"select": {"id": "opt1"}}}),           # 드롭다운 = 옵션 ID
    ("분류", "업무", {"sel": {"select": {"name": "업무"}}}),           # 참조식·AI = 옵션 이름
    ("분류", "새 분류", {"sel": {"select": {"name": "새 분류"}}}),     # 없는 이름 → Notion이 만든다(D8)
    ("상태", "st2", {"sts": {"status": {"id": "st2"}}}),
    ("상태", "진행 중", {"sts": {"status": {"name": "진행 중"}}}),
    ("태그", ["tg1", "새 태그"], {"Xy.z": {"multi_select": [{"id": "tg1"}, {"name": "새 태그"}]}}),
    ("태그", '["긴급", "보류"]', {"Xy.z": {"multi_select": [{"name": "긴급"}, {"name": "보류"}]}}),
    ("태그", "긴급, 보류", {"Xy.z": {"multi_select": [{"name": "긴급"}, {"name": "보류"}]}}),
    ("담당", ["u-1", "u-2"], {"ppl": {"people": [{"id": "u-1"}, {"id": "u-2"}]}}),
    ("담당", "u-1,u-2", {"ppl": {"people": [{"id": "u-1"}, {"id": "u-2"}]}}),
    ("프로젝트", '["p-1"]', {"rel": {"relation": [{"id": "p-1"}]}}),
    ("마감", "2026-11-01", {"due": {"date": {"start": "2026-11-01"}}}),
    ("마감", "2026-11-01T09:00", {"due": {"date": {"start": "2026-11-01T09:00", "time_zone": "Asia/Seoul"}}}),
    ("마감", "2026-11-01T09:00:00+09:00", {"due": {"date": {"start": "2026-11-01T09:00:00+09:00"}}}),
    ("마감", "2026-11-01T00:00:00.000Z", {"due": {"date": {"start": "2026-11-01T00:00:00.000Z"}}}),
    ("마감", {"start": "2026-11-01", "end": "2026-11-03"},
     {"due": {"date": {"start": "2026-11-01", "end": "2026-11-03"}}}),
    ("마감", {"start": "2026-11-01", "end": ""}, {"due": {"date": {"start": "2026-11-01"}}}),
    ("마감", {"start": "2026-11-01T09:00", "end": "2026-11-01T10:00"},
     {"due": {"date": {"start": "2026-11-01T09:00", "end": "2026-11-01T10:00", "time_zone": "Asia/Seoul"}}}),
    ("링크", "https://example.com/a", {"lnk": {"url": "https://example.com/a"}}),
    ("메일", "a@b.co", {"eml": {"email": "a@b.co"}}),
    ("전화", "010-1234-5678", {"tel": {"phone_number": "010-1234-5678"}}),
])
async def test_값_변환표(key, value, expected):
    client = _client(get=[_ok(SCHEMA)], post=[_ok(CREATED)])

    result = await _create(client, properties={key: value})

    assert result["success"] is True
    assert _sent(client)["properties"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("key, value, words", [
    ("점수", "abc", ["점수", "숫자"]),
    ("점수", True, ["점수", "숫자"]),                 # bool은 int의 하위형이지만 숫자가 아니다
    ("점수", "nan", ["점수", "숫자"]),
    ("완료", "yes", ["완료", "true 또는 false"]),
    ("상태", "보류", ["상태", "보류", "진행 중, 완료"]),  # status는 API로 옵션을 못 만든다(D8)
    ("태그", [f"t{i}" for i in range(101)], ["태그", "100개"]),
    ("담당", ",".join(f"u{i}" for i in range(101)), ["담당", "100개"]),
    ("링크", "https://x.io/" + "a" * 2000, ["링크", "2,000자"]),
    ("메일", "a" * 201, ["메일", "200자"]),
    ("합계", "1", ["합계", "쓸 수 없어요(formula)"]),
    ("분류", {"name": "업무"}, ["분류", "객체"]),       # Notion 형식 값을 빈 값으로 버리지 않는다(D7)
    ("태그", [{"name": "x"}], ["태그", "객체"]),        # 목록 안 객체를 str(dict)로 보내지 않는다
    ("분류", ["업무"], ["분류", "목록"]),               # str(list)가 영구 옵션 "['업무']"이 되지 않게(D8)
])
async def test_변환_실패는_행을_만들지_않는다(key, value, words):
    client = _client(get=[_ok(SCHEMA)])

    result = await _create(client, properties={key: value})

    assert all(w in result["error"] for w in words), result
    assert client.post.await_count == 0


# --- 키 해석 -----------------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("key, sent", [
    ("num", "num"),            # 저장 키 = 속성 ID
    ("Xy%2Ez", "Xy.z"),        # D5 정규화 키 → 스키마의 원본 ID로 보낸다
    ("m%3Bx", "m%3Bx"),        # 이미 URL 인코딩된 ID는 그대로
    ("점수", "num"),            # AI = 속성 이름
])
async def test_키는_정규화_ID_다음_이름으로_찾고_원본_ID로_보낸다(key, sent):
    client = _client(get=[_ok(SCHEMA)], post=[_ok(CREATED)])

    await _create(client, properties={key: "5" if sent == "num" else "x"})

    assert list(_sent(client)["properties"]) == [sent]


@pytest.mark.asyncio
async def test_없는_속성은_실패하고_쓸_수_있는_속성_목록을_알려_준다():
    """D7·D9 — 지운 속성에 쓰던 값을 조용히 버리지 않는다. 목록은 AI 재시도용이다."""
    client = _client(get=[_ok(SCHEMA)])

    result = await _create(client, properties={"Title": "회의록"})

    assert "'Title' 속성이 이 데이터베이스에 없어요" in result["error"]
    assert "이름(title)" in result["error"] and "마감(date)" in result["error"]
    assert "합계(formula)" not in result["error"]
    assert client.post.await_count == 0


@pytest.mark.asyncio
async def test_안쪽_names_키는_속성으로_보고_실패한다():
    """spec §6 — properties 안쪽엔 _names 규칙이 없다. dict 값이라고 빈 값으로 버리지 않는다(D7)."""
    client = _client(get=[_ok(SCHEMA)])

    result = await _create(client, properties={"_names": {"title": "이름"}})

    assert "'_names' 속성이 이 데이터베이스에 없어요" in result["error"]
    assert client.post.await_count == 0


# --- 빈 값 -----------------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", "  ", [], {}, {"start": ""}, {"start": None, "end": "2026-11-02"}, "[]"])
async def test_빈_값은_요청_본문에_넣지_않는다(value):
    client = _client(get=[_ok(SCHEMA)], post=[_ok(CREATED)])
    key = "마감" if isinstance(value, dict) else "태그"

    result = await _create(client, properties={"이름": "제목", key: value})

    assert result["success"] is True
    assert set(_sent(client)["properties"]) == {"title"}


@pytest.mark.asyncio
async def test_빈_값이면_스키마에_없는_키여도_실패하지_않는다():
    """비워 둔 칸의 속성을 DB에서 지워도 잃는 값이 없다 — D7은 값이 있을 때만(spec과 다른 점 2)."""
    client = _client(get=[_ok(SCHEMA)], post=[_ok(CREATED)])

    result = await _create(client, properties={"지운속성": "", "이름": "제목"})

    assert result["success"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("properties", ["{\"이름\": \"x\"}", ["이름"], 3])
async def test_properties가_객체가_아니면_부르지_않는다(properties):
    client = _client()

    assert await _create(client, properties=properties) == {"error": "속성은 객체여야 해요"}
    assert await _update(client, properties=properties) == {"error": "속성은 객체여야 해요"}


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", None, "../users", f"{DS}/../../users", f"{DS}?x=1", "g" * 32, "1" * 31])
async def test_ID가_Notion_ID가_아니면_경로에_끼우지_않는다(bad):
    client = _client()

    assert "Notion ID 형식이 아니에요" in (await _create(client, data_source_id=bad))["error"]
    assert "Notion ID 형식이 아니에요" in (await _update(client, row_id=bad, content="x"))["error"]


# --- 템플릿·본문 -----------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("template, expected", [
    (None, None),
    ("", None),
    ("default", {"type": "default"}),
    (" tpl-1 ", {"type": "template_id", "template_id": "tpl-1"}),
])
async def test_템플릿_세_가지(template, expected):
    client = _client(get=[_ok(SCHEMA)], post=[_ok(CREATED)])

    await _create(client, template=template)

    assert _sent(client).get("template") == expected


@pytest.mark.asyncio
async def test_기본_템플릿이_없으면_안내한다():
    client = _client(get=[_ok(SCHEMA)], post=[_make_response(400, {"message": "validation_error"})])

    result = await _create(client, template="default")

    assert result["error"].startswith("Notion API 오류 (400): validation_error")
    assert "이 DB에 기본 템플릿이 없어요" in result["error"]


@pytest.mark.asyncio
async def test_템플릿_ID의_400에는_기본_템플릿_안내를_붙이지_않는다():
    client = _client(get=[_ok(SCHEMA)], post=[_make_response(400, {"message": "bad"})])

    result = await _create(client, template="tpl-1")

    assert result == {"error": "Notion API 오류 (400): bad"}


@pytest.mark.asyncio
async def test_템플릿과_본문을_같이_주면_부르지_않는다():
    """D10 — dev e2e로 확인하기 전까지 1차 거절."""
    client = _client()

    result = await _create(client, template="default", content="# 본문")

    assert "템플릿과 본문은 같이 쓸 수 없어요" in result["error"]


@pytest.mark.asyncio
async def test_본문은_markdown으로_보낸다():
    client = _client(get=[_ok(SCHEMA)], post=[_ok(CREATED)])

    await _create(client, content="# 제목\n- 항목")

    assert _sent(client)["markdown"] == "# 제목\n- 항목" and "children" not in _sent(client)


@pytest.mark.asyncio
async def test_스키마_조회_실패는_Notion_오류로_돌려준다():
    client = _client(get=[_make_response(404, {"message": "Could not find data_source"})])

    assert await _create(client) == {"error": "Notion API 오류 (404): Could not find data_source"}


@pytest.mark.asyncio
async def test_네트워크_예외는_실행_실패_문구로_돌려준다():
    client = _client(get=[_ok(SCHEMA)], post=[RuntimeError("timed out")])

    result = await _create(client)

    assert result["error"].startswith(ToolErrorCode.EXECUTION_FAILED.message)
    assert "notion_create_row" in result["error"]


# --- 수정 흐름 -----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_수정은_행의_DB_스키마로_속성과_본문을_바꾼다():
    client = _client(get=[_ok(PAGE), _ok(SCHEMA)], patch_=[_ok(PAGE), _ok({"object": "page_markdown"})])

    result = await _update(client, properties={"상태": "완료", "점수": ""}, content="새 본문")

    assert result == {"success": True, "pageId": ROW, "url": PAGE["url"]}
    assert_matches_outputs("notion.update_row", result)
    assert [c.args[0] for c in client.get.await_args_list] == [f"{API}/pages/{ROW}", f"{API}/data_sources/{DS}"]
    assert [c.args[0] for c in client.patch.await_args_list] == [f"{API}/pages/{ROW}", f"{API}/pages/{ROW}/markdown"]
    assert _sent(client, "patch", 0) == {"properties": {"sts": {"status": {"name": "완료"}}}}  # 빈 값 = 그대로
    # D12: allow_deleting_content를 보내지 않는다
    assert _sent(client, "patch", 1) == {"type": "replace_content", "replace_content": {"new_str": "새 본문"}}


@pytest.mark.asyncio
async def test_수정_속성만이면_본문을_부르지_않는다():
    client = _client(get=[_ok(PAGE), _ok(SCHEMA)], patch_=[_ok(PAGE)])

    result = await _update(client, properties={"완료": "true"})

    assert result["success"] is True and client.patch.await_count == 1


@pytest.mark.asyncio
async def test_수정_본문만이면_속성을_부르지_않는다():
    client = _client(get=[_ok(PAGE), _ok(SCHEMA)], patch_=[_ok({"object": "page_markdown"})])

    result = await _update(client, content="본문만")

    assert result["success"] is True
    assert [c.args[0] for c in client.patch.await_args_list] == [f"{API}/pages/{ROW}/markdown"]


@pytest.mark.asyncio
@pytest.mark.parametrize("properties, content", [
    (None, None), ({}, ""), ({"점수": "", "태그": [], "마감": {"start": ""}}, "  "),
])
async def test_수정할_내용이_없으면_실패한다(properties, content):
    client = _client(get=[_ok(PAGE), _ok(SCHEMA)])

    result = await _update(client, properties=properties, content=content)

    assert "바꿀 내용이 없어요" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("parent", [{"type": "page_id", "page_id": "x"}, {"type": "workspace", "workspace": True}, None])
async def test_DB_행이_아니면_실패한다(parent):
    client = _client(get=[_ok({**PAGE, "parent": parent})])

    result = await _update(client, content="x")

    assert "DB 행이 아니에요" in result["error"] and client.get.await_count == 1


@pytest.mark.asyncio
async def test_속성은_성공하고_본문이_실패하면_둘_다_알린다():
    client = _client(get=[_ok(PAGE), _ok(SCHEMA)],
                     patch_=[_ok(PAGE), _make_response(400, {"message": "contains child pages"})])

    result = await _update(client, properties={"이름": "새 제목"}, content="본문")

    assert result == {"error": "속성은 바뀌었어요. 본문은 실패: Notion API 오류 (400): contains child pages"}


@pytest.mark.asyncio
async def test_속성은_성공하고_본문_호출이_예외면_둘_다_알린다():
    client = _client(get=[_ok(PAGE), _ok(SCHEMA)], patch_=[_ok(PAGE), RuntimeError("timed out")])

    result = await _update(client, properties={"이름": "새 제목"}, content="본문")

    assert result["error"].startswith("속성은 바뀌었어요. 본문은 실패: ") and "timed out" in result["error"]


@pytest.mark.asyncio
async def test_본문만_바꾸다_실패하면_Notion_오류만():
    client = _client(get=[_ok(PAGE), _ok(SCHEMA)], patch_=[_make_response(403, {"message": "no access"})])

    assert await _update(client, content="본문") == {"error": "Notion API 오류 (403): no access"}


# --- 속도 제한 -------------------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after, waited", [("2", 2.0), ("5", 5.0), ("0", 0.0)])
async def test_429는_Retry_After만큼_한_번_쉬고_다시_부른다(retry_after, waited):
    limited = _make_response(429, {"message": "rate_limited"}, {"Retry-After": retry_after})
    client = _client(get=[_ok(SCHEMA)], post=[limited, _ok(CREATED)])
    sleep = AsyncMock()

    with patch("tools.notion_rows.asyncio.sleep", new=sleep):
        result = await _create(client)

    assert result["success"] is True and client.post.await_count == 2
    sleep.assert_awaited_once_with(waited)


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{"Retry-After": "30"}, {"Retry-After": "soon"}, {}])
async def test_Retry_After가_5초를_넘거나_없으면_기다리지_않고_실패한다(headers):
    client = _client(get=[_make_response(429, {"message": "rate_limited"}, headers)])
    sleep = AsyncMock()

    with patch("tools.notion_rows.asyncio.sleep", new=sleep):
        result = await _create(client)

    assert result == {"error": "Notion API 오류 (429): rate_limited"}
    assert client.get.await_count == 1 and sleep.await_count == 0


@pytest.mark.asyncio
async def test_두_번째도_429면_실패한다():
    limited = _make_response(429, {"message": "rate_limited"}, {"Retry-After": "1"})
    client = _client(get=[_ok(PAGE), limited, limited])
    sleep = AsyncMock()

    with patch("tools.notion_rows.asyncio.sleep", new=sleep):
        result = await _update(client, content="x")

    assert result == {"error": "Notion API 오류 (429): rate_limited"}
    assert client.get.await_count == 3 and sleep.await_count == 1


@pytest.mark.asyncio
async def test_5xx는_다시_부르지_않는다():
    """POST /pages 재시도는 행을 두 번 만들 수 있다(Notion 문서)."""
    client = _client(get=[_ok(SCHEMA)], post=[_make_response(502, {"message": "bad gateway"})])
    sleep = AsyncMock()

    with patch("tools.notion_rows.asyncio.sleep", new=sleep):
        result = await _create(client)

    assert result == {"error": "Notion API 오류 (502): bad gateway"}
    assert client.post.await_count == 1 and sleep.await_count == 0

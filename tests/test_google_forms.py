"""Google Forms 도구(lane-forms 계획 Task 4). HTTP는 httpx.MockTransport로 대신한다 — 진짜 httpx가 URL을 만든다."""
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from tests.action_outputs import assert_matches_outputs
from tools.google_forms import (
    _json_size, _normalize_response, google_forms_get_form, google_forms_get_response,
    google_forms_list_responses, google_forms_set_accepting_responses,
)

API = "https://forms.googleapis.com/v1/forms"
FIELDS = ("formId,info,responderUri,linkedSheetId,publishSettings,"
          "items(title,questionItem/question,questionGroupItem/questions)")


def _forms(*responses):
    """요청마다 (상태, 본문)을 차례로 돌려준다. 본문이 str이면 그 문자열 그대로. 받은 요청은 (method, url, json)으로 쌓인다."""
    seen = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), json.loads(request.content) if request.content else None))
        status, body = queue.pop(0)
        return httpx.Response(status, text=body) if isinstance(body, str) else httpx.Response(status, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


def _state(published, accepting):
    return {"publishSettings": {"publishState": {"isPublished": published, "isAcceptingResponses": accepting}}}


async def _set(client, form_id="F1", accepting=True) -> dict:
    with patch("tools.google_forms.get_http_client", return_value=client):
        return json.loads(await google_forms_set_accepting_responses(
            access_token="tok", form_id=form_id, accepting=accepting))


async def _get(client, form_id="F1") -> dict:
    with patch("tools.google_forms.get_http_client", return_value=client):
        return json.loads(await google_forms_get_form(access_token="tok", form_id=form_id))


# --- 응답 받기 켜기·끄기 ------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_끄기는_게시를_유지하고_응답만_막는다():
    client, seen = _forms((200, _state(True, True)), (200, _state(True, False)))

    result = await _set(client, accepting=False)

    assert seen == [
        ("GET", f"{API}/F1?fields=publishSettings", None),
        ("POST", f"{API}/F1:setPublishSettings",
         {"publishSettings": {"publishState": {"isPublished": True, "isAcceptingResponses": False}},
          "updateMask": "publishState"}),
    ]
    assert result["isAcceptingResponses"] is False
    assert result["isPublished"] is True
    assert_matches_outputs("google_forms.set_accepting_responses", result)


@pytest.mark.asyncio
async def test_게시_안_된_폼_켜기는_게시도_함께():
    client, seen = _forms((200, _state(False, False)), (200, _state(True, True)))

    result = await _set(client, accepting=True)

    assert seen[1][2]["publishSettings"]["publishState"] == {"isPublished": True, "isAcceptingResponses": True}
    assert result == {"success": True, "isPublished": True, "isAcceptingResponses": True}


@pytest.mark.asyncio
async def test_이미_같은_상태면_쓰기_호출이_없다():
    client, seen = _forms((200, _state(True, True)))

    result = await _set(client, accepting=True)

    assert [m for m, _, _ in seen] == ["GET"]
    assert result == {"success": True, "isPublished": True, "isAcceptingResponses": True}


@pytest.mark.asyncio
async def test_publishState가_비어도_새_형식_폼이면_켤_수_있다():
    client, seen = _forms((200, {"publishSettings": {}}), (200, _state(True, True)))

    result = await _set(client, accepting=True)

    assert seen[1][2]["publishSettings"]["publishState"] == {"isPublished": True, "isAcceptingResponses": True}
    assert result == {"success": True, "isPublished": True, "isAcceptingResponses": True}


@pytest.mark.asyncio
async def test_예전_폼은_바꾸지_않고_오류():
    client, seen = _forms((200, {}))

    result = await _set(client, accepting=False)

    assert result["error"] == "예전 형식의 폼이라 바꿀 수 없어요. Google Forms에서 직접 바꿔 주세요"
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("accepting", ["false", "true", None, 1])
async def test_accepting이_bool이_아니면_거절(accepting):
    client, seen = _forms()

    result = await _set(client, accepting=accepting)

    assert "error" in result
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("form_id", ["../drive/v3/files", "a/b", "https://docs.google.com/forms/d/x/edit", "", "폼"])
async def test_폼_ID_형식_오류는_호출_없이_거절(form_id):
    client, seen = _forms()

    assert "error" in await _set(client, form_id=form_id)
    assert "error" in await _get(client, form_id=form_id)
    assert seen == []


@pytest.mark.asyncio
async def test_없는_폼은_편집_주소_ID_안내():
    client, _ = _forms((404, {"error": {"message": "Requested entity was not found."}}))

    result = await _set(client)

    assert "폼을 찾을 수 없어요" in result["error"]
    assert "편집 화면 주소" in result["error"]


@pytest.mark.asyncio
async def test_403은_Google_메시지와_Drive_권한_안내():
    client, _ = _forms((403, {"error": {"message": "Google Forms API has not been used"}}))

    result = await _set(client)

    assert "Google Forms API 오류 (403)" in result["error"]
    assert "Google Forms API has not been used" in result["error"]
    assert "Drive 권한" in result["error"]


@pytest.mark.asyncio
async def test_깨진_본문의_실패도_상태_코드를_남긴다():
    client, _ = _forms((502, "<html>"))

    result = await _set(client)

    assert "(502)" in result["error"]


# --- 폼 정보 가져오기 ---------------------------------------------------------------------------

def _q(kind_key, qid="q1", required=None, **extra):
    question = {"questionId": qid, kind_key: extra}
    if required is not None:
        question["required"] = required
    return question


@pytest.mark.asyncio
async def test_폼_정보_가져오기():
    form = {
        "formId": "F1",
        "info": {"title": "고객 설문"},
        "responderUri": "https://docs.google.com/forms/d/e/R1/viewform",
        "linkedSheetId": "S1",
        **_state(True, False),
        "items": [{"itemId": "i1", "title": "이름", "questionItem": {"question": _q("textQuestion", required=True)}}],
    }
    client, seen = _forms((200, form))

    result = await _get(client)

    [(method, url, body)] = seen
    url = httpx.URL(url)
    assert (method, str(url.copy_with(query=None)), body) == ("GET", f"{API}/F1", None)
    assert url.params["fields"] == FIELDS
    assert result["found"] is True and result["total"] == 1
    assert result["message"] == ""
    item = result["items"][0]
    assert item == {
        "formId": "F1", "title": "고객 설문", "description": "",
        "responderUrl": "https://docs.google.com/forms/d/e/R1/viewform",
        "isPublished": True, "isAcceptingResponses": False, "linkedSheetId": "S1",
        "questions": [{"questionId": "q1", "title": "이름", "kind": "text", "required": True}],
    }
    assert_matches_outputs("google_forms.get_form", result)


@pytest.mark.asyncio
async def test_get_form_성공은_items_한_개():
    form = {"formId": "F1", "info": {"title": "t", "description": "d"}, **_state(True, True),
            "items": [{"title": "색", "questionItem": {"question": _q("choiceQuestion", qid="c1")}}]}
    client, _ = _forms((200, form))

    result = await _get(client)

    assert result["found"] is True and result["total"] == 1 and len(result["items"]) == 1
    assert result["items"][0]["description"] == "d"
    assert result["items"][0]["questions"] == [
        {"questionId": "c1", "title": "색", "kind": "choice", "required": False}]
    assert_matches_outputs("google_forms.get_form", result)


@pytest.mark.asyncio
async def test_get_form_없는_폼은_found_false():
    client, _ = _forms((404, {"error": {"message": "Requested entity was not found."}}))

    result = await _get(client)

    assert "error" not in result
    assert result["found"] is False and result["total"] == 0 and result["items"] == []
    assert "편집 화면 주소" in result["message"]
    assert_matches_outputs("google_forms.get_form", result)


@pytest.mark.asyncio
async def test_get_form_403은_오류():
    client, _ = _forms((403, {"error": {"message": "denied"}}))

    result = await _get(client)

    assert "Drive 권한" in result["error"]


@pytest.mark.asyncio
async def test_질문_목록_종류_매핑():
    kinds = ["choiceQuestion", "scaleQuestion", "dateQuestion", "timeQuestion", "fileUploadQuestion", "ratingQuestion"]
    items = [{"title": k, "questionItem": {"question": _q(k, qid=k)}} for k in kinds]
    items.append({"title": "만족도", "questionGroupItem": {
        "questions": [{"questionId": "r1", "rowQuestion": {"title": "1주"}},
                      {"questionId": "r2", "rowQuestion": {"title": "2주"}}],
        "grid": {"columns": {"type": "RADIO"}}}})
    items += [{"title": "p", "pageBreakItem": {}}, {"title": "t", "textItem": {}},
              {"title": "i", "imageItem": {}}, {"title": "v", "videoItem": {}}]
    items.append({"title": "새 종류", "questionItem": {"question": _q("fooQuestion", qid="foo")}})
    client, _ = _forms((200, {"formId": "F1", "info": {"title": "t"}, "items": items}))

    questions = (await _get(client))["items"][0]["questions"]

    assert [q["kind"] for q in questions] == [
        "choice", "scale", "date", "time", "file_upload", "rating", "row", "row", "other"]
    assert [q["title"] for q in questions[6:8]] == ["만족도 - 1주", "만족도 - 2주"]
    assert [q["questionId"] for q in questions[6:8]] == ["r1", "r2"]
    assert all(q["required"] is False for q in questions)
    # assert_matches_outputs는 1단 children만 대조한다 — 2단 중첩(questions) 키는 여기서 직접 본다
    assert all(set(q) == {"questionId", "title", "kind", "required"} for q in questions)


@pytest.mark.asyncio
async def test_예전_폼_정보는_게시_상태가_null():
    client, _ = _forms((200, {"formId": "F1", "info": {"title": "t"}}))

    result = await _get(client)

    item = result["items"][0]
    assert item["isPublished"] is None and item["isAcceptingResponses"] is None
    assert item["linkedSheetId"] == ""
    assert item["questions"] == []
    assert_matches_outputs("google_forms.get_form", result)


@pytest.mark.asyncio
async def test_질문이_500개를_넘으면_앞_500개만_담고_안내():
    items = [{"title": f"q{i}", "questionItem": {"question": _q("textQuestion", qid=f"q{i}")}} for i in range(501)]
    client, _ = _forms((200, {"formId": "F1", "info": {"title": "t"}, "items": items}))

    result = await _get(client)

    questions = result["items"][0]["questions"]
    assert len(questions) == 500 and questions[-1]["questionId"] == "q499"
    assert "앞 500개" in result["message"]
    assert_matches_outputs("google_forms.get_form", result)


@pytest.mark.asyncio
async def test_긴_제목과_설명은_자른다():
    items = [{"title": "가" * 1500, "questionItem": {"question": _q("textQuestion")}}]
    client, _ = _forms((200, {"formId": "F1", "info": {"title": "제" * 1500, "description": "설" * 20000},
                              "items": items}))

    item = (await _get(client))["items"][0]

    assert len(item["title"]) == 1000 and len(item["description"]) == 10000
    assert len(item["questions"][0]["title"]) == 1000


@pytest.mark.asyncio
async def test_응답자용_제목이_비면_파일_이름을_제목으로():
    # dev e2e(2026-11-10): info.title이 빈 폼이 있다 — Drive 파일 이름(documentTitle)으로 채운다.
    client, _ = _forms((200, {"formId": "F1", "info": {"title": "", "documentTitle": "현직자 업무 자동화 인터뷰"},
                              "items": []}))
    result = await _get(client)
    assert result["items"][0]["title"] == "현직자 업무 자동화 인터뷰"


# --- 응답 목록·응답 하나 가져오기 (gmail-forms-9 P4 Task 1) ------------------------------------

QFIELDS = "items(title,questionItem/question,questionGroupItem(questions,grid/columns/type))"
FORM = {"items": [
    {"title": "이름", "questionItem": {"question": {"questionId": "q1", "required": True, "textQuestion": {}}}},
    {"title": "관심사", "questionItem": {"question": {"questionId": "q2", "choiceQuestion": {"type": "CHECKBOX"}}}},
    {"title": "만족도", "questionItem": {"question": {"questionId": "q3", "scaleQuestion": {"low": 1, "high": 5}}}},
    {"title": "이력서", "questionItem": {"question": {"questionId": "q4", "fileUploadQuestion": {}}}},
    {"title": "요일", "questionGroupItem": {"grid": {"columns": {"type": "CHECKBOX"}},
                                           "questions": [{"questionId": "r1", "rowQuestion": {"title": "1주"}}]}},
    {"title": "성별", "questionItem": {"question": {"questionId": "q5", "choiceQuestion": {"type": "RADIO"}}}},
]}


def _text(*values):
    return {"textAnswers": {"answers": [{"value": v} for v in values]}}


FILE = {"fileId": "D1", "fileName": "cv.pdf", "mimeType": "application/pdf"}
RESP = {"responseId": "R1", "createTime": "2026-11-10T01:00:00Z", "lastSubmittedTime": "2026-11-10T02:00:00.5Z",
        "respondentEmail": "a@x.com",
        "answers": {"q2": _text("독서"), "q1": _text("홍길동"), "q3": _text("4"),
                    "q4": {"fileUploadAnswers": {"answers": [FILE]}}, "r1": _text("월", "수"),
                    "q-x": _text("옛 질문")}}
NOW = datetime(2026, 11, 10, 3, 0, tzinfo=timezone.utc)


def _resp(rid, submitted, **answers):
    return {"responseId": rid, "createTime": submitted, "lastSubmittedTime": submitted,
            "answers": {k: _text(v) for k, v in (answers or {"q1": "x"}).items()}}


async def _list(client, **kw) -> dict:
    with patch("tools.google_forms.get_http_client", return_value=client), \
            patch("tools.google_forms._now", return_value=NOW):
        return json.loads(await google_forms_list_responses(access_token="tok", form_id="F1", **kw))


async def _one(client, **kw) -> dict:
    with patch("tools.google_forms.get_http_client", return_value=client):
        return json.loads(await google_forms_get_response(access_token="tok", form_id="F1", **kw))


@pytest.mark.asyncio
async def test_답_값_정규화():
    client, seen = _forms((200, RESP), (200, FORM))

    r = await _one(client, response_id="R1")

    assert seen[0] == ("GET", f"{API}/F1/responses/R1", None)
    assert seen[1][0] == "GET" and httpx.URL(seen[1][1]).path == "/v1/forms/F1"
    assert httpx.URL(seen[1][1]).params["fields"] == QFIELDS
    assert len(seen) == 2
    assert r["found"] is True and r["total"] == 1
    item = r["items"][0]
    assert item["answers"] == {"q1": "홍길동", "q2": ["독서"], "q3": 4, "q4": [FILE], "r1": ["월", "수"]}
    assert [a["questionId"] for a in item["answerList"]] == ["q1", "q2", "q3", "q4", "r1", "q-x"]
    assert item["answerList"][4]["question"] == "요일 - 1주"
    assert item["answerList"][5] == {"questionId": "q-x", "question": "", "answer": "옛 질문"}
    assert (item["responseId"], item["submittedAt"], item["createdAt"], item["respondentEmail"],
            item["totalScore"]) == ("R1", "2026-11-10T02:00:00.5Z", "2026-11-10T01:00:00Z", "a@x.com", None)
    assert_matches_outputs("google_forms.get_response", r)


def test_BE와_같은_정규화_고정_예시():
    # 고정 예시 1 — BE api/src/test/resources/forms/normalize_case1.json과 바이트 동일
    c = json.loads((Path(__file__).parent / "fixtures" / "forms_normalize_case1.json").read_text(encoding="utf-8"))
    out = _normalize_response(c["response"], c["form"])
    assert out == c["expected"]
    assert list(out) == ["responseId", "submittedAt", "createdAt", "respondentEmail", "answers", "answerList",
                         "totalScore"]

    # 고정 예시 2 — 이메일 없음, 퀴즈 점수, 10,000자 자르기, 숫자 아닌 배율은 글 그대로
    r2 = {"responseId": "R2", "createTime": "2026-11-10T02:00:00Z", "lastSubmittedTime": "2026-11-10T03:00:00Z",
          "totalScore": 7,
          "answers": {"1a2b3c4d": _text("가" * 10_001), "3c4d5e6f": _text("N/A"), "708192a3": _text("남")}}
    out = _normalize_response(r2, c["form"])
    assert out["respondentEmail"] == "" and out["totalScore"] == 7
    assert out["answers"] == {"1a2b3c4d": "가" * 10_000, "3c4d5e6f": "N/A", "708192a3": "남"}
    assert [a["questionId"] for a in out["answerList"]] == ["1a2b3c4d", "3c4d5e6f", "708192a3"]

    # 지운 질문은 응답 순서가 아니라 questionId 오름차순으로 맨 뒤
    r3 = {**r2, "answers": {**r2["answers"], "zz9": _text("z"), "aa1": _text("a")}}
    out = _normalize_response(r3, c["form"])
    assert [a["questionId"] for a in out["answerList"]][-2:] == ["aa1", "zz9"]


def test_이메일_미수집_퀴즈_점수():
    resp = {k: v for k, v in RESP.items() if k != "respondentEmail"} | {"totalScore": 7}
    out = _normalize_response(resp, FORM)
    assert out["respondentEmail"] == "" and out["totalScore"] == 7


@pytest.mark.parametrize("raw", ["abc", "4.5", " 4", "1234567890123456"])
def test_배율_값이_숫자가_아니면_글_그대로(raw):
    out = _normalize_response({**RESP, "answers": {"q3": _text(raw)}}, FORM)
    assert out["answers"]["q3"] == raw


def test_긴_답은_만자에서_자른다():
    out = _normalize_response({**RESP, "answers": {"q1": _text("가" * 10_001)}}, FORM)
    assert len(out["answers"]["q1"]) == 10_000


def test_응답이_예산을_넘으면_글_상한을_천자로_낮춰_다시_만든다():
    # BE budgetRecutsTo1000과 짝 — 예산만 작게 바꿔 재생성 경로를 탄다
    resp = {**RESP, "answers": {"q1": _text("가" * 5_000), "q2": _text("나" * 3_000, "다")}}
    with patch("tools.google_forms._OUTPUT_MAX", 5_000):
        out = _normalize_response(resp, FORM)
    assert out["answers"]["q1"] == "가" * 1_000
    assert out["answers"]["q2"] == ["나" * 1_000, "다"]
    assert out["answerList"][0]["answer"] == "가" * 1_000


@pytest.mark.asyncio
async def test_없는_응답은_found_false():
    client, seen = _forms((404, {"error": {"message": "Requested entity was not found."}}))

    r = await _one(client, response_id="R9")

    assert r == {"success": True, "found": False, "total": 0, "items": []}
    assert len(seen) == 1
    assert_matches_outputs("google_forms.get_response", r)


@pytest.mark.asyncio
@pytest.mark.parametrize("response_id", ["../x", "a/b", "", "응답"])
async def test_응답_ID_형식_오류는_호출_없이_거절(response_id):
    client, seen = _forms()
    r = await _one(client, response_id=response_id)
    assert "error" in r
    assert seen == []


@pytest.mark.asyncio
async def test_응답_목록은_최근_제출순():
    old, new = _resp("OLD", "2026-11-10T02:00:00Z"), _resp("NEW", "2026-11-10T02:00:00.5Z")
    client, seen = _forms((200, {"responses": [old, new]}), (200, FORM))

    r = await _list(client)

    params = httpx.URL(seen[0][1]).params
    assert httpx.URL(seen[0][1]).path == "/v1/forms/F1/responses"
    assert params["filter"] == "timestamp >= 2026-11-03T03:00:00Z"
    assert params["pageSize"] == "1000"
    assert httpx.URL(seen[1][1]).params["fields"] == QFIELDS
    assert [i["responseId"] for i in r["items"]] == ["NEW", "OLD"]
    assert (r["found"], r["total"], r["truncated"]) == (True, 2, False)
    assert_matches_outputs("google_forms.list_responses", r)


@pytest.mark.asyncio
async def test_오늘은_한국_자정부터():
    client, seen = _forms((200, {}))
    await _list(client, period="today")
    assert httpx.URL(seen[0][1]).params["filter"] == "timestamp >= 2026-11-09T15:00:00Z"


@pytest.mark.asyncio
async def test_전체는_필터가_없다():
    client, seen = _forms((200, {}))
    await _list(client, period="all")
    assert "filter" not in httpx.URL(seen[0][1]).params


@pytest.mark.asyncio
@pytest.mark.parametrize("since", ["2026-11-01T09:00:00", "2026-11-01T09:00:00+09:00", "2026-11-01T00:00:00Z"])
async def test_직접_입력_시작_시각(since):
    client, seen = _forms((200, {}))
    await _list(client, period="custom", since=since)
    assert httpx.URL(seen[0][1]).params["filter"] == "timestamp >= 2026-11-01T00:00:00Z"


@pytest.mark.asyncio
@pytest.mark.parametrize("since", ["", "어제", "0001-01-01T00:00:00"])  # 마지막은 UTC 변환 OverflowError
async def test_직접_입력인데_시작_시각이_없거나_틀리면_거절(since):
    client, seen = _forms()
    r = await _list(client, period="custom", since=since)
    assert "error" in r
    assert seen == []


@pytest.mark.asyncio
async def test_모르는_기간은_거절():
    client, seen = _forms()
    r = await _list(client, period="yesterday")
    assert "error" in r and seen == []

    client, seen = _forms((200, {}))
    await _list(client, period="")
    assert httpx.URL(seen[0][1]).params["filter"] == "timestamp >= 2026-11-03T03:00:00Z"


@pytest.mark.asyncio
async def test_최대_개수가_숫자가_아니면_거절():
    client, seen = _forms()
    r = await _list(client, max_results="abc")
    assert "error" in r and seen == []


@pytest.mark.asyncio
async def test_최대_개수만큼_최신부터():
    rs = [_resp(f"R{h}", f"2026-11-10T0{h}:00:00Z") for h in (1, 2, 3)]
    client, _ = _forms((200, {"responses": rs}), (200, FORM))

    r = await _list(client, max_results=2)

    assert [i["responseId"] for i in r["items"]] == ["R3", "R2"]
    assert r["truncated"] is False


@pytest.mark.asyncio
async def test_0건은_found_false이고_폼_조회가_없다():
    client, seen = _forms((200, {}))
    r = await _list(client)
    assert r == {"success": True, "found": False, "total": 0, "items": [], "truncated": False}
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_페이지를_따라_읽는다():
    client, seen = _forms((200, {"responses": [_resp("Ra", "2026-11-10T01:00:00Z")], "nextPageToken": "p2"}),
                          (200, {"responses": [_resp("Rb", "2026-11-10T02:00:00Z")]}), (200, FORM))
    r = await _list(client)
    assert httpx.URL(seen[1][1]).params["pageToken"] == "p2"
    assert r["total"] == 2


@pytest.mark.asyncio
async def test_목록이_크면_오래된_응답부터_뺀다():
    rs = [_resp(f"R{h}", f"2026-11-10T0{h}:00:00Z") for h in (1, 2, 3)]
    client, _ = _forms((200, {"responses": rs}), (200, FORM))
    r1 = await _list(client)
    budget = sum(_json_size(i) for i in r1["items"][:2])

    client, _ = _forms((200, {"responses": rs}), (200, FORM))
    with patch("tools.google_forms._OUTPUT_MAX", budget):
        r = await _list(client)

    assert [i["responseId"] for i in r["items"]] == [i["responseId"] for i in r1["items"][:2]]
    assert r["truncated"] is True and r["total"] == 2


@pytest.mark.asyncio
async def test_목록_없는_폼은_오류():
    client, _ = _forms((404, {"error": {"message": "Requested entity was not found."}}))
    r = await _list(client)
    assert "폼을 찾을 수 없어요" in r["error"]

"""Google Forms 도구(lane-forms 계획 Task 4). HTTP는 httpx.MockTransport로 대신한다 — 진짜 httpx가 URL을 만든다."""
import json
from unittest.mock import patch

import httpx
import pytest

from tests.action_outputs import assert_matches_outputs
from tools.google_forms import google_forms_get_form, google_forms_set_accepting_responses

API = "https://forms.googleapis.com/v1/forms"


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

    assert seen == [("GET", f"{API}/F1", None)]
    assert result["found"] is True and result["total"] == 1
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

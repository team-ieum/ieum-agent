import json
import re

from tools.http_client import get_http_client

_FORMS_API = "https://forms.googleapis.com/v1/forms"
_TIMEOUT = 30.0
_FORM_ID = re.compile(r"[A-Za-z0-9_-]+", re.ASCII)
_SCOPE_HINT = "Google 연결에서 Drive 권한을 켜 주세요"
_NOT_FOUND = "폼을 찾을 수 없어요. 편집 화면 주소(/forms/d/<ID>/edit)의 ID인지, 편집 권한이 있는 폼인지 확인해 주세요"
_LEGACY = "예전 형식의 폼이라 바꿀 수 없어요. Google Forms에서 직접 바꿔 주세요"

# Forms API 질문 종류 키 → 출력 kind. 표에 없는 종류는 "other"로 남긴다(조용히 빼지 않는다)
_KINDS = {
    "textQuestion": "text",
    "choiceQuestion": "choice",
    "scaleQuestion": "scale",
    "dateQuestion": "date",
    "timeQuestion": "time",
    "fileUploadQuestion": "file_upload",
    "ratingQuestion": "rating",
}


def _headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def _error(text: str) -> str:
    return json.dumps({"error": text}, ensure_ascii=False)


def _api_error(response) -> str:
    # 본문은 실패일 때만 읽는다. 깨진 본문이어도 상태 코드는 남긴다.
    if response.status_code == 404:
        return _error(_NOT_FOUND)
    try:
        message = response.json().get("error", {}).get("message", "알 수 없는 오류")
    except Exception:
        message = "알 수 없는 오류"
    text = f"Google Forms API 오류 ({response.status_code}): {message}"
    if response.status_code == 403:
        text += f" — {_SCOPE_HINT}"
    return _error(text)


def _form_id(form_id):
    """경로에 그대로 들어가므로 ID 문자만 받는다(../ 등 경로 조작 차단). URL 정규화는 하지 않는다."""
    value = str(form_id or "").strip()
    return value if _FORM_ID.fullmatch(value) else None


def _bad_form_id() -> str:
    return _error("폼 ID 형식이 아니에요. 편집 화면 주소(/forms/d/<ID>/edit)의 ID를 넣어 주세요")


def _publish_state(form: dict):
    """(isPublished, isAcceptingResponses). 예전 형식 폼(publishSettings 없음)이면 None."""
    if "publishSettings" not in form:
        return None
    state = (form["publishSettings"] or {}).get("publishState") or {}
    return bool(state.get("isPublished", False)), bool(state.get("isAcceptingResponses", False))


def _questions(items) -> list[dict]:
    out = []
    for item in items or []:
        title = item.get("title", "")
        if "questionItem" in item:
            q = item["questionItem"].get("question", {})
            kind = next((v for k, v in _KINDS.items() if k in q), "other")
            out.append({"questionId": q.get("questionId", ""), "title": title,
                        "kind": kind, "required": bool(q.get("required", False))})
        elif "questionGroupItem" in item:
            for q in item["questionGroupItem"].get("questions", []):
                row = (q.get("rowQuestion") or {}).get("title", "")
                out.append({"questionId": q.get("questionId", ""), "title": f"{title} - {row}",
                            "kind": "row", "required": bool(q.get("required", False))})
        # pageBreak·text·image·video 항목은 질문이 아니라 건너뛴다
    return out


async def google_forms_set_accepting_responses(access_token: str, form_id: str, accepting: bool) -> str:
    """구글 폼이 응답을 받을지 켜거나 끈다. 켜면 게시 안 된 폼은 게시도 함께, 끄면 게시 상태는 그대로 둔다.

    Args:
        form_id: 대상 폼 ID(편집 화면 주소 /forms/d/<ID>/edit의 ID)
        accepting: 응답 받기(true 켜기, false 끄기)

    Returns:
        성공 {"success": true, "isPublished", "isAcceptingResponses"}, 실패 {"error"}
    """
    fid = _form_id(form_id)
    if fid is None:
        return _bad_form_id()
    # 문자열 "false"가 참으로 읽혀 응답이 켜지면 안 된다 — bool만 받는다
    if not isinstance(accepting, bool):
        return _error("응답 받기는 true 또는 false여야 해요")

    try:
        client = get_http_client()
        headers = _headers(access_token)
        response = await client.get(f"{_FORMS_API}/{fid}", params={"fields": "publishSettings"},
                                    headers=headers, timeout=_TIMEOUT)
        if not response.is_success:
            return _api_error(response)
        current = _publish_state(response.json())
        if current is None:
            return _error(_LEGACY)

        published = True if accepting else current[0]
        if current == (published, accepting):
            return json.dumps({"success": True, "isPublished": published, "isAcceptingResponses": accepting},
                              ensure_ascii=False)

        body = {"publishSettings": {"publishState": {"isPublished": published, "isAcceptingResponses": accepting}},
                "updateMask": "publishState"}
        response = await client.post(f"{_FORMS_API}/{fid}:setPublishSettings", headers=headers,
                                     json=body, timeout=_TIMEOUT)
        if not response.is_success:
            return _api_error(response)
    except Exception as e:
        return _error(f"Google Forms 요청 실패: {e}")

    return json.dumps({"success": True, "isPublished": published, "isAcceptingResponses": accepting},
                      ensure_ascii=False)


async def google_forms_get_form(access_token: str, form_id: str) -> str:
    """구글 폼의 제목·응답 링크·게시 상태·질문 목록을 가져온다. 없는 폼은 실패가 아니라 found=false.

    Args:
        form_id: 대상 폼 ID(편집 화면 주소 /forms/d/<ID>/edit의 ID)

    Returns:
        성공 {"success": true, "found", "total", "items": [폼], "message"}, 실패 {"error"}
    """
    fid = _form_id(form_id)
    if fid is None:
        return _bad_form_id()

    try:
        response = await get_http_client().get(f"{_FORMS_API}/{fid}", headers=_headers(access_token),
                                               timeout=_TIMEOUT)
        if response.status_code == 404:
            return json.dumps({"success": True, "found": False, "total": 0, "items": [], "message": _NOT_FOUND},
                              ensure_ascii=False)
        if not response.is_success:
            return _api_error(response)
        form = response.json()
    except Exception as e:
        return _error(f"Google Forms 요청 실패: {e}")

    info = form.get("info") or {}
    state = _publish_state(form) or (None, None)
    item = {
        "formId": form.get("formId", fid),
        "title": info.get("title", ""),
        "description": info.get("description", ""),
        "responderUrl": form.get("responderUri", ""),
        "isPublished": state[0],
        "isAcceptingResponses": state[1],
        "linkedSheetId": form.get("linkedSheetId", ""),
        "questions": _questions(form.get("items")),
    }
    return json.dumps({"success": True, "found": True, "total": 1, "items": [item], "message": ""},
                      ensure_ascii=False)

import json
import re
from datetime import datetime, timedelta, timezone

from tools.http_client import get_http_client

_FORMS_API = "https://forms.googleapis.com/v1/forms"
_TIMEOUT = 30.0
_FORM_ID = re.compile(r"[A-Za-z0-9_-]+", re.ASCII)
_SCOPE_HINT = "Google 연결에서 Drive 권한을 켜 주세요"
_NOT_FOUND = "폼을 찾을 수 없어요. 편집 화면 주소(/forms/d/<ID>/edit)의 ID인지, 편집 권한이 있는 폼인지 확인해 주세요"
_LEGACY = "예전 형식의 폼이라 바꿀 수 없어요. Google Forms에서 직접 바꿔 주세요"
# S3 출력 상한 — 큰 폼이 다음 노드·로그로 수 MB를 흘리지 않게(최종 리뷰 M1). 받는 칸도 필요한 것만
_GET_FIELDS = ("formId,info,responderUri,linkedSheetId,publishSettings,"
               "items(title,questionItem/question,questionGroupItem/questions)")
_MAX_QUESTIONS = 500
_MAX_TITLE = 1000
_MAX_DESCRIPTION = 10000
_TRUNCATED = f"질문이 많아 앞 {_MAX_QUESTIONS}개만 담았어요"

# S1·S2 응답 가져오기 — 답 값 정규화는 BE FormsResponseNormalizer와 같은 결과여야 한다(gmail-forms-9 spec 4절)
_QUESTION_FIELDS = "items(title,questionItem/question,questionGroupItem(questions,grid/columns/type))"
_MAX_ANSWER = 10_000
_MAX_ANSWER_TIGHT = 1_000
_OUTPUT_MAX = 2_000_000  # tools/gmail.py _OUTPUT_MAX와 같은 값(BE 4MiB 버퍼 아래)
_PAGE_SIZE = 1000
_MAX_PAGES = 5  # ponytail: 5,000건까지만 읽는다 — 넘으면 truncated, 더 큰 폼은 BE 커서 방식이 필요할 때
_LIST_DEFAULT = 100
_LIST_MAX = 500
_KST = timezone(timedelta(hours=9))
_ANSWER_KEY = re.compile(r"[A-Za-z0-9_]+", re.ASCII)  # 참조식 글자셋 — 밖이면 answerList에만
_NUMBER = re.compile(r"-?[0-9]{1,15}", re.ASCII)
_PERIODS = {"today", "last_7_days", "last_30_days", "all", "custom"}
_PERIOD_DAYS = {"last_7_days": 7, "last_30_days": 30}
_BAD_SINCE = "시작 시각을 ISO 8601로 넣어 주세요 (예: 2026-11-01T09:00:00+09:00)"

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
            out.append({"questionId": q.get("questionId", ""), "title": title[:_MAX_TITLE],
                        "kind": kind, "required": bool(q.get("required", False))})
        elif "questionGroupItem" in item:
            for q in item["questionGroupItem"].get("questions", []):
                row = (q.get("rowQuestion") or {}).get("title", "")
                out.append({"questionId": q.get("questionId", ""), "title": f"{title} - {row}"[:_MAX_TITLE],
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
        response = await get_http_client().get(f"{_FORMS_API}/{fid}", params={"fields": _GET_FIELDS},
                                               headers=_headers(access_token), timeout=_TIMEOUT)
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
    questions = _questions(form.get("items"))
    item = {
        "formId": form.get("formId", fid),
        "title": (info.get("title") or info.get("documentTitle") or "")[:_MAX_TITLE],  # 응답자용 제목이 빈 폼이 있다
        "description": info.get("description", "")[:_MAX_DESCRIPTION],
        "responderUrl": form.get("responderUri", ""),
        "isPublished": state[0],
        "isAcceptingResponses": state[1],
        "linkedSheetId": form.get("linkedSheetId", ""),
        "questions": questions[:_MAX_QUESTIONS],
    }
    message = _TRUNCATED if len(questions) > _MAX_QUESTIONS else ""
    return json.dumps({"success": True, "found": True, "total": 1, "items": [item], "message": message},
                      ensure_ascii=False)


# --- 응답 가져오기 (S1·S2) ---------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_size(value) -> int:
    # BE 정규화 예산과 같은 식(공백 없는 JSON UTF-8 바이트)
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _ts_key(ts) -> tuple[str, str]:
    """Google 시각(…Z, 소수 초 자릿수 제각각)을 문자열 비교로 정렬하는 키."""
    head, _, frac = str(ts or "").rstrip("Z").partition(".")
    return head, frac.ljust(9, "0")


def _answer_kinds(items) -> dict[str, str]:
    """질문 ID → 답 종류(text·list·number·files). 모르는 종류는 text."""
    out = {}
    for item in items or []:
        if "questionItem" in item:
            q = item["questionItem"].get("question", {})
            if (q.get("choiceQuestion") or {}).get("type") == "CHECKBOX":
                kind = "list"
            elif "scaleQuestion" in q or "ratingQuestion" in q:
                kind = "number"
            elif "fileUploadQuestion" in q:
                kind = "files"
            else:
                kind = "text"
            out[q.get("questionId", "")] = kind
        elif "questionGroupItem" in item:
            group = item["questionGroupItem"]
            columns = (group.get("grid") or {}).get("columns") or {}
            kind = "list" if columns.get("type") == "CHECKBOX" else "text"
            for q in group.get("questions", []):
                out[q.get("questionId", "")] = kind
    return out


def _answer_value(answer: dict, kind, cap: int):
    """kind None = 폼에 없는(지운) 질문 — 답 모양으로 정한다."""
    if kind == "files" or "fileUploadAnswers" in answer:
        files = (answer.get("fileUploadAnswers") or {}).get("answers") or []
        return [{k: str(f.get(k, ""))[:cap] for k in ("fileId", "fileName", "mimeType")} for f in files]
    values = [str(a.get("value", ""))[:cap] for a in (answer.get("textAnswers") or {}).get("answers") or []]
    if kind is None:
        kind = "list" if len(values) > 1 else "text"
    if kind == "list":
        return values
    first = values[0] if values else ""
    if kind == "number" and _NUMBER.fullmatch(first):
        return int(first)
    return first


def _build_response(response: dict, form: dict, cap: int) -> dict:
    items = form.get("items")
    kinds = _answer_kinds(items)
    raw = response.get("answers") or {}
    order = [(q["questionId"], q["title"]) for q in _questions(items) if q["questionId"] in raw]
    known = {qid for qid, _ in order}
    order += [(qid, "") for qid in sorted(raw) if qid not in known]  # 지운 질문은 ID 오름차순으로 맨 뒤

    answers, answer_list = {}, []
    for qid, title in order:
        value = _answer_value(raw[qid] or {}, kinds.get(qid), cap)
        if _ANSWER_KEY.fullmatch(qid):
            answers[qid] = value
        answer_list.append({"questionId": qid, "question": title, "answer": value})
    return {
        "responseId": response.get("responseId", ""),
        "submittedAt": response.get("lastSubmittedTime", ""),
        "createdAt": response.get("createTime", ""),
        "respondentEmail": response.get("respondentEmail", ""),
        "answers": answers,
        "answerList": answer_list,
        "totalScore": response.get("totalScore"),
    }


def _normalize_response(response: dict, form: dict) -> dict:
    """응답 1건 → 응답 칸 7키. 2MB를 넘으면 글 상한을 1,000자로 낮춰 한 번만 다시 만든다(BE와 같음)."""
    out = _build_response(response, form, _MAX_ANSWER)
    if _json_size(out) > _OUTPUT_MAX:
        out = _build_response(response, form, _MAX_ANSWER_TIGHT)
    return out


def _since(period, since):
    """(시작 시각 또는 None, 오류 문구 또는 None). 오늘·최근 N일은 한국 시간 기준."""
    period = str(period or "").strip() or "last_7_days"
    if period not in _PERIODS:
        return None, "기간은 today·last_7_days·last_30_days·all·custom 중 하나여야 해요"
    if period == "all":
        return None, None
    if period == "today":
        today = _now().astimezone(_KST).replace(hour=0, minute=0, second=0, microsecond=0)
        return today, None
    if period in _PERIOD_DAYS:
        return _now() - timedelta(days=_PERIOD_DAYS[period]), None
    try:
        start = datetime.fromisoformat(str(since or "").strip())
        # 0001-01-01처럼 UTC로 옮기면 범위를 벗어나는 값은 OverflowError — 밖으로 새지 않게 여기서 변환까지
        return (start if start.tzinfo else start.replace(tzinfo=_KST)).astimezone(timezone.utc), None
    except (ValueError, OverflowError):
        return None, _BAD_SINCE


async def _get_form_questions(client, fid: str, headers: dict):
    """(폼, 오류 JSON 또는 None)."""
    response = await client.get(f"{_FORMS_API}/{fid}", params={"fields": _QUESTION_FIELDS},
                                headers=headers, timeout=_TIMEOUT)
    if not response.is_success:
        return None, _api_error(response)
    return response.json(), None


async def google_forms_list_responses(access_token: str, form_id: str, period: str = "last_7_days",
                                      since: str = "", max_results: int = _LIST_DEFAULT) -> str:
    """구글 폼 응답을 기간으로 골라 최근 제출 순으로 가져온다. 답은 질문 ID 키로 정리한다.

    Args:
        form_id: 대상 폼 ID(편집 화면 주소 /forms/d/<ID>/edit의 ID)
        period: 기간(today·last_7_days·last_30_days·all·custom, 오늘·최근 며칠은 한국 시간 기준)
        since: 기간이 custom일 때 이 시각 이후 제출된 응답(ISO 8601, 시간대 없으면 한국 시간)
        max_results: 최대 개수(기본 100, 최대 500)

    Returns:
        성공 {"success": true, "found", "total", "items": [응답], "truncated"}(0건도 성공), 실패 {"error"}
    """
    fid = _form_id(form_id)
    if fid is None:
        return _bad_form_id()
    start, problem = _since(period, since)
    if problem:
        return _error(problem)
    if max_results is None or str(max_results).strip() == "":  # BE는 없는 참조를 ""로 보낸다
        limit = _LIST_DEFAULT
    else:
        try:
            limit = min(max(int(max_results), 1), _LIST_MAX)
        except (TypeError, ValueError):
            return _error("최대 개수는 숫자여야 해요")

    params = {"pageSize": _PAGE_SIZE}
    if start is not None:
        params["filter"] = f"timestamp >= {start.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    try:
        client = get_http_client()
        headers = _headers(access_token)
        raw, truncated = [], False
        for _ in range(_MAX_PAGES):
            response = await client.get(f"{_FORMS_API}/{fid}/responses", params=params,
                                        headers=headers, timeout=_TIMEOUT)
            if not response.is_success:
                return _api_error(response)
            body = response.json()
            raw += body.get("responses") or []
            if not body.get("nextPageToken"):
                break
            params["pageToken"] = body["nextPageToken"]
        else:
            truncated = True  # 상한 쪽수까지 읽고도 남았다

        if not raw:
            return json.dumps({"success": True, "found": False, "total": 0, "items": [], "truncated": truncated},
                              ensure_ascii=False)
        form, problem = await _get_form_questions(client, fid, headers)
        if problem:
            return problem
    except Exception as e:
        return _error(f"Google Forms 요청 실패: {e}")

    # 정렬은 원본 제출 시각(= submittedAt)으로 먼저 — 남길 앞쪽만 정규화한다
    newest = sorted(raw, key=lambda r: _ts_key(r.get("lastSubmittedTime")), reverse=True)[:limit]
    items = [_normalize_response(r, form) for r in newest]
    kept, used = [], 0
    for item in items:
        used += _json_size(item)
        if used > _OUTPUT_MAX:  # 최신부터 담고 넘치면 오래된 것을 뺀다
            truncated = True
            break
        kept.append(item)
    return json.dumps({"success": True, "found": bool(kept), "total": len(kept), "items": kept,
                       "truncated": truncated}, ensure_ascii=False)


async def google_forms_get_response(access_token: str, form_id: str, response_id: str) -> str:
    """응답 ID로 구글 폼 응답 하나를 가져온다. 답은 질문 ID 키로 정리한다. 없는 응답은 실패가 아니라 found=false.

    Args:
        form_id: 대상 폼 ID(편집 화면 주소 /forms/d/<ID>/edit의 ID)
        response_id: 가져올 응답 ID

    Returns:
        성공 {"success": true, "found", "total", "items": [응답]}, 실패 {"error"}
    """
    fid = _form_id(form_id)
    if fid is None:
        return _bad_form_id()
    rid = _form_id(response_id)  # 경로에 들어가므로 같은 ID 문자 검사
    if rid is None:
        return _error("응답 ID 형식이 아니에요")

    try:
        client = get_http_client()
        headers = _headers(access_token)
        response = await client.get(f"{_FORMS_API}/{fid}/responses/{rid}", headers=headers, timeout=_TIMEOUT)
        if response.status_code == 404:
            return json.dumps({"success": True, "found": False, "total": 0, "items": []}, ensure_ascii=False)
        if not response.is_success:
            return _api_error(response)
        raw = response.json()
        form, problem = await _get_form_questions(client, fid, headers)
        if problem:
            return problem
    except Exception as e:
        return _error(f"Google Forms 요청 실패: {e}")

    return json.dumps({"success": True, "found": True, "total": 1, "items": [_normalize_response(raw, form)]},
                      ensure_ascii=False)

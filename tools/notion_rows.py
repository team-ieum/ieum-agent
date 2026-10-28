"""Notion DB 행 도구(spec 2026-10-26 §7). `properties`는 {속성 키: 단순값} — 키는 Notion 속성 ID(`.`만 `%2E`) 또는
속성 이름, 값은 사람이 쓰는 모양이다. 실행마다 DB 스키마를 읽어 Notion 형식으로 바꾼다(D6, 캐시 없음).
참조식으로 채운 값은 BE renderDeep이 치환해 문자열로 온다 — 변환은 문자열과 원래 타입을 둘 다 받는다."""
import asyncio
import json
import math
import re
from typing import Optional

from common.error_code import ToolErrorCode
from tools.http_client import get_http_client
from tools.notion import _NOTION_API_BASE, _TIMEOUT, _headers, _text_segments

_TIME_ZONE = "Asia/Seoul"  # 오프셋 없는 시각 — Notion은 UTC로 읽는다(D15)
_MAX_RETRY_WAIT = 5.0      # 429 Retry-After 상한. 넘으면 기다리지 않고 실패
_MAX_ITEMS = 100           # multi_select·people·relation 한 번에 보낼 수 있는 개수(Notion 요청 한도)
_MAX_LENGTH = {"url": 2000, "email": 200, "phone_number": 200}
_WRITABLE = {"title", "rich_text", "number", "checkbox", "select", "status", "multi_select",
             "people", "relation", "date", "url", "email", "phone_number"}
# 경로에 끼우는 Notion ID — BE 공급원과 같은 규칙. fullmatch + ASCII(`$`는 끝 개행을 통과시킨다)
_ID_RE = re.compile(r"[0-9a-fA-F-]{32,36}", re.ASCII)
_OFFSET_RE = re.compile(r"(Z|[+-]\d{2}:?\d{2})$", re.IGNORECASE)


class _Invalid(ValueError):
    """사용자에게 그대로 보일 입력 오류."""


def _result(**body) -> str:
    return json.dumps(body, ensure_ascii=False)


def _blank(value) -> bool:
    """빈 값(spec §6): None·공백 문자열·빈 항목만 든 목록([] 포함)·start가 빈 date dict({} 포함). BE는 못 푼 참조를 ""로 보낸다.
    dict는 date 모양(start·end만)일 때만 빈 값이다 — {"name": …}·안쪽 _names는 키 해석(D7)으로 넘긴다."""
    if value is None:
        return True
    if isinstance(value, list):
        return all(_blank(v) for v in value)
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, dict):
        return set(value) <= {"start", "end"} and _blank(value.get("start"))
    return False


def _clip(value) -> str:
    """오류에 싣는 사용자 값·키 — 80자를 넘으면 자르고 '…'(static_validator _clip과 같은 규칙)."""
    s = str(value)
    return s if len(s) <= 80 else s[:80] + "…"


def _notion_id(value) -> str | None:
    text = "" if value is None else str(value).strip()
    return text if _ID_RE.fullmatch(text) else None


def _key(prop_id: str) -> str:
    """저장 키 정규화(D5) — BE /fields와 같은 규칙. `.`만 바꾼다(나머지는 Notion이 이미 URL 인코딩해 준다)."""
    return prop_id.replace(".", "%2E")


def _items(value) -> list:
    """목록 값. 문자열이면 JSON 배열 → 안 되면 쉼표로 나눈다(Notion 옵션 이름엔 쉼표를 쓸 수 없다)."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            if isinstance(parsed, list):
                return parsed
        return [v.strip() for v in text.split(",") if v.strip()]
    return [value]


def _option(prop: dict, value, *, create: bool) -> dict:
    """옵션 ID면 {id}, 이름이면 {name}. 없으면 select·multi_select는 {name}(Notion이 만든다, D8), status는 실패."""
    text = str(value).strip()
    options = [o for o in (prop.get(prop["type"]) or {}).get("options") or [] if isinstance(o, dict)]
    if any(o.get("id") == text for o in options):
        return {"id": text}
    if create or any(o.get("name") == text for o in options):
        return {"name": text}
    names = ", ".join(str(o.get("name")) for o in options)
    raise _Invalid(f"'{prop['name']}'에 '{_clip(text)}' 상태가 없어요. 고를 수 있는 값: {names}")


def _number(prop: dict, value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            pass
        try:
            number = float(text)
        except ValueError:
            number = math.nan
        if math.isfinite(number):
            return number
    raise _Invalid(f"'{prop['name']}'에는 숫자를 넣어 주세요(지금 '{_clip(value)}')")


def _checkbox(prop: dict, value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise _Invalid(f"'{prop['name']}'에는 true 또는 false를 넣어 주세요(지금 '{_clip(value)}')")


def _date(name: str, value) -> dict | None:
    if isinstance(value, dict) and set(value) - {"start", "end"}:
        raise _Invalid(f"'{name}'에는 {{start, end}} 객체를 넣어 주세요")
    start, end = (value.get("start"), value.get("end")) if isinstance(value, dict) else (value, None)
    if _blank(start):
        return None
    out = {"start": str(start).strip()}
    if not _blank(end):
        out["end"] = str(end).strip()
    # 시각이 있는데 오프셋이 없으면 KST로 읽게 한다. 날짜만이거나 오프셋이 있으면 그대로(time_zone과 오프셋은 같이 못 쓴다)
    if any("T" in v and not _OFFSET_RE.search(v.split("T", 1)[1]) for v in out.values()):
        out["time_zone"] = _TIME_ZONE
    return out


def _convert(prop: dict, value):
    """단순값 → Notion 속성 값. None = 빈 값이라 보내지 않는다(spec §6)."""
    ptype, name = prop["type"], prop["name"]
    if isinstance(value, dict) and ptype != "date":
        raise _Invalid(f"'{name}'에는 객체를 넣을 수 없어요")
    if isinstance(value, list) and ptype not in ("multi_select", "people", "relation"):
        raise _Invalid(f"'{name}'에는 목록을 넣을 수 없어요")  # str(list)가 select 영구 옵션이 되지 않게(D8)
    if ptype in ("title", "rich_text"):
        return _text_segments(str(value))
    if ptype == "number":
        return _number(prop, value)
    if ptype == "checkbox":
        return _checkbox(prop, value)
    if ptype in ("select", "status"):
        return _option(prop, value, create=ptype == "select")
    if ptype == "date":
        return _date(name, value)
    if ptype in _MAX_LENGTH:
        text = str(value).strip()
        if len(text) > _MAX_LENGTH[ptype]:
            raise _Invalid(f"'{name}'은(는) {_MAX_LENGTH[ptype]:,}자까지예요(지금 {len(text):,}자)")
        return text
    items = [v for v in _items(value) if not _blank(v)]  # multi_select·people·relation
    if not items:
        return None
    if any(isinstance(v, (dict, list)) for v in items):
        raise _Invalid(f"'{name}'의 항목에는 객체나 목록을 넣을 수 없어요")
    if len(items) > _MAX_ITEMS:
        raise _Invalid(f"'{name}'에는 {_MAX_ITEMS}개까지 넣을 수 있어요(지금 {len(items)}개)")
    if ptype == "multi_select":
        return [_option(prop, v, create=True) for v in items]
    return [{"id": str(v).strip()} for v in items]


def _build(schema: dict, properties: dict) -> dict:
    """{저장 키: 단순값} → Notion `properties`(키 = 스키마의 원본 속성 ID). 빈 값은 키를 찾기 전에 거른다 —
    비워 둔 칸의 속성이 DB에서 지워져도 잃는 값이 없다(D7은 값이 있을 때만)."""
    props = [p for p in (schema.get("properties") or {}).values()
             if isinstance(p, dict) and isinstance(p.get("id"), str) and isinstance(p.get("name"), str)]
    out, seen = {}, set()
    for key, value in properties.items():
        if _blank(value):
            continue
        prop = next((p for p in props if _key(p["id"]) == key), None) \
            or next((p for p in props if p["name"] == key), None)
        if prop is None:
            names = ", ".join(f"{p['name']}({p.get('type')})" for p in props if p.get("type") in _WRITABLE)
            raise _Invalid(f"'{_clip(key)}' 속성이 이 데이터베이스에 없어요. 쓸 수 있는 속성: {names}")
        if prop["id"] in seen:
            raise _Invalid(f"'{prop['name']}' 속성이 두 번 들어왔어요")
        seen.add(prop["id"])
        if prop.get("type") not in _WRITABLE:
            raise _Invalid(f"'{prop['name']}' 속성은 쓸 수 없어요({prop.get('type')})")
        converted = _convert(prop, value)
        if converted is not None:
            out[prop["id"]] = {prop["type"]: converted}
    return out


def _api_error(response, hint: str = "") -> str:
    try:
        message = response.json().get("message", "알 수 없는 오류")
    except Exception:
        message = "알 수 없는 오류"
    return f"Notion API 오류 ({response.status_code}): {message}{hint}"


def _retry_after(response) -> float | None:
    try:
        seconds = float(response.headers.get("Retry-After"))
    except (TypeError, ValueError):
        return None
    return seconds if 0 <= seconds <= _MAX_RETRY_WAIT else None


async def _call(method: str, path: str, token: str, **kwargs):
    """Notion 한 번. 429만 Retry-After(≤5초)만큼 쉬고 한 번 더 — 5xx는 다시 부르지 않는다(POST /pages 재시도는 행 중복)."""
    send = getattr(get_http_client(), method)
    for attempt in range(2):
        response = await send(f"{_NOTION_API_BASE}{path}", headers=_headers(token), timeout=_TIMEOUT, **kwargs)
        wait = _retry_after(response) if response.status_code == 429 and not attempt else None
        if wait is None:
            return response
        await asyncio.sleep(wait)


async def notion_create_row(
    token: str,
    data_source_id: str,
    properties: Optional[dict] = None,
    template: Optional[str] = None,
    content: Optional[str] = None,
) -> str:
    """
    Notion 데이터베이스에 새 행을 만듭니다.

    Args:
        token: Notion Integration Token
        data_source_id: 행을 만들 데이터베이스(data source) ID
        properties: {속성 이름: 값} 객체. 값 형식은 아래와 같다
            텍스트·URL·이메일·전화번호: 문자열
            숫자: 숫자 / 체크박스: true 또는 false
            선택·상태: 옵션 이름 / 다중 선택: 옵션 이름 배열
            사람·관계: ID 배열
            날짜: "YYYY-MM-DD", 시각은 "YYYY-MM-DDTHH:MM", 기간은 {"start": ..., "end": ...}
        template: 템플릿 ID, 기본 템플릿은 "default". 비우면 템플릿 없이 만든다. content와 같이 쓸 수 없다
        content: 본문(Markdown). template과 같이 쓸 수 없다

    Returns:
        만든 행의 pageId·url을 담은 JSON 문자열
    """
    if properties is not None and not isinstance(properties, dict):
        return _result(error="속성은 객체여야 해요")
    use_template, use_content = not _blank(template), not _blank(content)
    if use_template and use_content:  # D10: children은 템플릿과 같이 못 쓰고, markdown은 문서에 없다
        return _result(error="템플릿과 본문은 같이 쓸 수 없어요. 둘 중 하나만 넣어 주세요")
    source = _notion_id(data_source_id)
    if source is None:
        return _result(error="데이터베이스 ID가 비었거나 Notion ID 형식이 아니에요")
    try:
        response = await _call("get", f"/data_sources/{source}", token)
        if response.status_code != 200:
            return _result(error=_api_error(response))
        body = {"parent": {"type": "data_source_id", "data_source_id": source},
                "properties": _build(response.json(), properties or {})}
        default = use_template and str(template).strip() == "default"
        if use_template:
            body["template"] = {"type": "default"} if default else \
                {"type": "template_id", "template_id": str(template).strip()}
        if use_content:
            body["markdown"] = str(content)
        response = await _call("post", "/pages", token, json=body)
        if response.status_code != 200:
            hint = ". 기본 템플릿을 골랐다면 이 DB에 기본 템플릿이 없어요 — 템플릿을 비우거나 다른 템플릿을 골라 주세요" \
                if default and response.status_code == 400 else ""
            return _result(error=_api_error(response, hint))
        page = response.json()
    except _Invalid as e:
        return _result(error=str(e))
    except Exception as e:
        return _result(error=f"{ToolErrorCode.EXECUTION_FAILED.message} (notion_create_row: {e})")
    return _result(success=True, pageId=page.get("id", ""), url=page.get("url", ""))


async def notion_update_row(
    token: str,
    row_id: str,
    properties: Optional[dict] = None,
    content: Optional[str] = None,
) -> str:
    """
    Notion 데이터베이스 행의 속성이나 본문을 바꿉니다. 넣은 속성만 바뀌고 나머지는 그대로입니다.

    Args:
        token: Notion Integration Token
        row_id: 바꿀 행(페이지) ID
        properties: {속성 이름: 값} 객체. 값 형식은 아래와 같다
            텍스트·URL·이메일·전화번호: 문자열
            숫자: 숫자 / 체크박스: true 또는 false
            선택·상태: 옵션 이름 / 다중 선택: 옵션 이름 배열(목록 전체를 바꾼다)
            사람·관계: ID 배열(목록 전체를 바꾼다)
            날짜: "YYYY-MM-DD", 시각은 "YYYY-MM-DDTHH:MM", 기간은 {"start": ..., "end": ...}
        content: 새 본문(Markdown). 넣으면 본문 전체를 바꾼다

    Returns:
        바꾼 행의 pageId·url을 담은 JSON 문자열
    """
    if properties is not None and not isinstance(properties, dict):
        return _result(error="속성은 객체여야 해요")
    row = _notion_id(row_id)
    if row is None:
        return _result(error="행 ID가 비었거나 Notion ID 형식이 아니에요")
    try:
        response = await _call("get", f"/pages/{row}", token)
        if response.status_code != 200:
            return _result(error=_api_error(response))
        page = response.json()
        parent = page.get("parent") or {}
        source = parent.get("data_source_id") if parent.get("type") == "data_source_id" else None
        if not source:
            return _result(error="DB 행이 아니에요. 데이터베이스의 행을 골라 주세요")
        response = await _call("get", f"/data_sources/{source}", token)
        if response.status_code != 200:
            return _result(error=_api_error(response))
        changes = _build(response.json(), properties or {})
        if not changes and _blank(content):
            return _result(error="바꿀 내용이 없어요. 속성이나 본문을 하나 이상 넣어 주세요")
        url = page.get("url", "")
        if changes:
            response = await _call("patch", f"/pages/{row}", token, json={"properties": changes})
            if response.status_code != 200:
                return _result(error=_api_error(response))
    except _Invalid as e:
        return _result(error=str(e))
    except Exception as e:
        return _result(error=f"{ToolErrorCode.EXECUTION_FAILED.message} (notion_update_row: {e})")
    # 여기부터 속성은 이미 바뀌었을 수 있다 — 예외도 접두어를 붙여 알린다(spec §7-2)
    prefix = "속성은 바뀌었어요. 본문은 실패: " if changes else ""
    if changes:
        try:
            url = response.json().get("url", url)
        except Exception:
            pass  # 속성은 바뀌었다 — 응답을 못 읽으면 GET 때 url을 쓴다
    try:
        if not _blank(content):
            # allow_deleting_content는 보내지 않는다 — 하위 페이지·DB가 있는 행은 Notion이 거절한다(D12)
            response = await _call("patch", f"/pages/{row}/markdown", token,
                                   json={"type": "replace_content", "replace_content": {"new_str": str(content)}})
            if response.status_code != 200:
                return _result(error=prefix + _api_error(response))
    except Exception as e:
        return _result(error=prefix + f"{ToolErrorCode.EXECUTION_FAILED.message} (notion_update_row: {e})")
    return _result(success=True, pageId=page.get("id") or row, url=url)

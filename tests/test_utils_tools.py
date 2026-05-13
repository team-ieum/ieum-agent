import json
import pytest

from tools.utils import json_parse, text_extract, date_format


# ---------------------------------------------------------------------------
# json_parse
# ---------------------------------------------------------------------------

def test_json_parse_전체_객체_반환():
    """key_path 없으면 전체 JSON 객체를 반환한다."""
    result = json.loads(json_parse('{"name": "홍길동", "age": 30}'))
    assert result["success"] is True
    assert result["value"]["name"] == "홍길동"


def test_json_parse_단순_키_추출():
    """단순 키 경로로 값을 추출한다."""
    result = json.loads(json_parse('{"name": "홍길동"}', key_path="name"))
    assert result["success"] is True
    assert result["value"] == "홍길동"


def test_json_parse_중첩_키_추출():
    """점 표기법으로 중첩 키를 추출한다."""
    data = '{"data": {"user": {"name": "홍길동"}}}'
    result = json.loads(json_parse(data, key_path="data.user.name"))
    assert result["success"] is True
    assert result["value"] == "홍길동"


def test_json_parse_리스트_인덱스_추출():
    """리스트 인덱스로 값을 추출한다."""
    data = '{"items": ["첫번째", "두번째", "세번째"]}'
    result = json.loads(json_parse(data, key_path="items.1"))
    assert result["success"] is True
    assert result["value"] == "두번째"


def test_json_parse_존재하지_않는_키():
    """존재하지 않는 키 경로는 에러를 반환한다."""
    result = json.loads(json_parse('{"name": "홍길동"}', key_path="age"))
    assert "error" in result


def test_json_parse_잘못된_json():
    """잘못된 JSON 문자열은 에러를 반환한다."""
    result = json.loads(json_parse("이건 JSON이 아닙니다"))
    assert "error" in result


# ---------------------------------------------------------------------------
# text_extract
# ---------------------------------------------------------------------------

def test_text_extract_기본_매치():
    """패턴에 매치되는 첫 번째 값을 반환한다."""
    result = json.loads(text_extract("가격: 1,500원", r"\d[\d,]+"))
    assert result["success"] is True
    assert result["value"] == "1,500"


def test_text_extract_find_all():
    """find_all=True이면 모든 매치를 반환한다."""
    result = json.loads(text_extract("010-1234-5678 / 010-9876-5432", r"\d{3}-\d{4}-\d{4}", find_all=True))
    assert result["success"] is True
    assert result["count"] == 2
    assert "010-1234-5678" in result["matches"]
    assert "010-9876-5432" in result["matches"]


def test_text_extract_캡처_그룹():
    """캡처 그룹 번호로 특정 그룹만 추출한다."""
    result = json.loads(text_extract("2026-05-13", r"(\d{4})-(\d{2})-(\d{2})", group=1))
    assert result["success"] is True
    assert result["value"] == "2026"


def test_text_extract_매치_없음():
    """매치가 없으면 value=None을 반환한다."""
    result = json.loads(text_extract("abc", r"\d+"))
    assert result["success"] is True
    assert result["value"] is None


def test_text_extract_잘못된_정규식():
    """잘못된 정규식은 에러를 반환한다."""
    result = json.loads(text_extract("abc", r"[invalid"))
    assert "error" in result


# ---------------------------------------------------------------------------
# date_format
# ---------------------------------------------------------------------------

def test_date_format_iso_자동_파싱():
    """ISO 8601 형식은 input_format 없이 자동 파싱된다."""
    result = json.loads(date_format("2026-05-13T09:00:00", output_format="%Y년 %m월 %d일"))
    assert result["success"] is True
    assert result["formatted"] == "2026년 05월 13일"


def test_date_format_커스텀_입력_포맷():
    """input_format을 지정하면 해당 포맷으로 파싱한다."""
    result = json.loads(date_format("13/05/2026", input_format="%d/%m/%Y", output_format="%Y-%m-%d"))
    assert result["success"] is True
    assert result["formatted"] == "2026-05-13"


def test_date_format_날짜만():
    """날짜만 있는 ISO 형식도 파싱된다."""
    result = json.loads(date_format("2026-05-13", output_format="%m/%d/%Y"))
    assert result["success"] is True
    assert result["formatted"] == "05/13/2026"


def test_date_format_Z_포함_iso():
    """Z로 끝나는 ISO 8601 형식도 파싱된다."""
    result = json.loads(date_format("2026-05-13T09:00:00Z", output_format="%Y-%m-%d"))
    assert result["success"] is True
    assert result["formatted"] == "2026-05-13"


def test_date_format_파싱_실패():
    """파싱할 수 없는 날짜 문자열은 에러를 반환한다."""
    result = json.loads(date_format("이건 날짜가 아닙니다", output_format="%Y-%m-%d"))
    assert "error" in result

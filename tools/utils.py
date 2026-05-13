import json
import re
from datetime import datetime

from common.error_code import ErrorCode


def json_parse(
    json_string: str,
    key_path: str = None,
) -> str:
    """
    JSON 문자열을 파싱하여 특정 키 경로의 값을 추출합니다.

    Args:
        json_string: 파싱할 JSON 문자열
        key_path: 추출할 키 경로 (점 표기법, 예: "data.user.name")
                  생략 시 전체 JSON 객체 반환

    Returns:
        추출된 값을 포함한 JSON 문자열
    """
    try:
        data = json.loads(json_string)

        if not key_path:
            return json.dumps({"success": True, "value": data}, ensure_ascii=False)

        # 점 표기법으로 중첩 키 탐색
        keys = key_path.split(".")
        value = data
        for key in keys:
            if isinstance(value, dict):
                if key not in value:
                    return json.dumps({
                        "error": f"키를 찾을 수 없습니다: '{key_path}' ('{key}' 없음)"
                    }, ensure_ascii=False)
                value = value[key]
            elif isinstance(value, list):
                try:
                    index = int(key)
                    value = value[index]
                except (ValueError, IndexError):
                    return json.dumps({
                        "error": f"리스트 인덱스 오류: '{key}'"
                    }, ensure_ascii=False)
            else:
                return json.dumps({
                    "error": f"탐색 불가한 값 타입: '{type(value).__name__}' at '{key}'"
                }, ensure_ascii=False)

        return json.dumps({"success": True, "value": value}, ensure_ascii=False)

    except json.JSONDecodeError as e:
        return json.dumps({
            "error": f"{ErrorCode.TOOL_EXECUTION_FAILED.message} (json_parse: JSON 파싱 오류 - {str(e)})"
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "error": f"{ErrorCode.TOOL_EXECUTION_FAILED.message} (json_parse: {str(e)})"
        }, ensure_ascii=False)


def text_extract(
    text: str,
    pattern: str,
    group: int = 0,
    find_all: bool = False,
) -> str:
    """
    텍스트에서 정규식 패턴으로 값을 추출합니다.

    Args:
        text: 대상 텍스트
        pattern: Python 정규식 패턴
        group: 캡처 그룹 번호 (기본값: 0 = 전체 매치)
        find_all: 모든 매치 반환 여부 (기본값: False)

    Returns:
        추출된 값 또는 값 목록을 포함한 JSON 문자열
    """
    try:
        if find_all:
            matches = re.findall(pattern, text)
            return json.dumps({
                "success": True,
                "matches": matches,
                "count": len(matches),
            }, ensure_ascii=False)

        match = re.search(pattern, text)
        if not match:
            return json.dumps({
                "success": True,
                "value": None,
                "matches": [],
            }, ensure_ascii=False)

        value = match.group(group)
        all_matches = re.findall(pattern, text)

        return json.dumps({
            "success": True,
            "value": value,
            "matches": all_matches,
        }, ensure_ascii=False)

    except re.error as e:
        return json.dumps({
            "error": f"{ErrorCode.TOOL_EXECUTION_FAILED.message} (text_extract: 정규식 오류 - {str(e)})"
        }, ensure_ascii=False)
    except IndexError:
        return json.dumps({
            "error": f"{ErrorCode.TOOL_EXECUTION_FAILED.message} (text_extract: 그룹 번호 {group}이 존재하지 않습니다)"
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "error": f"{ErrorCode.TOOL_EXECUTION_FAILED.message} (text_extract: {str(e)})"
        }, ensure_ascii=False)


def date_format(
    date_string: str,
    output_format: str,
    input_format: str = None,
) -> str:
    """
    날짜 문자열을 원하는 형식으로 변환합니다.

    Args:
        date_string: 변환할 날짜 문자열
        output_format: 출력 날짜 형식 (strptime 형식, 예: "%Y년 %m월 %d일")
        input_format: 입력 날짜 형식 (생략 시 ISO 8601 자동 파싱 시도)

    Returns:
        변환된 날짜 문자열을 포함한 JSON 문자열
    """
    # ISO 8601 자동 파싱을 위한 후보 포맷 목록
    _ISO_FORMATS = [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]

    try:
        dt = None

        if input_format:
            dt = datetime.strptime(date_string, input_format)
        else:
            # ISO 8601 자동 파싱 시도
            for fmt in _ISO_FORMATS:
                try:
                    dt = datetime.strptime(date_string, fmt)
                    break
                except ValueError:
                    continue

        if dt is None:
            return json.dumps({
                "error": f"{ErrorCode.TOOL_EXECUTION_FAILED.message} (date_format: 날짜 파싱 실패 - '{date_string}'에 맞는 포맷을 찾을 수 없습니다)"
            }, ensure_ascii=False)

        formatted = dt.strftime(output_format)
        return json.dumps({
            "success": True,
            "formatted": formatted,
            "original": date_string,
        }, ensure_ascii=False)

    except ValueError as e:
        return json.dumps({
            "error": f"{ErrorCode.TOOL_EXECUTION_FAILED.message} (date_format: {str(e)})"
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "error": f"{ErrorCode.TOOL_EXECUTION_FAILED.message} (date_format: {str(e)})"
        }, ensure_ascii=False)

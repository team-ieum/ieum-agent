import inspect
import json
from unittest.mock import MagicMock, patch
from urllib.parse import unquote

import httpx
import pytest

from common.error_code import ToolErrorCode
from core import template_registry as tr
from tests.action_outputs import assert_matches_outputs
from tools import _TOOL_MAP, get_tools_for_request
from tools.google_sheets import (
    _Invalid, _col, _header_range, _resolve, _row, _row_number,
    google_sheets_append, google_sheets_append_top, google_sheets_find_or_create_row,
    google_sheets_find_spreadsheet, google_sheets_find_worksheet, google_sheets_get_row, google_sheets_get_rows,
    google_sheets_get_spreadsheet,
    google_sheets_lookup_row, google_sheets_lookup_rows, google_sheets_read, google_sheets_update_row,
)
from tools.registry import _ACTION_ONLY_TOOLS, resolve_action_fn


def _resp(status_code: int, json_data: dict) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status_code
    mock.is_success = 200 <= status_code < 300
    mock.json = MagicMock(return_value=json_data)
    mock.content = json.dumps(json_data).encode()
    return mock


def _range(url: str) -> str:
    """values/ 뒤의 경로 조각(append면 ':append' 앞)을 디코딩한 A1 범위."""
    tail = url.rsplit("/values/", 1)[1]
    return unquote(tail.removesuffix(":append"))


class _Sheets:
    """URL로 가르는 가짜 Sheets API. calls = [(method, url, kwargs)].
    sheets: [(title, sheetId, columnCount)], grid: 시트 전체 값(1행 = 머리글).
    fail: {"append"|"update"|"insert"|"delete"|"get_values": MagicMock 응답 또는 던질 예외}."""

    def __init__(self, grid, sheets=(("Sales", 0, 26),), fail=None, updated_range=None, omit_sheet_id=False,
                 omit_column_count=False, values_by_range=None, sheet_types=None, drive_files=(), drive_next_page_token=None):
        self.grid = grid
        self.drive_next_page_token = drive_next_page_token
        self.sheet_types = sheet_types or {}  # {제목: sheetType}, 없으면 GRID
        self.drive_files = list(drive_files)
        self.values_by_range = values_by_range or {}  # {A1 범위: 행 목록 또는 None(values 키 없음)}
        self.sheets = sheets
        self.fail = fail or {}
        self.updated_range = updated_range
        self.omit_sheet_id = omit_sheet_id
        self.omit_column_count = omit_column_count
        self.calls = []

    def _answer(self, kind, ok):
        failure = self.fail.get(kind)
        if isinstance(failure, BaseException):
            raise failure
        return failure if failure is not None else _resp(200, ok)

    def of(self, method, kind=None):
        return [c for c in self.calls if c[0] == method and (kind is None or self._kind(c) == kind)]

    @staticmethod
    def _kind(call):
        method, url, kwargs = call
        if method == "POST" and url.endswith(":batchUpdate"):
            return "insert" if "insertDimension" in kwargs["json"]["requests"][0] else "delete"
        return None

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if url.startswith("https://www.googleapis.com/drive/v3/files"):
            page = {"files": self.drive_files}
            if self.drive_next_page_token:
                page["nextPageToken"] = self.drive_next_page_token
            return self._answer("drive", page)
        if "/values/" not in url:
            props = []
            for index, (title, sid, count) in enumerate(self.sheets):
                p = {"sheetId": sid, "title": title, "index": index, "sheetType": self.sheet_types.get(title, "GRID"),
                     "gridProperties": {"rowCount": 1000, "columnCount": count}}
                if self.omit_sheet_id and sid == 0:
                    del p["sheetId"]
                if self.omit_column_count:
                    del p["gridProperties"]["columnCount"]
                props.append({"properties": p})
            sid = url.rsplit("/", 1)[1]
            return self._answer("get_spreadsheet", {
                "spreadsheetId": sid, "spreadsheetUrl": f"https://docs.google.com/spreadsheets/d/{sid}/edit",
                "properties": {"title": "예산", "locale": "ko_KR", "timeZone": "Asia/Seoul"}, "sheets": props})
        if _range(url) in self.values_by_range:
            rows = self.values_by_range[_range(url)]
            if isinstance(rows, MagicMock):  # 그 범위만 오류 응답
                return rows
            return self._answer("get_values", {} if rows is None else {"values": rows})
        values = self.grid[:1] if _range(url).endswith("!1:1") else self.grid
        return self._answer("get_values", {"values": values})

    async def post(self, url, **kwargs):
        call = ("POST", url, kwargs)
        self.calls.append(call)
        if url.endswith(":append"):
            title = self.sheets[0][0]
            n = len(self.grid) + 1
            return self._answer("append", {"updates": {
                "updatedRange": self.updated_range or f"'{title}'!A{n}:C{n}"}})
        return self._answer(self._kind(call), {})

    async def put(self, url, **kwargs):
        self.calls.append(("PUT", url, kwargs))
        return self._answer("update", {"updatedRange": _range(url)})


GRID = [["이름", "이메일", "", "등급"], ["a", "x@x", "", "1"], ["b", "y@y"], ["a", "z@z", "", "2"]]


async def _call(fake, tool, **kwargs):
    with patch("tools.google_sheets.get_http_client", return_value=fake):
        return json.loads(await tool(access_token="t", spreadsheet_id="s1", **kwargs))


async def _no_google(tool, **kwargs):
    with patch("tools.google_sheets.get_http_client") as factory:
        result = json.loads(await tool(access_token="t", **kwargs))
    factory.assert_not_called()
    assert ToolErrorCode.EXECUTION_FAILED.message in result["error"]
    return result


# --- 시트 찾기 ---
@pytest.mark.asyncio
async def test_시트는_sheetId로_찾고_바뀐_제목을_쓴다():
    fake = _Sheets(GRID, sheets=(("새 이름", 7, 26),))
    await _call(fake, google_sheets_update_row, sheet_id="7", row_number=3, columns={"A": "q"})
    assert _range(fake.of("PUT")[0][1]) == "'새 이름'!A3"


@pytest.mark.asyncio
@pytest.mark.parametrize("sheet_id", ["0", 0, " 0 "])
async def test_sheet_id_0은_첫_시트다(sheet_id):
    fake = _Sheets(GRID, sheets=(("Sales", 0, 26), ("B", 5, 26)))
    await _call(fake, google_sheets_update_row, sheet_id=sheet_id, row_number=3, columns={"A": "q"})
    assert _range(fake.of("PUT")[0][1]) == "'Sales'!A3"


@pytest.mark.asyncio
async def test_sheetId가_생략된_첫_시트도_0으로_찾는다():
    fake = _Sheets(GRID, sheets=(("Sales", 0, 26), ("B", 5, 26)), omit_sheet_id=True)
    await _call(fake, google_sheets_update_row, sheet_id="0", row_number=3, columns={"A": "q"})
    assert _range(fake.of("PUT")[0][1]) == "'Sales'!A3"


@pytest.mark.asyncio
async def test_숫자_제목은_그_id가_없으면_제목으로_찾는다():
    for sid in ("2024", "9"):
        fake = _Sheets(GRID, sheets=(("Sales", 0, 26), ("2024", 9, 26)))
        with patch("tools.google_sheets.get_http_client", return_value=fake):
            await google_sheets_update_row(access_token="t", spreadsheet_id="s1", sheet_id=sid,
                                           row_number=3, columns={"A": "q"})
        assert _range(fake.calls[-1][1]) == "'2024'!A3"


@pytest.mark.asyncio
async def test_없는_시트는_시트_목록을_알려_준다():
    fake = _Sheets(GRID)
    result = await _call(fake, google_sheets_update_row, sheet_id="없음", row_number=3, columns={"A": "q"})
    assert "Sales(0)" in result["error"]
    assert not [c for c in fake.calls if "/values/" in c[1]]


_TOOL_ARGS = {
    "append": (google_sheets_append, {"columns": {"A": "x"}}),
    "read": (google_sheets_read, {"cell_range": "A1"}),
    "append_top": (google_sheets_append_top, {"columns": {"A": "x"}}),
    "update_row": (google_sheets_update_row, {"row_number": 2, "columns": {"A": "x"}}),
    "lookup_row": (google_sheets_lookup_row, {"lookup_column": "A", "lookup_value": "a"}),
    "find_or_create_row": (google_sheets_find_or_create_row,
                           {"lookup_column": "A", "lookup_value": "a", "columns": {"A": "x"}}),
    "lookup_rows": (google_sheets_lookup_rows, {"lookup_column": "A", "lookup_value": "a"}),
    "get_rows": (google_sheets_get_rows, {"column_range": "A:B", "row_count": 1}),
    "get_row": (google_sheets_get_row, {"row_number": 2}),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["append", "read", "append_top", "update_row", "lookup_row", "find_or_create_row",
                                  "lookup_rows", "get_rows", "get_row"])
@pytest.mark.parametrize("spreadsheet_id, sheet_id", [
    ("", "0"), ("  ", "0"), ("..", "0"), ("a/b", "0"), ("s1", ""), ("s1", "  "), ("s1", None)])
async def test_빈_대상은_Google을_부르지_않는다(tool, spreadsheet_id, sheet_id):
    fn, kwargs = _TOOL_ARGS[tool]
    await _no_google(fn, spreadsheet_id=spreadsheet_id, sheet_id=sheet_id, **kwargs)


# --- 열 범위·열 키 ---
def test_머리글_범위는_마지막_비지_않은_머리글까지():
    assert _header_range(["이름", "", "이메일", "", ""], 26) == ["이름", "", "이메일"]
    assert _header_range(["이름", "", "  "], 26) == ["이름"]
    assert _header_range(["이름", " \t", "x"], 26) == ["이름", "", "x"]
    assert len(_header_range(["  "], 5)) == 5


def test_빈_머리글_판정은_Java_isBlank와_같다():
    assert _header_range(["이름", "　"], 26) == ["이름"]                      # 전각 공백 = 빈 머리글
    assert _header_range(["이름", "\xa0"], 26) == ["이름", "\xa0"]               # NBSP는 글자
    assert _header_range(["이름", "\u2007", "\u202f"], 26) == ["이름", "\u2007", "\u202f"]


def test_열_문자는_bijective_base26():
    assert [_col(i) for i in (0, 25, 26, 27, 51, 52)] == ["A", "Z", "AA", "AB", "AZ", "BA"]
    headers = _header_range([f"h{i}" for i in range(28)], 28)
    assert (_resolve("AA", headers), _resolve("AB", headers)) == (26, 27)


@pytest.mark.parametrize("key", ["", "  ", None])
def test_빈_열_키는_빈_머리글로_풀리지_않는다(key):
    with pytest.raises(_Invalid):
        _resolve(key, ["이름", "", "등급"])


def test_머리글_이름의_점은_퍼센트2E로_와도_찾는다():
    assert _resolve("No%2E", ["No.", "이름"]) == 0


@pytest.mark.parametrize("count, ok, bad", [(5, "E", "F"), (40, "Z", "AA")])
def test_머리글이_모두_비면_A부터_columnCount_26까지(count, ok, bad):
    headers = _header_range([], count)
    assert len(headers) == min(count, 26)
    assert _resolve(ok, headers) == len(headers) - 1
    with pytest.raises(_Invalid):
        _resolve(bad, headers)


def test_열_키는_열_문자_먼저_다음_머리글_이름():
    headers = ["이름", "B", "ID"]
    assert (_resolve("B", headers), _resolve("이름", headers), _resolve("ID", headers)) == (1, 0, 2)
    with pytest.raises(_Invalid):
        _resolve("b", headers)


def test_머리글_범위_밖_열은_쓸_수_있는_열을_알려_준다():
    with pytest.raises(_Invalid, match=r"A\(이름\), B\(이메일\), C\(\(제목 없음\)\), D\(등급\)"):
        _resolve("E", ["이름", "이메일", "", "등급"])


def test_같은_열이_두_번이면_실패한다():
    with pytest.raises(_Invalid, match=r"A\(이름\)"):
        _row({"A": "1", "이름": "2"}, ["이름"], blank="")


def test_같은_이름_머리글이_둘이면_이름으로_쓸_수_없다():
    with pytest.raises(_Invalid, match=r"A\(이름\), B\(이름\)"):
        _row({"이름": "1"}, ["이름", "이름"], blank="")
    assert _row({"B": "1"}, ["이름", "이름"], blank="") == ["", "1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [{"x": 1}, [1]])
async def test_단순값이_아니면_실패한다(value):
    # 단순값 검사는 도구 앞단 한 곳(Google 호출 전)에만 있다.
    await _no_google(google_sheets_append_top, spreadsheet_id="s1", sheet_id="0", columns={"A": value})


@pytest.mark.parametrize("blank", ["", None])
@pytest.mark.parametrize("columns", [{}, {"A": ""}, {"A": "  ", "B": None}])
def test_넣을_값이_없으면_실패한다(columns, blank):
    with pytest.raises(_Invalid, match="넣을 값이 없어요"):
        _row(columns, ["a", "b"], blank=blank)


def test_행은_A부터_채운_마지막_열까지():
    assert _row({"C": 3, "A": True}, ["a", "b", "c", "d"], blank="") == [True, "", 3]
    assert _row({"B": "x", "D": ""}, ["a", "b", "c", "d"], blank=None) == [None, "x"]


@pytest.mark.parametrize("rng, n", [("'Sales'!A5:C5", 5), ("'Bob''s'!A12:B12", 12), ("Sheet1!A7", 7),
                                    ("", None), (None, None)])
def test_updatedRange에서_행_번호를_읽는다(rng, n):
    assert _row_number(rng) == n


# --- C3 맨 위 추가 ---
_DIM = {"sheetId": 0, "dimension": "ROWS", "startIndex": 1, "endIndex": 2}


@pytest.mark.asyncio
async def test_맨_위_추가는_2행에_빈_행을_넣고_값을_쓴다():
    fake = _Sheets(GRID)
    result = await _call(fake, google_sheets_append_top, sheet_id="0", columns={"이메일": "n@n"},
                         value_input_option="RAW")
    assert fake.of("POST", "insert")[0][2]["json"] == {"requests": [{"insertDimension": {
        "range": _DIM, "inheritFromBefore": False}}]}
    _, put_url, put_kwargs = fake.of("PUT")[0]
    assert _range(put_url) == "'Sales'!A2" and put_kwargs["params"] == {"valueInputOption": "RAW"}
    assert put_kwargs["json"]["values"] == [["", "n@n"]]
    assert result == {"success": True, "updatedRange": "'Sales'!A2", "row_number": 2}
    assert_matches_outputs("google_sheets.append_top", result)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [_resp(500, {"error": {"message": "boom"}}), httpx.ReadTimeout("t")])
async def test_맨_위_추가_쓰기가_실패하면_넣은_행을_지운다(failure):
    fake = _Sheets(GRID, fail={"update": failure})
    result = await _call(fake, google_sheets_append_top, sheet_id="0", columns={"A": "x"})
    batch = [c[2]["json"] for c in fake.calls if c[1].endswith(":batchUpdate")]
    assert batch[1] == {"requests": [{"deleteDimension": {"range": _DIM}}]}
    assert ToolErrorCode.EXECUTION_FAILED.message in result["error"]
    assert "남았을 수" not in result["error"]


@pytest.mark.asyncio
async def test_보상_삭제도_실패하면_빈_행이_남았을_수_있다고_알린다():
    fake = _Sheets(GRID, fail={"update": _resp(500, {"error": {"message": "boom"}}),
                               "delete": _resp(500, {"error": {"message": "nope"}})})
    result = await _call(fake, google_sheets_append_top, sheet_id="0", columns={"A": "x"})
    assert "boom" in result["error"]
    assert "2행에 빈 행이 남았을 수 있어요" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("columns", [{"Z": "x"}, {"A": ""}])
async def test_맨_위_추가는_열_키나_값이_잘못되면_행을_넣지_않는다(columns):
    fake = _Sheets(GRID)
    result = await _call(fake, google_sheets_append_top, sheet_id="0", columns=columns)
    assert ToolErrorCode.EXECUTION_FAILED.message in result["error"]
    assert not fake.of("POST")


@pytest.mark.asyncio
async def test_시트_제목의_작은따옴표를_이스케이프한다():
    fake = _Sheets(GRID, sheets=(("Bob's", 0, 26),))
    await _call(fake, google_sheets_append_top, sheet_id="0", columns={"A": "x"})
    assert _range(fake.of("PUT")[0][1]) == "'Bob''s'!A2"


_OPTION_CALLS = [
    lambda o: google_sheets_append(access_token="t", spreadsheet_id="s1", sheet_id="0", columns={"A": "x"}, value_input_option=o),
    lambda o: google_sheets_append_top(access_token="t", spreadsheet_id="s1", sheet_id="0", columns={"A": "x"}, value_input_option=o),
    lambda o: google_sheets_update_row(access_token="t", spreadsheet_id="s1", sheet_id="0", row_number=2, columns={"A": "x"}, value_input_option=o),
    lambda o: google_sheets_find_or_create_row(access_token="t", spreadsheet_id="s1", sheet_id="0", lookup_column="A", lookup_value="없음", columns={"A": "x"}, value_input_option=o)]


@pytest.mark.asyncio
@pytest.mark.parametrize("call", _OPTION_CALLS)
async def test_입력값_해석은_두_값만(call):
    with patch("tools.google_sheets.get_http_client") as factory:
        result = json.loads(await call("FORMULA"))
    factory.assert_not_called()
    assert ToolErrorCode.EXECUTION_FAILED.message in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("call", _OPTION_CALLS)
@pytest.mark.parametrize("option", ["", None])
async def test_빈_입력값_해석은_USER_ENTERED(call, option):
    fake = _Sheets(GRID)
    with patch("tools.google_sheets.get_http_client", return_value=fake):
        result = json.loads(await call(option))
    assert "error" not in result
    options = [kw["params"]["valueInputOption"] for _, _, kw in fake.calls if "valueInputOption" in kw.get("params", {})]
    assert options == ["USER_ENTERED"]


# --- C4 행 수정 ---
@pytest.mark.asyncio
async def test_행_수정은_안_채운_열을_None으로_둔다():
    fake = _Sheets(GRID)
    result = await _call(fake, google_sheets_update_row, sheet_id="0", row_number="5", columns={"B": "x", "D": ""})
    _, put_url, put_kwargs = fake.of("PUT")[0]
    assert _range(put_url) == "'Sales'!A5"
    assert put_kwargs["json"]["values"] == [[None, "x"]]
    assert result == {"success": True, "updatedRange": "'Sales'!A5", "row_number": 5}
    assert_matches_outputs("google_sheets.update_row", result)


@pytest.mark.asyncio
async def test_모든_열_덮어쓰기는_머리글_끝까지_빈_문자열():
    fake = _Sheets(GRID)
    await _call(fake, google_sheets_update_row, sheet_id="0", row_number=5, columns={"B": "x"}, overwrite_all=True)
    assert fake.of("PUT")[0][2]["json"]["values"] == [["", "x", "", ""]]


@pytest.mark.asyncio
@pytest.mark.parametrize("row", ["0", 0, -1, "abc", 1.5, "", None, True])
async def test_행_번호가_잘못되면_Google을_부르지_않는다(row):
    await _no_google(google_sheets_update_row, spreadsheet_id="s1", sheet_id="0", row_number=row, columns={"A": "x"})
    await _no_google(google_sheets_get_row, spreadsheet_id="s1", sheet_id="0", row_number=row)


@pytest.mark.asyncio
@pytest.mark.parametrize("overwrite_all", ["false", "False", 0, None])
async def test_모든_열_덮어쓰기는_참일_때만(overwrite_all):
    fake = _Sheets(GRID)
    await _call(fake, google_sheets_update_row, sheet_id="0", row_number="5", columns={"B": "x"},
                overwrite_all=overwrite_all)
    assert fake.of("PUT")[0][2]["json"]["values"] == [[None, "x"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("row, n", [("5", 5), (5, 5), (" 7 ", 7), (1, 1)])
async def test_행_번호는_문자열_숫자도_받는다(row, n):
    fake = _Sheets(GRID)
    result = await _call(fake, google_sheets_update_row, sheet_id="0", row_number=row, columns={"A": "x"})
    assert _range(fake.of("PUT")[0][1]) == f"'Sales'!A{n}"
    assert result["row_number"] == n


# --- S1 값으로 행 찾기 ---
async def _lookup(grid=GRID, **kwargs):
    fake = _Sheets(grid)
    result = await _call(fake, google_sheets_lookup_row, sheet_id="0", **kwargs)
    return fake, result


@pytest.mark.asyncio
async def test_값으로_행_찾기는_첫_일치_행을_돌려준다():
    fake, result = await _lookup(lookup_column="A", lookup_value="a")
    values = {"A": "a", "B": "x@x", "C": "", "D": "1"}
    assert result == {"success": True, "found": True, "total": 1,
                      "items": [{"row_number": 2, "values": values}],
                      "row_number": 2, "values": values}
    gets = [c for c in fake.calls if "/values/" in c[1]]
    assert len(gets) == 1 and _range(gets[0][1]) == "'Sales'"
    assert gets[0][2]["params"] == {"valueRenderOption": "FORMATTED_VALUE"}
    assert_matches_outputs("google_sheets.lookup_row", result)


@pytest.mark.asyncio
async def test_마지막_행부터_찾기():
    _, result = await _lookup(lookup_column="A", lookup_value="a", from_bottom=True)
    assert result["row_number"] == 4


@pytest.mark.asyncio
async def test_보조_열은_두_조건을_모두_만족하는_행():
    _, result = await _lookup(lookup_column="A", lookup_value="a", lookup_column_2="D", lookup_value_2="2")
    assert result["row_number"] == 4


@pytest.mark.asyncio
async def test_찾을_열은_머리글_이름도_받는다():
    _, result = await _lookup(lookup_column="이메일", lookup_value="y@y")
    assert result["row_number"] == 3 and result["values"]["D"] == ""


@pytest.mark.asyncio
async def test_26열을_넘는_시트의_values_키는_AA():
    grid = [[f"h{i}" for i in range(28)], [f"v{i}" for i in range(28)]]
    _, result = await _lookup(grid, lookup_column="AB", lookup_value="v27")
    assert result["found"] is True
    assert result["values"]["AA"] == "v26" and result["values"]["AB"] == "v27"


@pytest.mark.asyncio
async def test_머리글_행은_찾지_않는다():
    _, result = await _lookup(lookup_column="A", lookup_value="이름")
    assert result["found"] is False


@pytest.mark.asyncio
async def test_비교는_대소문자를_구분한다():
    _, result = await _lookup(lookup_column="A", lookup_value="A")
    assert result["found"] is False


@pytest.mark.asyncio
async def test_못_찾으면_빈_결과로_성공한다():
    _, result = await _lookup(lookup_column="A", lookup_value="zzz")
    assert result == {"success": True, "found": False, "total": 0, "items": [], "row_number": None, "values": {}}
    assert_matches_outputs("google_sheets.lookup_row", result)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [google_sheets_lookup_row, google_sheets_find_or_create_row,
                                  google_sheets_lookup_rows])
@pytest.mark.parametrize("kwargs", [
    {"lookup_value": ""}, {"lookup_value": "   "}, {"lookup_value": None},
    {"lookup_value": "a", "lookup_column_2": "D", "lookup_value_2": ""},
    {"lookup_value": "a", "lookup_column_2": "D", "lookup_value_2": None},
    {"lookup_column": "", "lookup_value": "a"}, {"lookup_column": "  ", "lookup_value": "a"}])
async def test_찾을_값이_비면_Google을_부르지_않는다(tool, kwargs):
    kwargs = {"lookup_column": "A", **kwargs}
    if tool is google_sheets_find_or_create_row:
        kwargs["columns"] = {"A": "x"}
    await _no_google(tool, spreadsheet_id="s1", sheet_id="0", **kwargs)


# --- S2 값으로 여러 행 찾기 ---
async def _lookup_rows(**kwargs):
    fake = _Sheets(GRID)
    result = await _call(fake, google_sheets_lookup_rows, sheet_id="0", lookup_column="A", **kwargs)
    return fake, result


@pytest.mark.asyncio
async def test_여러_행_찾기는_일치_행을_전부_돌려준다():
    fake, result = await _lookup_rows(lookup_value="a")
    assert (result["found"], result["total"]) == (True, 2)
    assert [i["row_number"] for i in result["items"]] == [2, 4]
    assert result["items"][0]["values"] == {"A": "a", "B": "x@x", "C": "", "D": "1"}
    gets = [c for c in fake.calls if "/values/" in c[1]]
    assert len(gets) == 1 and _range(gets[0][1]) == "'Sales'"
    assert_matches_outputs("google_sheets.lookup_rows", result)


@pytest.mark.asyncio
async def test_여러_행_찾기_마지막부터와_최대_개수():
    _, result = await _lookup_rows(lookup_value="a", from_bottom=True, max_rows=1)
    assert [i["row_number"] for i in result["items"]] == [4]


@pytest.mark.asyncio
@pytest.mark.parametrize("start_row, rows", [(3, [4]), ("1", [2, 4]), (0, [2, 4]), ("", [2, 4])])
async def test_여러_행_찾기_시작_행(start_row, rows):
    _, result = await _lookup_rows(lookup_value="a", start_row=start_row)
    assert [i["row_number"] for i in result["items"]] == rows


@pytest.mark.asyncio
async def test_여러_행_찾기_0건은_성공():
    _, result = await _lookup_rows(lookup_value="zzz")
    assert result == {"success": True, "found": False, "total": 0, "items": [], "truncated": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("max_rows", [0, 501, "abc", True])
async def test_최대_개수_범위_밖은_Google을_부르지_않는다(max_rows):
    await _no_google(google_sheets_lookup_rows, spreadsheet_id="s1", sheet_id="0", lookup_column="A",
                     lookup_value="a", max_rows=max_rows)


# --- S4 여러 행 가져오기 · S5 행 번호로 가져오기 ---
def _value_gets(fake):
    return [_range(c[1]) for c in fake.calls if "/values/" in c[1]]


@pytest.mark.asyncio
async def test_여러_행_가져오기는_범위를_조립한다():
    fake = _Sheets(GRID, values_by_range={"'Sales'!A2:D4": GRID[1:]})
    result = await _call(fake, google_sheets_get_rows, sheet_id="0", column_range="A:D", row_count=3)
    assert _value_gets(fake) == ["'Sales'!A2:D4"]
    assert fake.of("GET")[-1][2]["params"] == {"valueRenderOption": "FORMATTED_VALUE"}
    assert (result["found"], result["total"]) == (True, 3)
    assert [i["row_number"] for i in result["items"]] == [2, 3, 4]
    assert result["items"][1]["values"] == {"A": "b", "B": "y@y", "C": "", "D": ""}
    assert_matches_outputs("google_sheets.get_rows", result)


@pytest.mark.asyncio
async def test_중간_빈_행은_빈_값으로_넣는다():
    fake = _Sheets(GRID, values_by_range={"'Sales'!A2:B4": [["a"], [], ["c"]]})
    result = await _call(fake, google_sheets_get_rows, sheet_id="0", column_range="A:B", row_count=3)
    # 받은 데이터 폭(가장 긴 행 = 1열)까지만 키를 만든다. B는 어느 행에도 값이 없어 빠진다.
    assert result["total"] == 3 and result["items"][1]["values"] == {"A": ""}


@pytest.mark.asyncio
async def test_한_결과의_항목은_가장_긴_행_폭으로_키가_같다():
    fake = _Sheets(GRID, values_by_range={"'Sales'!A2:D3": [["a"], ["b", "c", "d"]]})
    result = await _call(fake, google_sheets_get_rows, sheet_id="0", column_range="A:D", row_count=2)
    assert [i["values"] for i in result["items"]] == [{"A": "a", "B": "", "C": ""}, {"A": "b", "B": "c", "C": "d"}]


@pytest.mark.asyncio
async def test_데이터_밖_범위는_0건():
    fake = _Sheets(GRID, values_by_range={"'Sales'!A100:B101": None})
    result = await _call(fake, google_sheets_get_rows, sheet_id="0", column_range="A:B", row_count=2, start_row=100)
    assert result == {"success": True, "found": False, "total": 0, "items": [], "truncated": False}


_BIG = "가" * 1000  # 한 칸 UTF-8 3,000바이트


@pytest.mark.asyncio
async def test_여러_행_가져오기는_2MB를_넘기_전에_멈춘다():
    rows = [[_BIG]] * 1500  # 약 4.5MB
    fake = _Sheets(GRID, values_by_range={"'Sales'!A2:A1501": rows})
    with patch("tools.google_sheets.get_http_client", return_value=fake):
        raw = await google_sheets_get_rows(access_token="t", spreadsheet_id="s1", sheet_id="0", column_range="A:A",
                                           row_count=1500)
    result = json.loads(raw)
    assert result["truncated"] is True and 0 < result["total"] == len(result["items"]) < 1500
    assert len(raw.encode()) < 2_100_000
    assert [i["row_number"] for i in result["items"]] == list(range(2, 2 + result["total"]))  # 앞에서부터 담는다
    assert_matches_outputs("google_sheets.get_rows", result)


@pytest.mark.asyncio
async def test_예산보다_큰_첫_행만_있어도_찾은_것으로_본다():
    # found가 거짓이면 '못 찾음 → 행 추가' 분기가 중복 행을 만든다.
    grid = [["이름", "메모"]] + [["a", "가" * 700_000]]  # 한 행 ≈ 2.1MB
    fake = _Sheets(grid)
    with patch("tools.google_sheets.get_http_client", return_value=fake):
        raw = await google_sheets_lookup_rows(access_token="t", spreadsheet_id="s1", sheet_id="0",
                                              lookup_column="A", lookup_value="a")
    result = json.loads(raw)
    assert (result["found"], result["total"], result["items"], result["truncated"]) == (True, 0, [], True)


@pytest.mark.asyncio
async def test_여러_행_찾기는_2MB를_넘기_전에_멈춘다():
    grid = [["이름", "메모", "메모2"]] + [["a", _BIG, _BIG]] * 600  # 상한 500행 × 6KB ≈ 3MB
    fake = _Sheets(grid)
    with patch("tools.google_sheets.get_http_client", return_value=fake):
        raw = await google_sheets_lookup_rows(access_token="t", spreadsheet_id="s1", sheet_id="0",
                                              lookup_column="A", lookup_value="a")
    result = json.loads(raw)
    assert result["truncated"] is True and 0 < result["total"] < 500
    assert len(raw.encode()) < 2_100_000
    assert_matches_outputs("google_sheets.lookup_rows", result)


@pytest.mark.asyncio
async def test_여러_행_가져오기는_시트_제목도_받는다():
    fake = _Sheets(GRID, sheets=(("Sales", 5, 26),), values_by_range={"'Sales'!A2:B2": [["a", "b"]]})
    result = await _call(fake, google_sheets_get_rows, sheet_id="Sales", column_range="A:B", row_count=1)
    assert _value_gets(fake) == ["'Sales'!A2:B2"] and result["found"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("column_range", ["", "a:z", "A", "A:", "AAAA:B", "D:A", "A1:B2"])
async def test_열_범위가_잘못되면_Google을_부르지_않는다(column_range):
    await _no_google(google_sheets_get_rows, spreadsheet_id="s1", sheet_id="0", column_range=column_range,
                     row_count=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("row_count", [0, 1501, "", None, "x"])
async def test_가져올_행_수_범위_밖(row_count):
    await _no_google(google_sheets_get_rows, spreadsheet_id="s1", sheet_id="0", column_range="A:D",
                     row_count=row_count)


@pytest.mark.asyncio
async def test_가져올_행_수_1500은_된다():
    fake = _Sheets(GRID)
    await _call(fake, google_sheets_get_rows, sheet_id="0", column_range="A:D", row_count="1500")
    assert _value_gets(fake) == ["'Sales'!A2:D1501"]


@pytest.mark.asyncio
async def test_행_번호로_가져오기():
    fake = _Sheets(GRID, values_by_range={"'Sales'!4:4": [GRID[3]]})
    result = await _call(fake, google_sheets_get_row, sheet_id="0", row_number="4")
    assert _value_gets(fake) == ["'Sales'!1:1", "'Sales'!4:4"]
    values = {"A": "a", "B": "z@z", "C": "", "D": "2"}
    assert result == {"success": True, "found": True, "total": 1, "items": [{"row_number": 4, "values": values}],
                      "row_number": 4, "values": values}
    assert_matches_outputs("google_sheets.get_row", result)


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [None, [["", ""]]])
async def test_빈_행이나_데이터_밖_행은_못_찾음(rows):
    fake = _Sheets(GRID, values_by_range={"'Sales'!9:9": rows})
    result = await _call(fake, google_sheets_get_row, sheet_id="0", row_number=9)
    assert result == {"success": True, "found": False, "total": 0, "items": [], "row_number": None, "values": {}}


# --- S6 스프레드시트 찾기 · S7 워크시트 찾기 · S8 ID로 스프레드시트 가져오기 ---
_FILE = {"id": "f1", "name": "예산", "webViewLink": "https://docs.google.com/spreadsheets/d/f1/edit",
         "modifiedTime": "2026-11-01T00:00:00.000Z", "ownedByMe": True}
_SPREADSHEET_Q = "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false and "


async def _find_spreadsheet(files=(_FILE,), fake=None, **kwargs):
    fake = fake or _Sheets(GRID, drive_files=files)
    with patch("tools.google_sheets.get_http_client", return_value=fake):
        result = json.loads(await google_sheets_find_spreadsheet(access_token="t", **kwargs))
    return fake, result


@pytest.mark.asyncio
async def test_스프레드시트_찾기_정확히():
    fake, result = await _find_spreadsheet(name="예산")
    [(_, url, kwargs)] = fake.calls
    assert url == "https://www.googleapis.com/drive/v3/files"
    assert kwargs["params"] == {"q": _SPREADSHEET_Q + "name = '예산'",
                                "fields": "nextPageToken,files(id,name,webViewLink,modifiedTime,ownedByMe)",
                                "orderBy": "modifiedTime desc", "pageSize": 50, "supportsAllDrives": "true",
                                "includeItemsFromAllDrives": "true"}
    assert result == {"success": True, "found": True, "total": 1, "truncated": False, "items": [
        {"id": "f1", "name": "예산", "url": _FILE["webViewLink"], "modified_time": _FILE["modifiedTime"],
         "owned_by_me": True}]}
    assert_matches_outputs("google_sheets.find_spreadsheet", result)


@pytest.mark.asyncio
async def test_남이_공유한_파일은_내_파일이_아니다():
    # ownedByMe가 거짓이거나 빠지면(공유 드라이브 등) 내 파일이 아니다 — 남이 만든 같은 이름 시트를 가려내게.
    _, result = await _find_spreadsheet(files=[{**_FILE, "ownedByMe": False}, {"id": "f2", "name": "예산"}],
                                        name="예산")
    assert [i["owned_by_me"] for i in result["items"]] == [False, False]


@pytest.mark.asyncio
async def test_다음_페이지가_있으면_잘림():
    _, result = await _find_spreadsheet(fake=_Sheets(GRID, drive_files=[_FILE], drive_next_page_token="p2"),
                                        name="예산", match="prefix")
    assert result["truncated"] is True and result["total"] == 1


@pytest.mark.asyncio
async def test_Drive_오류는_Drive로_알린다():
    fake = _Sheets(GRID, fail={"drive": _resp(403, {"error": {"message": "insufficient scope"}})})
    _, result = await _find_spreadsheet(fake=fake, name="예산")
    assert "Google Drive API 오류 (403): insufficient scope" in result["error"]
    assert "Sheets API" not in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("match, expected", [("prefix", "name contains '예산'"), ("", "name = '예산'"),
                                             (None, "name = '예산'")])
async def test_스프레드시트_찾기_앞부분(match, expected):
    fake, _ = await _find_spreadsheet(name="예산", match=match)
    assert fake.calls[0][2]["params"]["q"] == _SPREADSHEET_Q + expected


@pytest.mark.asyncio
async def test_검색식_이스케이프():
    fake, _ = await _find_spreadsheet(name="Bob's \\ x")
    assert fake.calls[0][2]["params"]["q"].endswith("name = 'Bob\\'s \\\\ x'")


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"name": ""}, {"name": "  "}, {"name": None}, {"name": "a", "match": "regex"}])
async def test_스프레드시트_찾기_거절(kwargs):
    await _no_google(google_sheets_find_spreadsheet, **kwargs)


@pytest.mark.asyncio
async def test_스프레드시트_0건():
    _, result = await _find_spreadsheet(files=[], name="없음")
    assert result == {"success": True, "found": False, "total": 0, "items": [], "truncated": False}


_TWO_SHEETS = (("Sales", 0, 26), ("요약", 7, 5))


@pytest.mark.asyncio
async def test_워크시트_찾기():
    fake = _Sheets(GRID, sheets=_TWO_SHEETS)
    result = await _call(fake, google_sheets_find_worksheet, title="요약")
    item = {"sheet_id": "7", "title": "요약", "index": 1, "row_count": 1000, "column_count": 5}
    assert result == {"success": True, "found": True, "total": 1, "items": [item], "sheet_id": "7", "title": "요약"}
    assert "sheetType" in fake.calls[0][2]["params"]["fields"]
    assert_matches_outputs("google_sheets.find_worksheet", result)


@pytest.mark.asyncio
async def test_워크시트_찾기는_대소문자_구분():
    result = await _call(_Sheets(GRID, sheets=_TWO_SHEETS), google_sheets_find_worksheet, title="sales")
    assert result == {"success": True, "found": False, "total": 0, "items": [], "sheet_id": None, "title": None}


@pytest.mark.asyncio
async def test_GRID가_아닌_시트는_찾지_않는다():
    fake = _Sheets(GRID, sheet_types={"Sales": "OBJECT"})
    result = await _call(fake, google_sheets_find_worksheet, title="Sales")
    assert result["found"] is False


@pytest.mark.asyncio
async def test_스프레드시트_가져오기():
    fake = _Sheets(GRID, sheets=_TWO_SHEETS + (("차트", 9, 1),), sheet_types={"차트": "OBJECT"})
    result = await _call(fake, google_sheets_get_spreadsheet)
    sheets = [{"sheet_id": "0", "title": "Sales", "index": 0, "row_count": 1000, "column_count": 26},
              {"sheet_id": "7", "title": "요약", "index": 1, "row_count": 1000, "column_count": 5}]
    url = "https://docs.google.com/spreadsheets/d/s1/edit"
    assert result == {"success": True, "found": True, "total": 1,
                      "items": [{"id": "s1", "title": "예산", "locale": "ko_KR", "time_zone": "Asia/Seoul",
                                 "url": url, "sheets": sheets}],
                      "title": "예산", "url": url, "sheets": sheets}
    assert "includeGridData" not in fake.calls[0][2]["params"]["fields"]
    assert_matches_outputs("google_sheets.get_spreadsheet", result)


@pytest.mark.asyncio
async def test_없는_스프레드시트는_못_찾음():
    fake = _Sheets(GRID, fail={"get_spreadsheet": _resp(404, {"error": {"message": "not found"}})})
    result = await _call(fake, google_sheets_get_spreadsheet)
    assert result == {"success": True, "found": False, "total": 0, "items": [], "title": None, "url": None,
                      "sheets": []}


@pytest.mark.asyncio
async def test_스프레드시트_403은_실패():
    fake = _Sheets(GRID, fail={"get_spreadsheet": _resp(403, {"error": {"message": "denied"}})})
    result = await _call(fake, google_sheets_get_spreadsheet)
    assert ToolErrorCode.EXECUTION_FAILED.message in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("spreadsheet_id", ["", "..", "a/b"])
async def test_스프레드시트_대상이_잘못되면_Google을_부르지_않는다(spreadsheet_id):
    await _no_google(google_sheets_get_spreadsheet, spreadsheet_id=spreadsheet_id)
    await _no_google(google_sheets_find_worksheet, spreadsheet_id=spreadsheet_id, title="Sales")


@pytest.mark.asyncio
@pytest.mark.parametrize("title", ["", None])
async def test_워크시트_제목이_비면_Google을_부르지_않는다(title):
    await _no_google(google_sheets_find_worksheet, spreadsheet_id="s1", title=title)


# --- S11 ---
@pytest.mark.asyncio
async def test_찾으면_만들지_않는다():
    fake = _Sheets(GRID)
    result = await _call(fake, google_sheets_find_or_create_row, sheet_id="0", lookup_column="A",
                         lookup_value="a", columns={"A": "a"})
    assert result == {"success": True, "created": False, "row_number": 2,
                      "values": {"A": "a", "B": "x@x", "C": "", "D": "1"}}
    assert not fake.of("POST")
    assert_matches_outputs("google_sheets.find_or_create_row", result)


@pytest.mark.asyncio
async def test_없으면_표_끝에_추가한다():
    fake = _Sheets(GRID, updated_range="'Sales'!A5:B5")
    result = await _call(fake, google_sheets_find_or_create_row, sheet_id="0", lookup_column="A",
                         lookup_value="c", columns={"A": "c", "이메일": "c@c"})
    assert result == {"success": True, "created": True, "row_number": 5,
                      "values": {"A": "c", "B": "c@c", "C": "", "D": ""}}
    _, url, kwargs = fake.of("POST")[0]
    assert url.endswith(":append") and _range(url) == "'Sales'!A1"
    assert kwargs["params"] == {"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"}
    assert kwargs["json"] == {"range": "'Sales'!A1", "majorDimension": "ROWS", "values": [["c", "c@c"]]}
    assert_matches_outputs("google_sheets.find_or_create_row", result)


@pytest.mark.asyncio
@pytest.mark.parametrize("columns", [None, {}])
async def test_없는데_넣을_값이_없으면_실패한다(columns):
    fake = _Sheets(GRID)
    result = await _call(fake, google_sheets_find_or_create_row, sheet_id="0", lookup_column="A",
                         lookup_value="c", columns=columns)
    assert "넣을 값이 없어요" in result["error"]
    assert not fake.of("POST")
    assert [c for c in fake.calls if "/values/" in c[1]]  # 찾기는 했다


# --- C1 행 추가·읽기 ---
@pytest.mark.asyncio
async def test_행_추가는_A1에_INSERT_ROWS로_붙인다():
    fake = _Sheets(GRID, updated_range="'Sales'!A5:D5")
    with patch("tools.google_sheets.get_http_client", return_value=fake):
        result = await google_sheets_append(access_token="t", spreadsheet_id="s1", sheet_id="0",
                                            columns={"이름": "홍길동", "D": 3})
    [(_, post_url, post_kwargs)] = fake.of("POST")
    assert post_url.endswith(":append") and _range(post_url) == "'Sales'!A1"
    assert post_kwargs["params"] == {"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"}
    assert post_kwargs["json"] == {"range": "'Sales'!A1", "majorDimension": "ROWS", "values": [["홍길동", "", "", 3]]}
    assert json.loads(result) == {"success": True, "updatedRange": "'Sales'!A5:D5", "row_number": 5}
    assert_matches_outputs("google_sheets.append", result)


@pytest.mark.asyncio
async def test_columnCount가_없으면_26열로_본다():
    for key, ok in (("Z", True), ("AA", False)):
        fake = _Sheets([["", "  "]], sheets=(("Sales", 0, 40),), omit_column_count=True)
        result = await _call(fake, google_sheets_append, sheet_id="0", columns={key: "x"})
        if ok:
            assert fake.of("POST")[0][2]["json"]["values"] == [[""] * 25 + ["x"]]
        else:
            assert "Z((제목 없음))" in result["error"] and not fake.of("POST")


@pytest.mark.asyncio
@pytest.mark.parametrize("cell_range, expected", [
    ("Sheet1!A1:B2", "'Sales'!A1:B2"), ("A:B", "'Sales'!A:B"), ("", "'Sales'"), (5, "'Sales'!5")])
async def test_읽기는_sheet_id로_시트를_찾고_범위를_붙인다(cell_range, expected):
    fake = _Sheets(GRID)
    with patch("tools.google_sheets.get_http_client", return_value=fake):
        result = await google_sheets_read(access_token="t", spreadsheet_id="s1", sheet_id="0", cell_range=cell_range)
    [(_, url, _)] = [c for c in fake.calls if "/values/" in c[1]]
    assert _range(url) == expected
    assert json.loads(result) == {"success": True, "range": expected, "values": GRID}
    assert_matches_outputs("google_sheets.read", result)


@pytest.mark.asyncio
async def test_읽기_API_오류는_실패로_돌려준다():
    fake = _Sheets(GRID, fail={"get_values": _resp(403, {"error": {"message": "denied"}})})
    result = await _call(fake, google_sheets_read, sheet_id="0", cell_range="A1")
    assert ToolErrorCode.EXECUTION_FAILED.message in result["error"]
    assert "Google Sheets API 오류 (403): denied" in result["error"]


@pytest.mark.asyncio
async def test_숫자_spreadsheet_id는_문자열로_쓴다():
    fake = _Sheets(GRID)
    with patch("tools.google_sheets.get_http_client", return_value=fake):
        result = json.loads(await google_sheets_read(access_token="t", spreadsheet_id=12345, sheet_id="0",
                                                     cell_range="A1:B2"))
    assert result["success"] is True
    assert all("/spreadsheets/12345" in url for _, url, _ in fake.calls)


@pytest.mark.asyncio
async def test_시트_제목의_예약_문자는_경로로_새지_않는다():
    fake = _Sheets(GRID, sheets=(("a/b#c?d", 0, 26),))
    await _call(fake, google_sheets_read, sheet_id="0", cell_range="A1")
    [(_, url, _)] = [c for c in fake.calls if "/values/" in c[1]]
    tail = url.rsplit("/values/", 1)[1]
    assert not any(ch in tail for ch in "/#?")
    assert _range(url) == "'a/b#c?d'!A1"


# --- AI 노드 노출 ---
@pytest.mark.asyncio
async def test_AI_노드는_시트를_고정하고_열을_이름으로_넘긴다():
    [bound] = get_tools_for_request([{"name": "builtin:google_sheets_append",
        "config": {"spreadsheet_id": "abc", "sheet_id": "0", "_names": {"sheet_id": "Sales"}}}])
    assert set(inspect.signature(bound.func).parameters) == {"access_token", "columns", "value_input_option"}
    assert "sheet_id:" not in (bound.func.__doc__ or "")
    fake = _Sheets(GRID)
    with patch("tools.google_sheets.get_http_client", return_value=fake):
        result = json.loads(await bound.func(access_token="t", columns={"이메일": "a@x"}))
    assert result["success"] is True
    [(_, url, kwargs)] = fake.of("POST")
    assert "/spreadsheets/abc/" in url and kwargs["json"]["values"] == [["", "a@x"]]


def test_AI_노드_도구_선택지의_Sheets는_쓰기_전용_셋을_뺀다():
    tools = next(f for f in tr.get_template("ai.agent")["inputFields"] if f["key"] == "tools")
    sheets = {c["id"] for c in tools["choices"] if c["id"].startswith("builtin:google_sheets_")}
    assert sheets == {f"builtin:google_sheets_{x}" for x in (
        "append", "lookup_row", "read", "lookup_rows", "get_rows", "get_row",
        "find_spreadsheet", "find_worksheet", "get_spreadsheet")}


def test_ACTION_전용_Sheets_도구는_AI_목록_밖에서만_풀린다():
    for name in ("append_top", "update_row", "find_or_create_row"):
        key = f"builtin:google_sheets_{name}"
        assert key not in _TOOL_MAP and key in _ACTION_ONLY_TOOLS and resolve_action_fn(key) is not None
    assert resolve_action_fn("builtin:google_sheets_write") is None


@pytest.mark.asyncio
async def test_행_수가_마이너스_두_개면_친절한_오류():
    result = await _no_google(google_sheets_get_rows, spreadsheet_id="s1", sheet_id="0", column_range="A:D",
                              row_count="--5")
    assert "가져올 행 수이(가) 올바르지 않아요" in result["error"]


@pytest.mark.asyncio
async def test_넓은_열_범위는_받은_데이터_폭까지만_값을_채운다():
    # 시트 열 수(그리드)로만 줄이면 데이터가 몇 열뿐인 넓은 그리드에서 빈 키가 폭증해 BE 4MB 응답 한도를 넘는다.
    fake = _Sheets(GRID, sheets=(("Sales", 0, 4),), values_by_range={"'Sales'!A2:ZZZ2": [["a", "b"]]})
    result = await _call(fake, google_sheets_get_rows, sheet_id="0", column_range="A:ZZZ", row_count=1)
    assert result["items"][0]["values"] == {"A": "a", "B": "b"}


@pytest.mark.asyncio
async def test_그리드가_넓어도_데이터_3열이면_키_3개():
    fake = _Sheets(GRID, sheets=(("Sales", 0, 1000),), values_by_range={"'Sales'!A2:ALL3": [["a", "b", "c"]] * 2})
    result = await _call(fake, google_sheets_get_rows, sheet_id="0", column_range="A:ALL", row_count=2)
    assert all(list(i["values"]) == ["A", "B", "C"] for i in result["items"])


@pytest.mark.asyncio
async def test_행_찾기는_상한에_닿으면_더_만들지_않는다():
    # 일치 행이 많아도 limit=1이면 {열: 값}은 한 번만 만든다(첫 일치에서 멈춤). _col 호출 = 머리글 4열 × 1행.
    grid = [GRID[0]] + [["a", "x", "", "1"]] * 100
    with patch("tools.google_sheets._col", wraps=_col) as col:
        _, result = await _lookup(grid, lookup_column="A", lookup_value="a")
    assert result["row_number"] == 2 and col.call_count == 4


_GRID_LIMIT = {"error": {"code": 400, "message": "Range ('Sales'!5000) exceeds grid limits. Max rows: 1006, max columns: 26"}}


@pytest.mark.asyncio
async def test_시트_밖_행_번호는_0건():
    # dev e2e(2026-11-10): Google은 그리드 밖 범위를 400으로 거절한다 — 찾기 약속대로 found:false로 이어 간다.
    fake = _Sheets(GRID, values_by_range={"'Sales'!5000:5000": _resp(400, _GRID_LIMIT)})
    result = await _call(fake, google_sheets_get_row, sheet_id="0", row_number=5000)
    assert result == {"success": True, "found": False, "total": 0, "items": [], "row_number": None, "values": {}}


@pytest.mark.asyncio
async def test_시트_밖_시작_행이면_여러_행도_0건():
    fake = _Sheets(GRID, values_by_range={"'Sales'!A5000:B5001": _resp(400, _GRID_LIMIT)})
    result = await _call(fake, google_sheets_get_rows, sheet_id="0", column_range="A:B", row_count=2, start_row=5000)
    assert (result["found"], result["total"], result["items"]) == (False, 0, [])


@pytest.mark.asyncio
async def test_그리드_한도가_아닌_400은_그대로_오류():
    fake = _Sheets(GRID, values_by_range={"'Sales'!4:4": _resp(400, {"error": {"message": "Unable to parse range"}})})
    result = await _call(fake, google_sheets_get_row, sheet_id="0", row_number=4)
    assert "Unable to parse range" in result["error"]

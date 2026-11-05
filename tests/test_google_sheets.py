import json
from unittest.mock import MagicMock, patch
from urllib.parse import unquote

import httpx
import pytest

from common.error_code import ToolErrorCode
from tools.google_sheets import (
    _Invalid, _col, _header_range, _resolve, _row, _row_number,
    google_sheets_append_top, google_sheets_find_or_create_row,
    google_sheets_lookup_row, google_sheets_update_row,
)


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

    def __init__(self, grid, sheets=(("Sales", 0, 26),), fail=None, updated_range=None, omit_sheet_id=False):
        self.grid = grid
        self.sheets = sheets
        self.fail = fail or {}
        self.updated_range = updated_range
        self.omit_sheet_id = omit_sheet_id
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
        if "/values/" not in url:
            props = []
            for title, sid, count in self.sheets:
                p = {"sheetId": sid, "title": title, "gridProperties": {"columnCount": count}}
                if self.omit_sheet_id and sid == 0:
                    del p["sheetId"]
                props.append({"properties": p})
            return _resp(200, {"sheets": props})
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
    "append_top": (google_sheets_append_top, {"columns": {"A": "x"}}),
    "update_row": (google_sheets_update_row, {"row_number": 2, "columns": {"A": "x"}}),
    "lookup_row": (google_sheets_lookup_row, {"lookup_column": "A", "lookup_value": "a"}),
    "find_or_create_row": (google_sheets_find_or_create_row,
                           {"lookup_column": "A", "lookup_value": "a", "columns": {"A": "x"}}),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["append_top", "update_row", "lookup_row", "find_or_create_row"])
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
    assert _header_range(["이름", " ", " "], 26) == ["이름", " ", " "]


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


@pytest.mark.asyncio
@pytest.mark.parametrize("call", [
    lambda o: google_sheets_append_top(access_token="t", spreadsheet_id="s1", sheet_id="0", columns={"A": "x"}, value_input_option=o),
    lambda o: google_sheets_update_row(access_token="t", spreadsheet_id="s1", sheet_id="0", row_number=2, columns={"A": "x"}, value_input_option=o),
    lambda o: google_sheets_find_or_create_row(access_token="t", spreadsheet_id="s1", sheet_id="0", lookup_column="A", lookup_value="a", columns={"A": "x"}, value_input_option=o)])
@pytest.mark.parametrize("option", ["FORMULA", "", None])
async def test_입력값_해석은_두_값만(call, option):
    with patch("tools.google_sheets.get_http_client") as factory:
        result = json.loads(await call(option))
    factory.assert_not_called()
    assert ToolErrorCode.EXECUTION_FAILED.message in result["error"]


# --- C4 행 수정 ---
@pytest.mark.asyncio
async def test_행_수정은_안_채운_열을_None으로_둔다():
    fake = _Sheets(GRID)
    result = await _call(fake, google_sheets_update_row, sheet_id="0", row_number="5", columns={"B": "x", "D": ""})
    _, put_url, put_kwargs = fake.of("PUT")[0]
    assert _range(put_url) == "'Sales'!A5"
    assert put_kwargs["json"]["values"] == [[None, "x"]]
    assert result == {"success": True, "updatedRange": "'Sales'!A5", "row_number": 5}


@pytest.mark.asyncio
async def test_모든_열_덮어쓰기는_머리글_끝까지_빈_문자열():
    fake = _Sheets(GRID)
    await _call(fake, google_sheets_update_row, sheet_id="0", row_number=5, columns={"B": "x"}, overwrite_all=True)
    assert fake.of("PUT")[0][2]["json"]["values"] == [["", "x", "", ""]]


@pytest.mark.asyncio
@pytest.mark.parametrize("row", ["0", 0, -1, "abc", 1.5, "", None, True])
async def test_행_번호가_잘못되면_Google을_부르지_않는다(row):
    await _no_google(google_sheets_update_row, spreadsheet_id="s1", sheet_id="0", row_number=row, columns={"A": "x"})


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


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [google_sheets_lookup_row, google_sheets_find_or_create_row])
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


# --- S11 ---
@pytest.mark.asyncio
async def test_찾으면_만들지_않는다():
    fake = _Sheets(GRID)
    result = await _call(fake, google_sheets_find_or_create_row, sheet_id="0", lookup_column="A",
                         lookup_value="a", columns={"A": "a"})
    assert result == {"success": True, "created": False, "row_number": 2,
                      "values": {"A": "a", "B": "x@x", "C": "", "D": "1"}}
    assert not fake.of("POST")


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


@pytest.mark.asyncio
@pytest.mark.parametrize("columns", [None, {}])
async def test_없는데_넣을_값이_없으면_실패한다(columns):
    fake = _Sheets(GRID)
    result = await _call(fake, google_sheets_find_or_create_row, sheet_id="0", lookup_column="A",
                         lookup_value="c", columns=columns)
    assert "넣을 값이 없어요" in result["error"]
    assert not fake.of("POST")
    assert [c for c in fake.calls if "/values/" in c[1]]  # 찾기는 했다

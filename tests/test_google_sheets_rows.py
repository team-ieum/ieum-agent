import json
from unittest.mock import MagicMock, patch
from urllib.parse import unquote

import pytest

from tests.action_outputs import assert_matches_outputs
from tools.google_sheets import _Invalid
from tools.google_sheets_rows import (_parse_row_numbers, google_sheets_append_rows, google_sheets_clear_rows,
                                      google_sheets_delete_rows, google_sheets_update_rows)

pytestmark = pytest.mark.asyncio


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


class _Fake:
    """레인 R 전용 가짜 Sheets API. calls = [(method, url, kwargs)].
    fail: {"append"|"values_batch"|"clear"|"batch": MagicMock 응답}."""

    def __init__(self, grid, sheets=(("Sales", 0, 26),), fail=None):
        self.grid = grid
        self.sheets = sheets
        self.fail = fail or {}
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if "/values/" not in url:
            return _resp(200, {"sheets": [{"properties": {"sheetId": i, "title": t,
                                                          "gridProperties": {"columnCount": c}}}
                                          for t, i, c in self.sheets]})
        assert _range(url).endswith("!1:1"), url
        return _resp(200, {"values": self.grid[:1]})

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        kind = self._kind(url)
        if kind in self.fail:
            return self.fail[kind]
        body = kwargs.get("json") or {}
        if kind == "append":
            n, values = len(self.grid) + 1, body["values"]
            return _resp(200, {"updates": {"updatedRange": f"'Sales'!A{n}:D{n + len(values) - 1}",
                                           "updatedRows": len(values)}})
        if kind == "values_batch":
            return _resp(200, {"totalUpdatedRows": len(body["data"])})
        if kind == "clear":
            return _resp(200, {"clearedRanges": body["ranges"]})
        return _resp(200, {"replies": [{}] * len(body["requests"])})

    @staticmethod
    def _kind(url):
        if url.endswith(":append"):
            return "append"
        if url.endswith("/values:batchUpdate"):
            return "values_batch"
        if url.endswith("/values:batchClear"):
            return "clear"
        return "batch"

    def of(self, kind):
        return [c for c in self.calls if c[0] == "POST" and self._kind(c[1]) == kind]


GRID = [["이름", "이메일", "", "등급"], ["a", "x@x", "", "1"], ["b", "y@y"], ["a", "z@z", "", "2"]]
BASE = {"access_token": "t", "spreadsheet_id": "s1", "sheet_id": "0"}


async def _run(tool, fake=None, **kwargs):
    fake = fake or _Fake(GRID)
    with patch("tools.google_sheets_rows.get_http_client", return_value=fake):
        return fake, json.loads(await tool(**{**BASE, **kwargs}))


async def _no_google(tool, **kwargs):
    with patch("tools.google_sheets_rows.get_http_client") as factory:
        result = json.loads(await tool(**{**BASE, **kwargs}))
    assert "error" in result, result
    factory.assert_not_called()
    return result["error"]


async def _appended(**kwargs):
    fake, result = await _run(google_sheets_append_rows, **kwargs)
    assert "error" not in result, result
    (_, _, kw), = fake.of("append")
    return kw["json"]["values"]


# --- C2 append_rows ---

async def test_여러_행_추가는_append_한_번():
    fake, result = await _run(google_sheets_append_rows,
                              rows=json.dumps([{"A": "p", "B": "p@p"}, {"이름": "q", "등급": "3"}]))
    (_, url, kw), = fake.of("append")
    assert _range(url) == "'Sales'!A1"
    assert kw["params"] == {"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"}
    assert kw["json"]["values"] == [["p", "p@p"], ["q", "", "", "3"]]
    assert result == {"success": True, "updatedRange": "'Sales'!A5:D6", "row_number": 5, "row_count": 2}
    assert_matches_outputs("google_sheets.append_rows", result)


async def test_코드_펜스와_설명이_붙은_AI_출력():
    assert await _appended(rows="결과:\n```json\n[{\"A\":\"p\"}]\n```") == [["p"]]


async def test_찾기_결과_items를_그대로_받는다():
    rows = json.dumps([{"row_number": 2, "values": {"A": "a", "B": "x@x", "C": "", "D": "1"}}])
    assert await _appended(rows=rows) == [["a", "x@x", "", "1"]]


async def test_배열_행은_A부터():
    assert await _appended(rows='[["p","p@p"]]') == [["p", "p@p"]]


async def test_list_값도_받는다():
    assert await _appended(rows=[{"A": "p"}]) == [["p"]]


async def test_매핑이_있으면_매핑한_열만_쓴다():
    rows = json.dumps([{"name": "p", "mail": "p@p", "x": "무시"}])
    assert await _appended(rows=rows, column_map={"A": "name", "B": "mail", "D": ""}) == [["p", "p@p"]]


async def test_매핑은_점_경로와_찾기_items를_읽는다():
    rows = json.dumps([{"row_number": 2, "values": {"A": "a", "B": "x@x"}}])
    assert await _appended(rows=rows, column_map={"B": "values.A", "A": "values.B"}) == [["x@x", "a"]]


async def test_매핑에_없는_키는_빈_칸():
    rows = json.dumps([{"name": "p"}, {"name": "q", "mail": "q@q"}])
    assert await _appended(rows=rows, column_map={"A": "name", "B": "mail"}) == [["p"], ["q", "q@q"]]


@pytest.mark.parametrize("column_map", [{}, {"A": ""}, None])
async def test_매핑이_비면_자동_해석(column_map):
    assert await _appended(rows='[{"A":"p"}]', column_map=column_map) == [["p"]]


async def test_빈_목록은_0행_성공():
    fake, result = await _run(google_sheets_append_rows, rows="[]")
    assert result == {"success": True, "updatedRange": None, "row_number": None, "row_count": 0}
    assert fake.calls == []


async def test_한_행이_틀리면_쓰지_않는다():
    fake, result = await _run(google_sheets_append_rows, rows=json.dumps([{"A": "p"}, {"없는열": "q"}]))
    assert "2번째 행:" in result["error"] and "열 '없는열'" in result["error"]
    assert fake.of("append") == []
    fake, result = await _run(google_sheets_append_rows, rows=json.dumps([{"A": "p"}, {"A": ""}]))
    assert "2번째 행:" in result["error"] and "넣을 값이 없어요" in result["error"]
    assert fake.of("append") == []


@pytest.mark.parametrize("rows", ["", "  ", None, "{}", "abc", "[1]", '[{"A":{"x":1}}]'])
async def test_행_목록_거절은_Google을_부르지_않는다(rows):
    error = await _no_google(google_sheets_append_rows, rows=rows)
    if rows in ("[1]", '[{"A":{"x":1}}]'):
        assert "1번째 행:" in error


async def test_행_상한():
    await _no_google(google_sheets_append_rows, rows=json.dumps([{"A": "p"}] * 1001))
    assert len(await _appended(rows=json.dumps([{"A": "p"}] * 1000))) == 1000


@pytest.mark.parametrize("column_map", ["A", {"A": 5}])
async def test_매핑_거절(column_map):
    await _no_google(google_sheets_append_rows, rows='[{"A":"p"}]', column_map=column_map)


@pytest.mark.parametrize("kwargs", [{"spreadsheet_id": ""}, {"spreadsheet_id": "a/b"}, {"sheet_id": ""},
                                    {"value_input_option": "X"}])
async def test_빈_대상은_Google을_부르지_않는다(kwargs):
    await _no_google(google_sheets_append_rows, rows='[{"A":"p"}]', **kwargs)


async def test_시트_제목도_받는다():
    fake, _ = await _run(google_sheets_append_rows, rows='[{"A":"p"}]', sheet_id="Sales")
    (_, url, _), = fake.of("append")
    assert _range(url) == "'Sales'!A1"


# --- C5 update_rows ---

async def _updated(**kwargs):
    fake, result = await _run(google_sheets_update_rows, **kwargs)
    assert "error" not in result, result
    (_, _, kw), = fake.of("values_batch")
    return [d["values"] for d in kw["json"]["data"]]


async def test_여러_행_수정은_values_batchUpdate_한_번():
    fake, result = await _run(google_sheets_update_rows,
                              rows=json.dumps([{"row_number": 4, "B": "n@n"}, {"row_number": 2, "values": {"D": "9"}}]))
    (_, url, kw), = fake.of("values_batch")
    assert url.endswith("/spreadsheets/s1/values:batchUpdate")
    assert kw["json"] == {"valueInputOption": "USER_ENTERED", "data": [
        {"range": "'Sales'!A4", "majorDimension": "ROWS", "values": [[None, "n@n"]]},
        {"range": "'Sales'!A2", "majorDimension": "ROWS", "values": [[None, None, None, "9"]]},
    ]}
    assert result == {"success": True, "row_count": 2, "row_numbers": [2, 4]}
    assert_matches_outputs("google_sheets.update_rows", result)


@pytest.mark.parametrize("overwrite_all", [True, "true"])
async def test_모든_열_덮어쓰기(overwrite_all):
    values = await _updated(rows='[{"row_number":3,"A":"q"}]', overwrite_all=overwrite_all)
    assert values == [[["q", "", "", ""]]]


async def test_매핑으로_수정():
    rows = json.dumps([{"row_number": 3, "mail": "m@m", "x": "무시"}])
    assert await _updated(rows=rows, column_map={"B": "mail"}) == [[[None, "m@m"]]]


async def test_같은_행이_두_번이면_거절():
    fake, result = await _run(google_sheets_update_rows,
                              rows=json.dumps([{"row_number": 3, "A": "q"}, {"row_number": "3", "B": "r"}]))
    assert "2번째 행:" in result["error"]
    assert fake.of("values_batch") == []


@pytest.mark.parametrize("item", [{"A": "q"}, {"row_number": 0, "A": "q"}, {"row_number": "", "A": "q"},
                                  {"row_number": True, "A": "q"}, ["q"]])
async def test_행_번호가_없거나_틀리면_거절(item):
    error = await _no_google(google_sheets_update_rows, rows=json.dumps([item]))
    assert "1번째 행:" in error


async def test_수정_한_행이_틀리면_쓰지_않는다():
    fake, result = await _run(google_sheets_update_rows,
                              rows=json.dumps([{"row_number": 2, "A": "q"}, {"row_number": 3, "없는열": "r"}]))
    assert "error" in result
    assert fake.of("values_batch") == []


async def test_수정_빈_목록은_0행_성공():
    fake, result = await _run(google_sheets_update_rows, rows="[]")
    assert result == {"success": True, "row_count": 0, "row_numbers": []}
    assert fake.calls == []


async def test_수정_상한():
    await _no_google(google_sheets_update_rows,
                     rows=json.dumps([{"row_number": n, "A": "q"} for n in range(2, 503)]))
    assert len(await _updated(rows=json.dumps([{"row_number": n, "A": "q"} for n in range(2, 502)]))) == 500


# --- 행 번호 파서, C6 clear_rows, C7 delete_rows ---

@pytest.mark.parametrize("value, spans", [
    ("5", [(5, 5)]), ("5,7", [(5, 5), (7, 7)]), (" 5 - 9 ", [(5, 9)]), ("5,6,7-9", [(5, 9)]),
    ("9,5,5", [(5, 5), (9, 9)]), ("1", [(1, 1)]), ('[{"row_number":4},{"row_number":2}]', [(2, 2), (4, 4)]),
    ('[3,"5-6"]', [(3, 3), (5, 6)]), ([2, "3"], [(2, 3)]), ("[]", []), ("1-1000", [(1, 1000)]),
])
async def test_행_번호_파서(value, spans):
    assert _parse_row_numbers(value) == spans


@pytest.mark.parametrize("value", ["", "  ", None, "9-5", "0", "-1", "5,,7", "２", "a", "5-", "[true]",
                                   '[{"x":1}]', "[0]", "1-1001", "2-99999999"])
async def test_행_번호_파서_거절(value):
    with pytest.raises(_Invalid):
        _parse_row_numbers(value)


async def test_큰_범위는_펼치지_않는다():
    with patch("tools.google_sheets_rows._expand") as expand:
        error = await _no_google(google_sheets_clear_rows, row_numbers="2-99999999")
    assert "1000개까지" in error
    expand.assert_not_called()


async def test_행_비우기는_batchClear_한_번():
    fake, result = await _run(google_sheets_clear_rows, row_numbers="5,7-9")
    (_, url, kw), = fake.of("clear")
    assert url.endswith("/spreadsheets/s1/values:batchClear")
    assert kw["json"] == {"ranges": ["'Sales'!5:5", "'Sales'!7:9"]}
    assert result == {"success": True, "row_count": 4, "row_numbers": [5, 7, 8, 9]}
    assert_matches_outputs("google_sheets.clear_rows", result)


def _deletes(fake):
    (_, url, kw), = fake.of("batch")
    assert url.endswith("/spreadsheets/s1:batchUpdate")
    return kw["json"]["requests"]


async def test_행_삭제는_아래부터():
    fake, result = await _run(google_sheets_delete_rows, row_numbers="5,7-9", confirm_delete=True)
    assert _deletes(fake) == [
        {"deleteDimension": {"range": {"sheetId": 0, "dimension": "ROWS", "startIndex": 6, "endIndex": 9}}},
        {"deleteDimension": {"range": {"sheetId": 0, "dimension": "ROWS", "startIndex": 4, "endIndex": 5}}},
    ]
    assert result == {"success": True, "row_count": 4, "row_numbers": [5, 7, 8, 9]}
    assert_matches_outputs("google_sheets.delete_rows", result)


async def test_찾기_결과로_삭제():
    rows = json.dumps([{"row_number": 2, "values": {}}, {"row_number": 4, "values": {}}])
    fake, _ = await _run(google_sheets_delete_rows, row_numbers=rows, confirm_delete=True)
    assert [r["deleteDimension"]["range"]["startIndex"] for r in _deletes(fake)] == [3, 1]


@pytest.mark.parametrize("confirm_delete", [False, None, "", "false", 0])
async def test_삭제_확인이_없으면_Google을_부르지_않는다(confirm_delete):
    error = await _no_google(google_sheets_delete_rows, row_numbers="5", confirm_delete=confirm_delete)
    assert "삭제 확인" in error


async def test_삭제_확인_문자열_참():
    fake, result = await _run(google_sheets_delete_rows, row_numbers="5", confirm_delete="true")
    assert result["row_numbers"] == [5] and len(fake.of("batch")) == 1


async def test_시트_id로_삭제():
    fake, _ = await _run(google_sheets_delete_rows, _Fake(GRID, sheets=(("Sales", 0, 26), ("B", 5, 26))),
                         sheet_id="5", row_numbers="2", confirm_delete=True)
    assert _deletes(fake)[0]["deleteDimension"]["range"]["sheetId"] == 5


@pytest.mark.parametrize("kwargs, word", [({"spreadsheet_id": ""}, "spreadsheet_id"), ({"sheet_id": ""}, "sheet_id"),
                                          ({"row_numbers": ""}, "행 번호")])
async def test_비우기_삭제_빈_대상(kwargs, word):
    args = {"row_numbers": "5", **kwargs}
    assert word in await _no_google(google_sheets_clear_rows, **args)
    error = await _no_google(google_sheets_delete_rows, confirm_delete=True, **args)
    assert word in error and "삭제 확인" not in error


async def test_빈_배열은_0행_성공():
    for tool, extra in ((google_sheets_clear_rows, {}), (google_sheets_delete_rows, {"confirm_delete": True})):
        fake, result = await _run(tool, row_numbers="[]", **extra)
        assert result == {"success": True, "row_count": 0, "row_numbers": []}
        assert fake.calls == []


async def test_구글_오류는_그대로():
    message = "Sorry, it is not possible to delete all non-frozen rows."
    fake = _Fake(GRID, fail={"batch": _resp(400, {"error": {"message": message}})})
    _, result = await _run(google_sheets_delete_rows, fake, row_numbers="2-4", confirm_delete=True)
    assert message in result["error"]

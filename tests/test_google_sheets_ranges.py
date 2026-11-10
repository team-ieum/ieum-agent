import json
from unittest.mock import patch

import pytest

from common.error_code import ToolErrorCode
from tests.action_outputs import assert_matches_outputs
from tests.test_google_sheets import GRID, _Sheets, _range, _resp
from tools.google_sheets import _Invalid
from tools.google_sheets_ranges import (
    _grid_range, google_sheets_add_column, google_sheets_copy_range, google_sheets_sort_range,
)


async def _call(fake, tool, **kwargs):
    with patch("tools.google_sheets_ranges.get_http_client", return_value=fake):
        return json.loads(await tool(access_token="t", spreadsheet_id="s1", **kwargs))


async def _no_google(tool, **kwargs):
    with patch("tools.google_sheets_ranges.get_http_client") as factory:
        result = json.loads(await tool(access_token="t", **kwargs))
    factory.assert_not_called()
    assert ToolErrorCode.EXECUTION_FAILED.message in result["error"]
    return result


def _requests(fake) -> list[dict]:  # 마지막 batchUpdate의 requests
    return fake.of("POST")[-1][2]["json"]["requests"]


# --- _grid_range ---

_A1_C10 = {"startRowIndex": 0, "endRowIndex": 10, "startColumnIndex": 0, "endColumnIndex": 3}
_B3 = {"startRowIndex": 2, "endRowIndex": 3, "startColumnIndex": 1, "endColumnIndex": 2}


@pytest.mark.parametrize("cell_range, expected", [
    ("B3", _B3),
    ("A1:C10", _A1_C10),
    ("A:C", {"startColumnIndex": 0, "endColumnIndex": 3}),
    ("A:A", {"startColumnIndex": 0, "endColumnIndex": 1}),
    ("2:10", {"startRowIndex": 1, "endRowIndex": 10}),
    ("1:1", {"startRowIndex": 0, "endRowIndex": 1}),
    ("A2:C", {"startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 3}),
    ("A1:B", {"startRowIndex": 0, "startColumnIndex": 0, "endColumnIndex": 2}),
    ("'시트 ''a!b'''!b3", _B3),
    ("Other!A1:C10", _A1_C10),
    ("$A$1:$C$10", _A1_C10),
    (" a1:c10 ", _A1_C10),
])
def test_A1_범위를_GridRange로(cell_range, expected):
    assert _grid_range(cell_range, 7) == {"sheetId": 7, **expected}


@pytest.mark.parametrize("cell_range", ["", "  ", None, "MyRange", "A", "3", "A:B10", "1:A", "A1:3", "C1:A3",
                                        "10:2", "0:3", "A0", "A1:B2:C3", "AAAA1", "A12345678"])
def test_A1_범위_거절(cell_range):
    with pytest.raises(_Invalid, match="A1:C10"):
        _grid_range(cell_range, 0)


# --- C10 copy_range ---

@pytest.mark.asyncio
async def test_범위_복사():
    fake = _Sheets(GRID, sheets=(("Sales", 7, 26),))
    result = await _call(fake, google_sheets_copy_range, sheet_id="7", source_range="A1:B2", destination_range="D1")
    assert len(fake.of("POST")) == 1
    assert _requests(fake) == [{"copyPaste": {
        "source": {"sheetId": 7, "startRowIndex": 0, "endRowIndex": 2, "startColumnIndex": 0, "endColumnIndex": 2},
        "destination": {"sheetId": 7, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 3,
                        "endColumnIndex": 4},
        "pasteType": "PASTE_NORMAL", "pasteOrientation": "NORMAL"}}]
    assert result == {"success": True, "source": "'Sales'!A1:B2", "destination": "'Sales'!D1"}
    assert_matches_outputs("google_sheets.copy_range", result)


@pytest.mark.asyncio
async def test_범위_복사_값만_행열_바꾸기():
    fake = _Sheets(GRID)
    await _call(fake, google_sheets_copy_range, sheet_id="0", source_range="A1:B2", destination_range="D1",
                paste_type="PASTE_VALUES", paste_orientation="TRANSPOSE")
    req = _requests(fake)[0]["copyPaste"]
    assert (req["pasteType"], req["pasteOrientation"]) == ("PASTE_VALUES", "TRANSPOSE")
    for blank in ("", None):
        fake = _Sheets(GRID)
        await _call(fake, google_sheets_copy_range, sheet_id="0", source_range="A1:B2", destination_range="D1",
                    paste_type=blank)
        assert _requests(fake)[0]["copyPaste"]["pasteType"] == "PASTE_NORMAL"


_COPY_OK = {"spreadsheet_id": "s1", "sheet_id": "0", "source_range": "A1:B2", "destination_range": "D1"}


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [
    {"source_range": ""}, {"source_range": "A:B10"}, {"destination_range": ""}, {"paste_type": "CUT"},
    {"paste_orientation": "X"}, {"spreadsheet_id": ""}, {"sheet_id": ""},
])
async def test_범위_복사_거절은_Google을_부르지_않는다(override):
    await _no_google(google_sheets_copy_range, **{**_COPY_OK, **override})


# --- C11 sort_range ---

@pytest.mark.asyncio
async def test_범위_비우면_머리글_빼고_전체_정렬():
    fake = _Sheets(GRID, sheets=(("Sales", 0, 6),))
    result = await _call(fake, google_sheets_sort_range, sheet_id="0", sort_column="D")
    assert _requests(fake) == [{"sortRange": {"range": {"sheetId": 0, "startRowIndex": 1},
                                              "sortSpecs": [{"dimensionIndex": 3, "sortOrder": "ASCENDING"}]}}]
    assert result["range"] == "'Sales'!A2:F"
    assert_matches_outputs("google_sheets.sort_range", result)


@pytest.mark.asyncio
async def test_정렬_기준_열은_시트_절대_인덱스():
    fake = _Sheets(GRID, sheets=(("Sales", 0, 6),))
    result = await _call(fake, google_sheets_sort_range, sheet_id="0", cell_range="C2:E9", sort_column="d",
                         sort_order="DESCENDING")
    req = _requests(fake)[0]["sortRange"]
    assert req["range"] == {"sheetId": 0, "startRowIndex": 1, "endRowIndex": 9, "startColumnIndex": 2,
                            "endColumnIndex": 5}
    assert req["sortSpecs"] == [{"dimensionIndex": 3, "sortOrder": "DESCENDING"}]
    assert result["range"] == "'Sales'!C2:E9"


@pytest.mark.asyncio
async def test_기준_열이_범위_밖이면_거절():
    result = await _no_google(google_sheets_sort_range, spreadsheet_id="s1", sheet_id="0", cell_range="A:C",
                              sort_column="E")
    assert "E" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [
    {"sort_column": ""}, {"sort_column": "이름"}, {"sort_column": "A1"}, {"sort_column": "AAAA"},
    {"sort_order": "UP"}, {"cell_range": "A:B10"},
])
async def test_정렬_거절(override):
    await _no_google(google_sheets_sort_range, **{"spreadsheet_id": "s1", "sheet_id": "0", "sort_column": "A",
                                                  **override})


@pytest.mark.asyncio
async def test_범위_행_전체면_기준_열_검사_안_함():
    fake = _Sheets(GRID)
    result = await _call(fake, google_sheets_sort_range, sheet_id="0", cell_range="2:10", sort_column="Z")
    assert result["success"] is True
    assert _requests(fake)[0]["sortRange"]["sortSpecs"][0]["dimensionIndex"] == 25


# --- C9 add_column ---

_E_COLUMN = {"sheetId": 0, "dimension": "COLUMNS", "startIndex": 4, "endIndex": 5}


@pytest.mark.asyncio
async def test_열_추가는_맨_끝에_넣고_머리글을_RAW로_쓴다():
    fake = _Sheets(GRID)
    result = await _call(fake, google_sheets_add_column, sheet_id="0", title="2026")
    assert [fake._kind(c) for c in fake.of("POST")] == ["insert"]
    assert _requests(fake) == [{"insertDimension": {"range": _E_COLUMN, "inheritFromBefore": True}}]
    [(_, url, kwargs)] = fake.of("PUT")
    assert _range(url) == "'Sales'!E1"
    assert kwargs["params"] == {"valueInputOption": "RAW"}
    assert kwargs["json"]["values"] == [["2026"]]
    assert result == {"success": True, "column": "E", "title": "2026"}
    assert_matches_outputs("google_sheets.add_column", result)


@pytest.mark.asyncio
async def test_머리글이_그리드를_채우면_appendDimension():
    fake = _Sheets(GRID, sheets=(("Sales", 0, 4),))
    await _call(fake, google_sheets_add_column, sheet_id="0", title="2026")
    assert _requests(fake) == [{"appendDimension": {"sheetId": 0, "dimension": "COLUMNS", "length": 1}}]
    assert _range(fake.of("PUT")[0][1]) == "'Sales'!E1"


@pytest.mark.asyncio
async def test_넣을_위치는_머리글_이름이나_열_문자():
    fake = _Sheets(GRID)
    result = await _call(fake, google_sheets_add_column, sheet_id="0", title="t", before_column="이메일")
    req = _requests(fake)[0]["insertDimension"]
    assert (req["range"]["startIndex"], req["inheritFromBefore"], result["column"]) == (1, True, "B")
    fake = _Sheets(GRID)
    await _call(fake, google_sheets_add_column, sheet_id="0", title="t", before_column="A")
    req = _requests(fake)[0]["insertDimension"]
    assert (req["range"]["startIndex"], req["inheritFromBefore"]) == (0, False)
    assert _range(fake.of("PUT")[0][1]) == "'Sales'!A1"


@pytest.mark.asyncio
async def test_머리글_없는_시트는_위치가_필요():
    fake = _Sheets([[]])
    result = await _call(fake, google_sheets_add_column, sheet_id="0", title="t", before_column=None)
    assert "머리글이 없는 시트" in result["error"]
    assert fake.of("POST") == []


@pytest.mark.asyncio
async def test_머리글_쓰기_실패면_넣은_열을_지운다():
    fake = _Sheets(GRID, fail={"update": _resp(400, {"error": {"message": "bad"}})})
    result = await _call(fake, google_sheets_add_column, sheet_id="0", title="t")
    assert "error" in result
    assert [fake._kind(c) for c in fake.of("POST")] == ["insert", "delete"]
    assert _requests(fake) == [{"deleteDimension": {"range": _E_COLUMN}}]


@pytest.mark.asyncio
async def test_보상_삭제도_실패하면_알린다():
    fake = _Sheets(GRID, fail={"update": _resp(400, {"error": {"message": "bad"}}), "delete": _resp(500, {})})
    result = await _call(fake, google_sheets_add_column, sheet_id="0", title="t")
    assert "빈 열이 남았을 수 있어요" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("title", ["", "  ", None, "x" * 50_001])
async def test_열_제목_거절(title):
    await _no_google(google_sheets_add_column, spreadsheet_id="s1", sheet_id="0", title=title)


@pytest.mark.asyncio
async def test_없는_머리글_이름_위치는_거절():
    fake = _Sheets(GRID)
    result = await _call(fake, google_sheets_add_column, sheet_id="0", title="t", before_column="없는열")
    assert "error" in result
    assert fake.of("POST") == []

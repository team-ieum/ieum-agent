import json
from unittest.mock import patch

import pytest

from common.error_code import ToolErrorCode
from core import template_registry as tr
from tests.action_outputs import assert_matches_outputs
from tests.test_google_sheets import GRID, _Sheets, _range, _resp
from tools import _TOOL_MAP
from tools.google_sheets import _Invalid, _cell_format
from tools.google_sheets_ranges import (
    _CF_CONDITIONS, _DV_CONDITIONS, _H_ALIGNS, _NUMBER_FORMATS, _PASTE_ORIENTATIONS, _PASTE_TYPES, _RELATIVE_DATES,
    _SORT_ORDERS, _V_ALIGNS, _WRAPS, _grid_range, google_sheets_add_column, google_sheets_add_conditional_format,
    google_sheets_copy_range, google_sheets_format_range, google_sheets_set_data_validation,
    google_sheets_sort_range,
)
from tools.registry import _ACTION_ONLY_TOOLS, resolve_action_fn


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


# --- C12 format_range ---

@pytest.mark.asyncio
async def test_서식_마스크는_채운_칸만():
    fake = _Sheets(GRID)
    result = await _call(fake, google_sheets_format_range, sheet_id="0", cell_range="A1:B2", number_format="DATE",
                         number_pattern="yyyy-mm-dd", background_color="#FFF2CC", horizontal_alignment="CENTER")
    assert len(fake.of("POST")) == 1
    fmt0, mask0 = _cell_format(background_color="#FFF2CC")
    [req] = _requests(fake)
    assert req["repeatCell"]["range"] == {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 2, "startColumnIndex": 0,
                                          "endColumnIndex": 2}
    assert req["repeatCell"]["cell"]["userEnteredFormat"] == {
        **fmt0, "numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}, "horizontalAlignment": "CENTER"}
    fields = req["repeatCell"]["fields"].split(",")
    assert set(fields) == set(mask0) | {"userEnteredFormat.numberFormat", "userEnteredFormat.horizontalAlignment"}
    assert "userEnteredFormat" not in fields
    assert result["range"] == "'Sales'!A1:B2"
    assert_matches_outputs("google_sheets.format_range", result)


@pytest.mark.asyncio
@pytest.mark.parametrize("bold", [False, "false"])
async def test_굵게_끄기도_마스크에_들어간다(bold):
    fake = _Sheets(GRID)
    await _call(fake, google_sheets_format_range, sheet_id="0", cell_range="A1", bold=bold)
    fields = _requests(fake)[0]["repeatCell"]["fields"]
    assert fields == ",".join(_cell_format(bold=False)[1])
    assert "userEnteredFormat.textFormat.bold" in fields


@pytest.mark.asyncio
async def test_정렬과_줄바꿈():
    fake = _Sheets(GRID)
    await _call(fake, google_sheets_format_range, sheet_id="0", cell_range="A1", vertical_alignment="MIDDLE",
                wrap_strategy="WRAP")
    assert set(_requests(fake)[0]["repeatCell"]["fields"].split(",")) == {
        "userEnteredFormat.verticalAlignment", "userEnteredFormat.wrapStrategy"}


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [
    {}, {"number_pattern": "#,##0"}, {"number_format": "MONEY"}, {"number_format": "NUMBER", "number_pattern": "0" * 201},
    {"background_color": "FFF2CC"}, {"text_color": "#GGGGGG"}, {"bold": "maybe"},
    {"horizontal_alignment": "JUSTIFY"}, {"wrap_strategy": "LEGACY_WRAP"}, {"cell_range": "", "bold": True},
])
async def test_서식_거절은_Google을_부르지_않는다(override):
    await _no_google(google_sheets_format_range, **{"spreadsheet_id": "s1", "sheet_id": "0", "cell_range": "A1",
                                                    **override})


# --- 선택지·등록 ---

@pytest.mark.parametrize("event, key, const", [
    ("google_sheets.add_conditional_format", "condition", _CF_CONDITIONS),
    ("google_sheets.set_data_validation", "condition", _DV_CONDITIONS),
    ("google_sheets.add_conditional_format", "relative_date", _RELATIVE_DATES),
    ("google_sheets.copy_range", "paste_type", _PASTE_TYPES),
    ("google_sheets.copy_range", "paste_orientation", _PASTE_ORIENTATIONS),
    ("google_sheets.sort_range", "sort_order", _SORT_ORDERS),
    ("google_sheets.format_range", "number_format", _NUMBER_FORMATS),
    ("google_sheets.format_range", "horizontal_alignment", _H_ALIGNS),
    ("google_sheets.format_range", "vertical_alignment", _V_ALIGNS),
    ("google_sheets.format_range", "wrap_strategy", _WRAPS),
])
def test_카탈로그_선택지는_코드_상수와_같다(event, key, const):
    field = next(f for f in tr.get_template(event)["inputFields"] if f["key"] == key)
    assert [c["id"] for c in field["choices"]] == list(const)


def test_열·범위_도구는_ACTION_전용():
    for name in ("add_column", "copy_range", "sort_range", "format_range", "add_conditional_format",
                 "set_data_validation"):
        key = f"builtin:google_sheets_{name}"
        assert key not in _TOOL_MAP and key in _ACTION_ONLY_TOOLS and resolve_action_fn(key) is not None, key


# --- C13 add_conditional_format ---

def _condition(fake) -> dict:
    return _requests(fake)[0]["addConditionalFormatRule"]["rule"]["booleanRule"]["condition"]


_CF_OK = {"spreadsheet_id": "s1", "sheet_id": "0", "cell_range": "A1:A9", "bold": True}


@pytest.mark.asyncio
async def test_조건부_서식_숫자_사이():
    fake = _Sheets(GRID)
    result = await _call(fake, google_sheets_add_conditional_format, sheet_id="0", cell_range="B2:B",
                         condition="NUMBER_BETWEEN", value_1="1", value_2="9", bold=True)
    assert _requests(fake) == [{"addConditionalFormatRule": {"rule": {
        "ranges": [{"sheetId": 0, "startRowIndex": 1, "startColumnIndex": 1, "endColumnIndex": 2}],
        "booleanRule": {"condition": {"type": "NUMBER_BETWEEN",
                                      "values": [{"userEnteredValue": "1"}, {"userEnteredValue": "9"}]},
                        "format": _cell_format(bold=True)[0]}}, "index": 0}}]
    assert result["range"] == "'Sales'!B2:B"
    assert_matches_outputs("google_sheets.add_conditional_format", result)


@pytest.mark.asyncio
@pytest.mark.parametrize("condition, values, count", [
    ("NUMBER_GREATER", {}, 1), ("NUMBER_BETWEEN", {"value_1": "1"}, 2), ("TEXT_CONTAINS", {"value_1": "  "}, 1),
    ("DATE_BEFORE", {}, 1), ("CUSTOM_FORMULA", {"value_1": "A1>3"}, None),
])
async def test_조건부_서식_필요한_값이_비면_거절(condition, values, count):
    result = await _no_google(google_sheets_add_conditional_format, **_CF_OK, condition=condition, **values)
    if count:
        assert f"값 {count}개가 필요해요" in result["error"]


@pytest.mark.asyncio
async def test_남은_값은_무시():
    fake = _Sheets(GRID)
    await _call(fake, google_sheets_add_conditional_format, sheet_id="0", cell_range="A1", condition="NUMBER_GREATER",
                value_1="5", value_2="9", bold=True)
    assert _condition(fake)["values"] == [{"userEnteredValue": "5"}]
    fake = _Sheets(GRID)
    await _call(fake, google_sheets_add_conditional_format, sheet_id="0", cell_range="A1", condition="BLANK",
                value_1="x", bold=True)
    assert _condition(fake) == {"type": "BLANK"}


@pytest.mark.asyncio
async def test_상대_날짜():
    fake = _Sheets(GRID)
    await _call(fake, google_sheets_add_conditional_format, sheet_id="0", cell_range="A1", condition="DATE_AFTER",
                relative_date="PAST_WEEK", value_1="2026-01-01", bold=True)
    assert _condition(fake)["values"] == [{"relativeDate": "PAST_WEEK"}]
    fake = _Sheets(GRID)
    await _call(fake, google_sheets_add_conditional_format, sheet_id="0", cell_range="A1", condition="TEXT_EQ",
                value_1="a", relative_date="TODAY", bold=True)
    assert _condition(fake)["values"] == [{"userEnteredValue": "a"}]
    await _no_google(google_sheets_add_conditional_format, **_CF_OK, condition="DATE_EQ", relative_date="LAST_DECADE")


@pytest.mark.asyncio
async def test_맞춤_수식():
    fake = _Sheets(GRID)
    await _call(fake, google_sheets_add_conditional_format, sheet_id="0", cell_range="A1",
                condition="CUSTOM_FORMULA", value_1=" =$A2>3", bold=True)
    assert _condition(fake)["values"] == [{"userEnteredValue": "=$A2>3"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [
    {"condition": "NOT_BLANK", "bold": None}, {"condition": ""}, {"condition": "TEXT_IS_EMAIL"}, {"condition": "X"},
    {"condition": "NOT_BLANK", "cell_range": ""},
])
async def test_조건부_서식_거절(override):
    await _no_google(google_sheets_add_conditional_format, **{**_CF_OK, **override})


# --- C14 set_data_validation ---

def _dv(fake) -> dict:
    return _requests(fake)[0]["setDataValidation"]["rule"]


_DV_OK = {"spreadsheet_id": "s1", "sheet_id": "0", "cell_range": "C2:C"}


@pytest.mark.asyncio
async def test_유효성_목록():
    fake = _Sheets(GRID)
    result = await _call(fake, google_sheets_set_data_validation, sheet_id="0", cell_range="C2:C",
                         condition="ONE_OF_LIST", values=["예", "아니오"], input_message="골라 주세요")
    assert _requests(fake) == [{"setDataValidation": {
        "range": {"sheetId": 0, "startRowIndex": 1, "startColumnIndex": 2, "endColumnIndex": 3},
        "rule": {"condition": {"type": "ONE_OF_LIST",
                               "values": [{"userEnteredValue": "예"}, {"userEnteredValue": "아니오"}]},
                 "inputMessage": "골라 주세요", "strict": False, "showCustomUi": True}}}]
    assert_matches_outputs("google_sheets.set_data_validation", result)


@pytest.mark.asyncio
@pytest.mark.parametrize("values", [None, [], ["a", ""], ["=A1"], [1], ["x"] * 501])
async def test_유효성_목록_거절(values):
    await _no_google(google_sheets_set_data_validation, **_DV_OK, condition="ONE_OF_LIST", values=values)


@pytest.mark.asyncio
async def test_유효성_목록_500개는_된다():
    fake = _Sheets(GRID)
    result = await _call(fake, google_sheets_set_data_validation, sheet_id="0", cell_range="C2:C",
                         condition="ONE_OF_LIST", values=["x"] * 500)
    assert result["success"] is True


@pytest.mark.asyncio
async def test_유효성_범위_조건은_등호를_붙인다():
    for value, expected in (("'목록'!A2:A", "='목록'!A2:A"), ("=Sheet2!A:A", "=Sheet2!A:A")):
        fake = _Sheets(GRID)
        await _call(fake, google_sheets_set_data_validation, sheet_id="0", cell_range="C2:C",
                    condition="ONE_OF_RANGE", value_1=value)
        assert _dv(fake)["condition"]["values"] == [{"userEnteredValue": expected}]


@pytest.mark.asyncio
async def test_유효성_체크박스():
    fake = _Sheets(GRID)
    await _call(fake, google_sheets_set_data_validation, sheet_id="0", cell_range="C2:C", condition="BOOLEAN")
    assert _dv(fake)["condition"] == {"type": "BOOLEAN"}
    fake = _Sheets(GRID)
    await _call(fake, google_sheets_set_data_validation, sheet_id="0", cell_range="C2:C", condition="BOOLEAN",
                value_1="Y", value_2="N")
    assert _dv(fake)["condition"]["values"] == [{"userEnteredValue": "Y"}, {"userEnteredValue": "N"}]
    await _no_google(google_sheets_set_data_validation, **_DV_OK, condition="BOOLEAN", value_2="N")


@pytest.mark.asyncio
async def test_유효성_값_개수():
    result = await _no_google(google_sheets_set_data_validation, **_DV_OK, condition="DATE_BETWEEN",
                              value_1="2026-01-01")
    assert "값 2개가 필요해요" in result["error"]
    fake = _Sheets(GRID)
    await _call(fake, google_sheets_set_data_validation, sheet_id="0", cell_range="C2:C", condition="TEXT_IS_URL",
                value_1="x", strict=True, show_dropdown=False)
    rule = _dv(fake)
    assert rule["condition"] == {"type": "TEXT_IS_URL"}
    assert (rule["strict"], rule["showCustomUi"]) == (True, False)
    fake = _Sheets(GRID)
    await _call(fake, google_sheets_set_data_validation, sheet_id="0", cell_range="C2:C", condition="TEXT_IS_URL",
                strict="false")
    assert _dv(fake)["strict"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [{"condition": ""}, {"condition": "BLANK"},
                                      {"condition": "TEXT_IS_URL", "cell_range": "A:B10"}])
async def test_유효성_거절(override):
    await _no_google(google_sheets_set_data_validation, **{**_DV_OK, **override})

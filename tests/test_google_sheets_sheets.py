import json
from unittest.mock import patch

import pytest

from common.error_code import ToolErrorCode
from tests.action_outputs import assert_matches_outputs
from tests.test_google_sheets import _resp
from tools.google_sheets import _DRIVE_FILES_URL, _SHEET_PROPS
from tools.google_sheets_sheets import (
    google_sheets_copy_worksheet, google_sheets_create_spreadsheet, google_sheets_create_worksheet,
    google_sheets_delete_worksheet, google_sheets_find_or_create_spreadsheet, google_sheets_find_or_create_worksheet,
    google_sheets_rename_worksheet, google_sheets_update_worksheet,
)


class _Fake:
    """레인 S 전용 가짜 Google 클라이언트. sheets = [(title, sheetId, index)], calls = [(method, url, kwargs)].
    fail: {kind: MagicMock 응답 또는 던질 예외}."""

    def __init__(self, sheets=(("Sales", 0, 0),), fail=None, drive_files=(), drive_next_page_token=None):
        self.sheets = sheets
        self.fail = fail or {}
        self.drive_files = list(drive_files)
        self.drive_next_page_token = drive_next_page_token
        self.calls = []

    def _answer(self, kind, ok):
        failure = self.fail.get(kind)
        if isinstance(failure, BaseException):
            raise failure
        return failure if failure is not None else _resp(200, ok)

    @staticmethod
    def _kind(method, url, kwargs):
        if url.startswith(_DRIVE_FILES_URL):
            if method == "GET":
                return "drive_list"
            if method == "PATCH":
                return "drive_patch"
            return "drive_copy" if url.endswith("/copy") else "drive_create"
        if method == "PUT":
            return "put"
        if url.endswith(":batchUpdate"):
            return next(iter(kwargs["json"]["requests"][0]))
        if url.endswith(":copyTo"):
            return "copyTo"
        return "get_sheets"

    def of(self, kind):
        return [c for c in self.calls if self._kind(*c) == kind]

    async def _call(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        kind = self._kind(method, url, kwargs)
        body = kwargs.get("json") or {}
        if kind == "get_sheets":
            ok = {"sheets": [{"properties": {"sheetId": i, "title": t, "index": x, "sheetType": "GRID",
                                             "gridProperties": {"rowCount": 1000, "columnCount": 26}}}
                             for t, i, x in self.sheets]}
        elif kind == "addSheet":
            ok = {"replies": [{"addSheet": {"properties": {
                "sheetId": 101, "title": body["requests"][0]["addSheet"]["properties"]["title"]}}}]}
        elif kind == "duplicateSheet":
            name = body["requests"][0]["duplicateSheet"].get("newSheetName", "Sales의 사본")
            ok = {"replies": [{"duplicateSheet": {"properties": {"sheetId": 102, "title": name}}}]}
        elif kind == "copyTo":
            ok = {"sheetId": 103, "title": "Copy of Sales"}
        elif kind == "put":
            ok = {"updatedRange": body.get("range")}
        elif kind in ("drive_create", "drive_copy"):
            fid = "newfile1" if kind == "drive_create" else "copyfile1"
            ok = {"id": fid, "name": body.get("name"), "webViewLink": f"https://docs.google.com/spreadsheets/d/{fid}/edit"}
        elif kind == "drive_list":
            ok = {"files": self.drive_files}
            if self.drive_next_page_token:
                ok["nextPageToken"] = self.drive_next_page_token
        elif kind == "drive_patch":
            ok = {}
        else:
            ok = {"replies": [{}]}
        return self._answer(kind, ok)

    async def get(self, url, **kwargs):
        return await self._call("GET", url, kwargs)

    async def post(self, url, **kwargs):
        return await self._call("POST", url, kwargs)

    async def put(self, url, **kwargs):
        return await self._call("PUT", url, kwargs)

    async def patch(self, url, **kwargs):
        return await self._call("PATCH", url, kwargs)


def _err(message):
    return _resp(400, {"error": {"message": message}})


async def _run(fake, tool, **kwargs):
    with patch("tools.google_sheets_sheets.get_http_client", return_value=fake):
        return json.loads(await tool(access_token="t", **kwargs))


async def _no_google(tool, **kwargs):
    with patch("tools.google_sheets_sheets.get_http_client") as factory:
        result = json.loads(await tool(access_token="t", **kwargs))
    factory.assert_not_called()
    assert ToolErrorCode.EXECUTION_FAILED.message in result["error"]
    return result


def _req(call):
    return call[2]["json"]["requests"][0]


# --- C15 create_worksheet ---

@pytest.mark.asyncio
async def test_워크시트_만들기():
    fake = _Fake()
    result = await _run(fake, google_sheets_create_worksheet, spreadsheet_id="abc", title="요약",
                        headers="이름, 이메일 ,날짜")
    [add] = fake.of("addSheet")
    assert _req(add) == {"addSheet": {"properties": {"title": "요약", "gridProperties": {"columnCount": 26}}}}
    [put] = fake.of("put")
    assert put[2]["params"] == {"valueInputOption": "RAW"}
    assert put[2]["json"]["values"] == [["이름", "이메일", "날짜"]]
    assert put[2]["json"]["range"] == "'요약'!A1"
    assert result == {"success": True, "spreadsheet_id": "abc", "sheet_id": "101", "title": "요약"}
    assert_matches_outputs("google_sheets.create_worksheet", result)


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [None, "", " , ,"])
async def test_헤더가_없으면_쓰지_않는다(headers):
    fake = _Fake()
    result = await _run(fake, google_sheets_create_worksheet, spreadsheet_id="abc", title="요약", headers=headers)
    assert result["success"] is True
    assert fake.of("put") == []


@pytest.mark.asyncio
async def test_헤더_끝쪽_빈_항목만_버린다():
    fake = _Fake()
    await _run(fake, google_sheets_create_worksheet, spreadsheet_id="abc", title="요약", headers="a,,b,,")
    assert fake.of("put")[0][2]["json"]["values"] == [["a", "", "b"]]


@pytest.mark.asyncio
async def test_헤더_27개면_열_수_27():
    fake = _Fake()
    await _run(fake, google_sheets_create_worksheet, spreadsheet_id="abc", title="요약",
               headers=",".join(f"h{i}" for i in range(27)))
    assert _req(fake.of("addSheet")[0])["addSheet"]["properties"]["gridProperties"]["columnCount"] == 27


@pytest.mark.asyncio
async def test_작은따옴표_시트_제목_헤더_범위():
    fake = _Fake()
    await _run(fake, google_sheets_create_worksheet, spreadsheet_id="abc", title="Bob's", headers="a")
    assert fake.of("put")[0][2]["json"]["range"] == "'Bob''s'!A1"


@pytest.mark.asyncio
async def test_같은_이름_시트는_Google_오류를_그대로():
    fake = _Fake(fail={"addSheet": _err('A sheet with the name "요약" already exists.')})
    result = await _run(fake, google_sheets_create_worksheet, spreadsheet_id="abc", title="요약", headers="a")
    assert "already exists" in result["error"]
    assert fake.of("put") == []


@pytest.mark.asyncio
async def test_헤더_실패하면_시트를_지운다():
    fake = _Fake(fail={"put": _err("boom")})
    result = await _run(fake, google_sheets_create_worksheet, spreadsheet_id="abc", title="요약", headers="a")
    [delete] = fake.of("deleteSheet")
    assert _req(delete) == {"deleteSheet": {"sheetId": 101}}
    assert "error" in result


@pytest.mark.asyncio
async def test_되돌리기도_실패하면_남은_시트를_알린다():
    fake = _Fake(fail={"put": _err("boom"), "deleteSheet": _err("nope")})
    result = await _run(fake, google_sheets_create_worksheet, spreadsheet_id="abc", title="요약", headers="a")
    assert "만든 시트 '요약'이 남았을 수 있어요" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"spreadsheet_id": "", "title": "요약"},
    {"spreadsheet_id": "a/b", "title": "요약"},
    {"spreadsheet_id": "abc", "title": ""},
    {"spreadsheet_id": "abc", "title": "  "},
    {"spreadsheet_id": "abc", "title": None},
    {"spreadsheet_id": "abc", "title": "요약", "headers": 123},
])
async def test_워크시트_만들기_입력_거절(kwargs):
    await _no_google(google_sheets_create_worksheet, **kwargs)
    await _no_google(google_sheets_find_or_create_worksheet, **kwargs)


# --- S10 find_or_create_worksheet ---

@pytest.mark.asyncio
async def test_워크시트_찾기_없으면_만들기_찾음():
    fake = _Fake(sheets=(("Sales", 0, 0), ("요약", 7, 1)))
    result = await _run(fake, google_sheets_find_or_create_worksheet, spreadsheet_id="abc", title="요약", headers="a")
    [get] = fake.of("get_sheets")
    assert get[2]["params"]["fields"] == _SHEET_PROPS
    assert [c for c in fake.calls if c[0] != "GET"] == []
    assert result == {"success": True, "created": False, "sheet_id": "7", "title": "요약"}
    assert_matches_outputs("google_sheets.find_or_create_worksheet", result)


@pytest.mark.asyncio
async def test_워크시트_찾기_없으면_만들기_만듦():
    fake = _Fake()
    result = await _run(fake, google_sheets_find_or_create_worksheet, spreadsheet_id="abc", title="새탭",
                        headers="a,b")
    assert len(fake.of("addSheet")) == 1 and len(fake.of("put")) == 1
    assert result == {"success": True, "created": True, "sheet_id": "101", "title": "새탭"}
    assert_matches_outputs("google_sheets.find_or_create_worksheet", result)


@pytest.mark.asyncio
async def test_워크시트_찾기는_대소문자_구분():
    fake = _Fake()
    await _run(fake, google_sheets_find_or_create_worksheet, spreadsheet_id="abc", title="sales")
    assert len(fake.of("addSheet")) == 1


# --- C17 rename_worksheet ---

@pytest.mark.asyncio
async def test_워크시트_이름_바꾸기():
    fake = _Fake()
    result = await _run(fake, google_sheets_rename_worksheet, spreadsheet_id="abc", sheet_id="0", new_title="정리")
    [upd] = fake.of("updateSheetProperties")
    assert _req(upd) == {"updateSheetProperties": {"properties": {"sheetId": 0, "title": "정리"}, "fields": "title"}}
    assert result == {"success": True, "sheet_id": "0", "title": "정리", "previous_title": "Sales"}
    assert_matches_outputs("google_sheets.rename_worksheet", result)


@pytest.mark.asyncio
async def test_이름_바꾸기_시트_제목도_받는다():
    fake = _Fake()
    await _run(fake, google_sheets_rename_worksheet, spreadsheet_id="abc", sheet_id="Sales", new_title="정리")
    assert _req(fake.of("updateSheetProperties")[0])["updateSheetProperties"]["properties"]["sheetId"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"sheet_id": "0", "new_title": ""},
    {"sheet_id": "0", "new_title": None},
    {"sheet_id": "", "new_title": "정리"},
])
async def test_이름_바꾸기_입력_거절(kwargs):
    await _no_google(google_sheets_rename_worksheet, spreadsheet_id="abc", **kwargs)


# --- C16 copy_worksheet ---

@pytest.mark.asyncio
@pytest.mark.parametrize("dest", [None, "", "abc"])
async def test_같은_파일_복사는_duplicateSheet_1회(dest):
    fake = _Fake()
    result = await _run(fake, google_sheets_copy_worksheet, spreadsheet_id="abc", sheet_id="0",
                        destination_spreadsheet_id=dest, new_title="복사본")
    [dup] = [c for c in fake.calls if c[1].endswith(":batchUpdate")]
    assert _req(dup) == {"duplicateSheet": {"sourceSheetId": 0, "newSheetName": "복사본"}}
    assert fake.of("copyTo") == []
    assert result == {"success": True, "spreadsheet_id": "abc", "sheet_id": "102", "title": "복사본"}
    assert_matches_outputs("google_sheets.copy_worksheet", result)


@pytest.mark.asyncio
async def test_같은_파일_복사_이름_비우면_newSheetName_없음():
    fake = _Fake()
    result = await _run(fake, google_sheets_copy_worksheet, spreadsheet_id="abc", sheet_id="0", new_title="")
    assert "newSheetName" not in _req(fake.of("duplicateSheet")[0])["duplicateSheet"]
    assert result["title"] == "Sales의 사본"


@pytest.mark.asyncio
async def test_다른_파일_복사는_copyTo_뒤_이름_변경():
    fake = _Fake()
    result = await _run(fake, google_sheets_copy_worksheet, spreadsheet_id="abc", sheet_id="0",
                        destination_spreadsheet_id="dst", new_title="받은 시트")
    [copy] = fake.of("copyTo")
    assert copy[1].endswith("/spreadsheets/abc/sheets/0:copyTo")
    assert copy[2]["json"] == {"destinationSpreadsheetId": "dst"}
    [upd] = fake.of("updateSheetProperties")
    assert upd[1].endswith("/spreadsheets/dst:batchUpdate")
    assert _req(upd) == {"updateSheetProperties": {"properties": {"sheetId": 103, "title": "받은 시트"},
                                                   "fields": "title"}}
    assert result == {"success": True, "spreadsheet_id": "dst", "sheet_id": "103", "title": "받은 시트"}
    assert_matches_outputs("google_sheets.copy_worksheet", result)


@pytest.mark.asyncio
async def test_다른_파일_복사_이름_없으면_변경_안함():
    fake = _Fake()
    result = await _run(fake, google_sheets_copy_worksheet, spreadsheet_id="abc", sheet_id="0",
                        destination_spreadsheet_id="dst")
    assert [c for c in fake.calls if c[1].endswith(":batchUpdate")] == []
    assert result["title"] == "Copy of Sales"


@pytest.mark.asyncio
async def test_이름_변경_실패하면_대상에서_복사본을_지운다():
    fake = _Fake(fail={"updateSheetProperties": _err("boom")})
    result = await _run(fake, google_sheets_copy_worksheet, spreadsheet_id="abc", sheet_id="0",
                        destination_spreadsheet_id="dst", new_title="받은 시트")
    [delete] = fake.of("deleteSheet")
    assert delete[1].endswith("/spreadsheets/dst:batchUpdate")
    assert _req(delete) == {"deleteSheet": {"sheetId": 103}}
    assert "error" in result


@pytest.mark.asyncio
async def test_복사_되돌리기도_실패하면_남은_시트를_알린다():
    fake = _Fake(fail={"updateSheetProperties": _err("boom"), "deleteSheet": _err("nope")})
    result = await _run(fake, google_sheets_copy_worksheet, spreadsheet_id="abc", sheet_id="0",
                        destination_spreadsheet_id="dst", new_title="받은 시트")
    assert "대상 파일에 'Copy of Sales' 시트가 남았을 수 있어요" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"spreadsheet_id": "", "sheet_id": "0"},
    {"spreadsheet_id": "abc", "sheet_id": ""},
    {"spreadsheet_id": "abc", "sheet_id": "0", "destination_spreadsheet_id": "a/b"},
])
async def test_복사_입력_거절(kwargs):
    await _no_google(google_sheets_copy_worksheet, **kwargs)


# --- C18 update_worksheet ---

@pytest.mark.asyncio
async def test_속성_바꾸기_마스크는_채운_칸만():
    fake = _Fake()
    result = await _run(fake, google_sheets_update_worksheet, spreadsheet_id="abc", sheet_id="0",
                        frozen_rows="1", visibility="hide")
    [upd] = fake.of("updateSheetProperties")
    assert _req(upd) == {"updateSheetProperties": {
        "properties": {"sheetId": 0, "gridProperties": {"frozenRowCount": 1}, "hidden": True},
        "fields": "gridProperties.frozenRowCount,hidden"}}
    assert result == {"success": True, "sheet_id": "0", "title": "Sales", "updated": ["frozen_rows", "visibility"]}
    assert_matches_outputs("google_sheets.update_worksheet", result)


@pytest.mark.asyncio
async def test_속성_바꾸기_고정_해제는_0():
    fake = _Fake()
    await _run(fake, google_sheets_update_worksheet, spreadsheet_id="abc", sheet_id="0", frozen_columns=0)
    req = _req(fake.of("updateSheetProperties")[0])["updateSheetProperties"]
    assert req["properties"]["gridProperties"] == {"frozenColumnCount": 0}
    assert req["fields"] == "gridProperties.frozenColumnCount"


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", [None, ""])
async def test_속성이_전부_비면_Google을_부르지_않는다(blank):
    result = await _no_google(google_sheets_update_worksheet, spreadsheet_id="abc", sheet_id="0", frozen_rows=blank,
                              frozen_columns=blank, position=blank, visibility=blank)
    assert "바꿀 속성이 없어요" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"frozen_rows": -1}, {"position": 0}, {"position": "x"}, {"visibility": "hidden"}, {"frozen_rows": True},
])
async def test_속성_값_거절(kwargs):
    await _no_google(google_sheets_update_worksheet, spreadsheet_id="abc", sheet_id="0", **kwargs)


_ABC = (("A", 0, 0), ("B", 1, 1), ("C", 2, 2))


async def _index(sheet_id, position):
    fake = _Fake(sheets=_ABC)
    result = await _run(fake, google_sheets_update_worksheet, spreadsheet_id="abc", sheet_id=sheet_id,
                        position=position)
    assert result["updated"] == ["position"]
    req = _req(fake.of("updateSheetProperties")[0])["updateSheetProperties"]
    assert req["fields"] == "index"
    return req["properties"]["index"]


@pytest.mark.asyncio
async def test_오른쪽으로_옮기면_index는_position():
    assert await _index("0", 3) == 3


@pytest.mark.asyncio
async def test_왼쪽으로_옮기면_index는_position_빼기_1():
    assert await _index("2", 1) == 0


@pytest.mark.asyncio
async def test_제자리면_index_그대로():
    assert await _index("1", 2) == 1


@pytest.mark.asyncio
async def test_속성_바꾸기_시트_제목도_받는다():
    fake = _Fake(sheets=_ABC)
    result = await _run(fake, google_sheets_update_worksheet, spreadsheet_id="abc", sheet_id="B", visibility="show")
    req = _req(fake.of("updateSheetProperties")[0])["updateSheetProperties"]
    assert req["properties"] == {"sheetId": 1, "hidden": False}
    assert result["sheet_id"] == "1" and result["title"] == "B"


# --- C19 delete_worksheet ---

@pytest.mark.asyncio
@pytest.mark.parametrize("confirm", [True, "true"])
async def test_시트_삭제(confirm):
    fake = _Fake()
    result = await _run(fake, google_sheets_delete_worksheet, spreadsheet_id="abc", sheet_id="0",
                        confirm_delete=confirm)
    [delete] = [c for c in fake.calls if c[1].endswith(":batchUpdate")]
    assert _req(delete) == {"deleteSheet": {"sheetId": 0}}
    assert result == {"success": True, "deleted": True, "sheet_id": "0", "title": "Sales"}
    assert_matches_outputs("google_sheets.delete_worksheet", result)


@pytest.mark.asyncio
@pytest.mark.parametrize("confirm", [False, "false", "", None, "yes"])
async def test_삭제_확인이_꺼져_있으면_Google을_부르지_않는다(confirm):
    result = await _no_google(google_sheets_delete_worksheet, spreadsheet_id="abc", sheet_id="0",
                              confirm_delete=confirm)
    assert "삭제 확인을 켜야 삭제돼요" in result["error"]


@pytest.mark.asyncio
async def test_마지막_시트_삭제는_Google_오류를_그대로():
    fake = _Fake(fail={"deleteSheet": _err("You can't remove all the sheets in a document.")})
    result = await _run(fake, google_sheets_delete_worksheet, spreadsheet_id="abc", sheet_id="0",
                        confirm_delete=True)
    assert "You can't remove all the sheets in a document." in result["error"]


# --- C20 create_spreadsheet ---

_NEW_URL = "https://docs.google.com/spreadsheets/d/newfile1/edit"


@pytest.mark.asyncio
async def test_스프레드시트_만들기():
    fake = _Fake()
    result = await _run(fake, google_sheets_create_spreadsheet, title="예산")
    [create] = fake.of("drive_create")
    assert create[2]["params"]["supportsAllDrives"] == "true"
    assert create[2]["params"]["fields"] == "id,name,webViewLink"
    assert create[2]["json"] == {"name": "예산", "mimeType": "application/vnd.google-apps.spreadsheet"}
    assert fake.of("put") == []
    assert result == {"success": True, "spreadsheet_id": "newfile1", "title": "예산", "url": _NEW_URL}
    assert_matches_outputs("google_sheets.create_spreadsheet", result)


@pytest.mark.asyncio
async def test_폴더를_고르면_parents():
    fake = _Fake()
    await _run(fake, google_sheets_create_spreadsheet, title="예산", folder_id="fold1")
    assert fake.of("drive_create")[0][2]["json"]["parents"] == ["fold1"]


@pytest.mark.asyncio
async def test_새_파일_헤더는_첫_시트_A1_RAW():
    fake = _Fake()
    await _run(fake, google_sheets_create_spreadsheet, title="예산", headers="a,b")
    [put] = fake.of("put")
    assert "/spreadsheets/newfile1/values/A1" in put[1]
    assert put[2]["params"] == {"valueInputOption": "RAW"}
    assert put[2]["json"]["values"] == [["a", "b"]]
    assert fake.of("appendDimension") == []


@pytest.mark.asyncio
async def test_헤더_30개면_열을_먼저_늘린다():
    fake = _Fake()
    await _run(fake, google_sheets_create_spreadsheet, title="예산", headers=",".join(f"h{i}" for i in range(30)))
    kinds = [fake._kind(*c) for c in fake.calls]
    assert kinds == ["drive_create", "appendDimension", "put"]
    assert _req(fake.of("appendDimension")[0]) == {
        "appendDimension": {"sheetId": 0, "dimension": "COLUMNS", "length": 4}}


@pytest.mark.asyncio
async def test_복사하면_헤더를_무시한다():
    fake = _Fake()
    result = await _run(fake, google_sheets_create_spreadsheet, title="예산", copy_from="src1", headers="a,b",
                        folder_id="fold1")
    [copy] = fake.of("drive_copy")
    assert copy[1].endswith("/files/src1/copy")
    assert copy[2]["json"] == {"name": "예산", "parents": ["fold1"]}
    assert copy[2]["params"]["supportsAllDrives"] == "true"
    assert fake.of("put") == [] and fake.of("drive_create") == []
    assert result["spreadsheet_id"] == "copyfile1"


@pytest.mark.asyncio
async def test_헤더_실패하면_파일을_휴지통으로():
    fake = _Fake(fail={"put": _err("boom")})
    result = await _run(fake, google_sheets_create_spreadsheet, title="예산", headers="a")
    [patch_call] = fake.of("drive_patch")
    assert patch_call[1].endswith("/files/newfile1")
    assert patch_call[2]["json"] == {"trashed": True}
    assert patch_call[2]["params"]["supportsAllDrives"] == "true"
    assert "error" in result


@pytest.mark.asyncio
async def test_휴지통도_실패하면_남은_파일을_알린다():
    fake = _Fake(fail={"put": _err("boom"), "drive_patch": _err("nope")})
    result = await _run(fake, google_sheets_create_spreadsheet, title="예산", headers="a")
    assert "만든 스프레드시트 '예산'(newfile1)가 남았을 수 있어요" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"title": ""}, {"title": None}, {"title": "예산", "folder_id": "a/b"}, {"title": "예산", "copy_from": "x y"},
    {"title": "예산", "headers": 1},
])
async def test_스프레드시트_만들기_입력_거절(kwargs):
    await _no_google(google_sheets_create_spreadsheet, **kwargs)
    await _no_google(google_sheets_find_or_create_spreadsheet, **kwargs)


# --- S9 find_or_create_spreadsheet ---

@pytest.mark.asyncio
async def test_스프레드시트_찾기_없으면_만들기_찾음():
    fake = _Fake(drive_files=[{"id": "f1", "name": "예산", "webViewLink": "u1", "modifiedTime": "2026-11-10T00:00:00Z"},
                              {"id": "f2", "name": "예산", "webViewLink": "u2", "modifiedTime": "2026-11-01T00:00:00Z"}])
    result = await _run(fake, google_sheets_find_or_create_spreadsheet, title="예산", headers="a")
    assert len(fake.of("drive_list")) == 1
    assert fake.of("drive_create") == [] and fake.of("put") == []
    assert result == {"success": True, "created": False, "spreadsheet_id": "f1", "title": "예산", "url": "u1"}
    assert_matches_outputs("google_sheets.find_or_create_spreadsheet", result)


@pytest.mark.asyncio
async def test_스프레드시트_찾기_없으면_만들기_만듦():
    fake = _Fake()
    result = await _run(fake, google_sheets_find_or_create_spreadsheet, title="예산", headers="a")
    assert len(fake.of("drive_create")) == 1 and len(fake.of("put")) == 1
    assert result == {"success": True, "created": True, "spreadsheet_id": "newfile1", "title": "예산",
                      "url": _NEW_URL}
    assert_matches_outputs("google_sheets.find_or_create_spreadsheet", result)


@pytest.mark.asyncio
async def test_폴더를_고르면_그_폴더_안에서만_찾는다():
    fake = _Fake()
    await _run(fake, google_sheets_find_or_create_spreadsheet, title="예산", folder_id="fold1")
    q = fake.of("drive_list")[0][2]["params"]["q"]
    assert "'fold1' in parents" in q and "name = '예산'" in q
    assert fake.of("drive_create")[0][2]["json"]["parents"] == ["fold1"]

    fake = _Fake()
    await _run(fake, google_sheets_find_or_create_spreadsheet, title="예산")
    assert "in parents" not in fake.of("drive_list")[0][2]["params"]["q"]

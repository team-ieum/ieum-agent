import json
from unittest.mock import patch

import pytest

from common.error_code import ToolErrorCode
from tests.action_outputs import assert_matches_outputs
from tests.test_google_sheets import _resp
from tools.google_sheets import _DRIVE_FILES_URL, _SHEET_PROPS
from tools.google_sheets_sheets import (
    google_sheets_create_worksheet, google_sheets_find_or_create_worksheet, google_sheets_rename_worksheet,
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

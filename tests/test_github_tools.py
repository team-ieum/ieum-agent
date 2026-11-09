import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.action_outputs import assert_matches_outputs
from tools.github import (
    _api_error,
    _csv,
    _found,
    _head_ref,
    _issue_no,
    _ref_path,
    _right_side_lines,
    github_add_labels,
    github_create_comment,
    github_create_issue,
    github_find_issue,
    github_check_org_membership,
    github_create_pull_request,
    github_find_branch,
    github_find_org,
    github_find_repo,
    github_find_user,
    github_get_file,
    github_find_or_create_issue,
    github_find_or_create_pull_request,
    github_find_pull_request,
    github_get_pr_files,
    github_get_pr_reviews,
    github_list_issues,
    github_request_reviewers,
    github_submit_pr_review,
    github_update_issue,
    github_update_pull_request,
)


def _response(status_code: int, data: dict | None = None, text: str = "") -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = data or {}
    response.text = text
    return response


@pytest.fixture
def client():
    mock = AsyncMock()
    with patch("tools.github.get_http_client", return_value=mock):
        yield mock


# 이 PR의 카탈로그에는 새 이벤트가 없어 assert_matches_outputs 대신 반환 키를 직접 단언한다.
_OUTPUT_KEYS: dict[str, set[str]] = {
    "github.create_issue": {"number", "title", "url"},
    "github.find_issue": {"found", "total", "items", "message"},
    "github.update_issue": {"number", "url", "title", "state", "warnings"},
    "github.add_labels": {"number", "labels"},
    "github.create_comment": {"id", "url"},
    "github.find_or_create_issue": {"created", "number", "url", "title", "state", "warnings"},
    "github.create_pull_request": {"number", "url", "title", "state", "draft"},
    "github.update_pull_request": {"number", "url", "title", "state"},
    "github.request_reviewers": {"number", "requested_reviewers"},
    "github.find_pull_request": {"found", "total", "items", "message"},
    "github.find_or_create_pull_request": {"created", "number", "url", "title", "state", "draft"},
    "github.get_pr_reviews": {"found", "total", "items", "comments", "approved_count", "changes_requested_count",
                              "message", "truncated"},
    "github.find_branch": {"found", "total", "items", "message"},
    "github.get_file": {"found", "total", "items", "message"},
    "github.find_repo": {"found", "total", "items", "message"},
    "github.find_org": {"found", "total", "items", "message"},
    "github.check_org_membership": {"found", "total", "items", "message", "member"},
    "github.find_user": {"found", "total", "items", "message"},
    "github.get_pr_files": {"diff", "found", "head_sha", "items", "skipped", "total", "truncated"},
    "github.submit_pr_review": {"moved_comments", "posted_comments", "review_id", "state", "url", "warnings"},
}


def _assert_outputs(event: str, result: dict) -> None:
    assert "error" not in result, result
    assert set(result) - {"success"} == _OUTPUT_KEYS[event], (event, sorted(result))


# ---------------------------------------------------------------------------
# 공용 함수
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    (12, 12), ("12", 12), (" #12 ", 12),
    ("0", None), ("-1", None), ("12.0", None), ("abc", None), ("", None), (None, None), (True, None),
    ("１２", None), ("##12", None),
])
def test_issue_no(value, expected):
    assert _issue_no(value) == expected


@pytest.mark.parametrize("value, expected", [
    ("main", "main"),
    ("feature/a b#1", "feature/a%20b%231"),
    ("docs/설명.md", "docs/%EC%84%A4%EB%AA%85.md"),
    ("a/%2e%2e/b", "a/%252e%252e/b"),
])
def test_ref_path_허용(value, expected):
    assert _ref_path(value) == expected


@pytest.mark.parametrize("value", [
    "../x", "a/../b", "a//b", "/a", "a/", ".", "a\\b", "a\nb", "", "   ", "a" * 1025, None, 3, "\nmain",
])
def test_ref_path_거부(value):
    assert _ref_path(value) is None


@pytest.mark.parametrize("value, expected", [
    ("feature/x", "feature/x"), ("user:feature/x", "user:feature/x"),
    ("us er:x", None), ("user:../x", None), (":x", None),
    ("../x", None), ("user: x", None), ("user:a:b", None), ("user:\nx", None),
])
def test_head_ref(value, expected):
    assert _head_ref(value) == expected


def test_csv():
    assert _csv(" bug, p1 ,,bug ") == ["bug", "p1"]
    assert _csv(None) == []


def test_found_0건():
    assert json.loads(_found([])) == {
        "success": True, "found": False, "total": 0, "items": [], "message": "결과가 없습니다"}
    found = json.loads(_found([{"a": 1}]))
    assert found["message"] == ""
    assert (found["found"], found["total"]) == (True, 1)


def test_api_error_403은_권한_승인_안내():
    assert json.loads(_api_error(_response(403, text="x")))["error"] == (
        "GitHub API 오류 (403): x — GitHub App 새 권한을 승인했는지 확인해 주세요.")


# ---------------------------------------------------------------------------
# github_create_issue
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_issue_성공(client):
    client.post = AsyncMock(return_value=_response(201, {
        "id": 99, "number": 7, "title": "버그", "html_url": "https://github.com/o/r/issues/7",
    }))

    result = json.loads(await github_create_issue(
        token="gh-token", owner="o", repo="r", title="버그", body="재현 방법"))

    assert_matches_outputs("github.create_issue", result)
    assert result == {"success": True, "number": 7, "url": "https://github.com/o/r/issues/7", "title": "버그"}
    args, kwargs = client.post.call_args
    assert args[0] == "https://api.github.com/repos/o/r/issues"
    assert kwargs["json"] == {"title": "버그", "body": "재현 방법"}
    assert kwargs["headers"]["Authorization"] == "Bearer gh-token"


@pytest.mark.asyncio
async def test_create_issue_본문_기본값은_빈_문자열(client):
    client.post = AsyncMock(return_value=_response(201, {"number": 1, "title": "t", "html_url": "u"}))
    await github_create_issue(token="gh-token", owner="o", repo="r", title="t")
    assert client.post.call_args.kwargs["json"] == {"title": "t", "body": ""}


@pytest.mark.asyncio
async def test_create_issue_api_오류는_본문_전체를_싣는다(client):
    client.post = AsyncMock(return_value=_response(422, text='{"message":"Validation Failed"}'))
    result = json.loads(await github_create_issue(token="gh-token", owner="o", repo="r", title="t"))
    assert result == {"error": 'GitHub API 오류 (422): {"message":"Validation Failed"}'}


@pytest.mark.asyncio
async def test_create_issue_네트워크_예외는_error로_돌려준다(client):
    client.post = AsyncMock(side_effect=RuntimeError("연결 끊김"))
    result = json.loads(await github_create_issue(token="gh-token", owner="o", repo="r", title="t"))
    assert result == {"error": "연결 끊김"}


@pytest.mark.asyncio
@pytest.mark.parametrize("owner, repo", [
    ("o/../../user", "r"), ("o", "r/../../user"), ("..", "r"), ("o", "."), ("o/x", "r"),
    ("", "r"), ("o", ""), ("o r", "r"), ("o", "r?x=1"),
])
async def test_create_issue_owner_repo에_경로_문자가_있으면_요청하지_않는다(client, owner, repo):
    """쓰기 요청의 URL 경로는 사용자 입력이다 — `..`·`/`로 다른 GitHub API 경로를 칠 수 없어야 한다."""
    client.post = AsyncMock()
    result = json.loads(await github_create_issue(token="gh-token", owner=owner, repo=repo, title="t"))
    assert "error" in result
    client.post.assert_not_called()


# ---------------------------------------------------------------------------
# 이슈 묶음 — find_issue·update_issue·add_labels·create_comment·find_or_create_issue
# ---------------------------------------------------------------------------

_ISSUE_ITEM_KEYS = {"number", "title", "state", "url", "labels", "assignees", "author", "created_at"}


def _issue(number: int, *, repo_url: str = "https://api.github.com/repos/o/r", pr: bool = False) -> dict:
    issue = {
        "number": number, "title": f"이슈 {number}", "state": "open", "html_url": f"https://github.com/o/r/issues/{number}",
        "labels": [{"name": "bug"}], "assignees": [{"login": "alice"}], "user": {"login": "bob"},
        "created_at": "2026-11-01T00:00:00Z", "repository_url": repo_url,
    }
    if pr:
        issue["pull_request"] = {}
    return issue


@pytest.mark.asyncio
async def test_find_issue_번호(client):
    client.get = AsyncMock(return_value=_response(200, _issue(5)))
    result = json.loads(await github_find_issue(token="t", owner="o", repo="r", search_by="number", value="5"))
    _assert_outputs("github.find_issue", result)
    assert result["found"] is True
    assert set(result["items"][0]) == _ISSUE_ITEM_KEYS
    assert client.get.call_args.args[0] == "https://api.github.com/repos/o/r/issues/5"

    client.get = AsyncMock(return_value=_response(404, text="Not Found"))
    result = json.loads(await github_find_issue(token="t", owner="o", repo="r", search_by="number", value="5"))
    assert result == {"success": True, "found": False, "total": 0, "items": [], "message": "결과가 없습니다"}

    client.get = AsyncMock(return_value=_response(200, _issue(5, pr=True)))
    result = json.loads(await github_find_issue(token="t", owner="o", repo="r", search_by="number", value="5"))
    assert result["total"] == 0


@pytest.mark.asyncio
async def test_find_issue_라벨_목록_페이지(client):
    page1 = [_issue(i, pr=i < 10) for i in range(100)]
    page2 = [_issue(100 + i) for i in range(20)]
    client.get = AsyncMock(side_effect=[_response(200, page1), _response(200, page2)])
    result = json.loads(await github_find_issue(
        token="t", owner="o", repo="r", search_by="label", value="bug", max_results=150))

    _assert_outputs("github.find_issue", result)
    assert len(result["items"]) == 100
    assert client.get.call_count == 2
    params = client.get.call_args_list[0].kwargs["params"]
    assert params["labels"] == "bug"
    assert params["per_page"] == 100


@pytest.mark.asyncio
async def test_find_issue_제목_검색(client):
    client.get = AsyncMock(return_value=_response(200, {"items": [_issue(1)]}))
    result = json.loads(await github_find_issue(token="t", owner="o", repo="r", search_by="title", value="로그인"))
    _assert_outputs("github.find_issue", result)
    assert client.get.call_args.args[0] == "https://api.github.com/search/issues"
    assert client.get.call_args.kwargs["params"]["q"] == "로그인 in:title repo:o/r is:issue state:open"

    await github_find_issue(token="t", owner="o", repo="r", search_by="title", value="로그인", state="all")
    assert "state:" not in client.get.call_args.kwargs["params"]["q"]


@pytest.mark.asyncio
async def test_find_issue_다른_레포_결과는_버린다(client):
    client.get = AsyncMock(return_value=_response(200, {"items": [
        _issue(1, repo_url="https://api.github.com/repos/a/secret"),
        _issue(2, repo_url="https://api.github.com/repos/O/r"),
        _issue(3, pr=True),
    ]}))
    result = json.loads(await github_find_issue(
        token="t", owner="o", repo="r", search_by="title", value="x repo:a/secret"))
    _assert_outputs("github.find_issue", result)
    assert [i["number"] for i in result["items"]] == [2]


@pytest.mark.asyncio
@pytest.mark.parametrize("search_by, value", [("nope", "x"), ("title", "  "), ("author", "a b")])
async def test_find_issue_입력_오류(client, search_by, value):
    client.get = AsyncMock()
    result = json.loads(await github_find_issue(token="t", owner="o", repo="r", search_by=search_by, value=value))
    assert "error" in result
    client.get.assert_not_called()


def _updated(**extra) -> MagicMock:
    return _response(200, {"number": 7, "title": "새 제목", "state": "open", "html_url": "u",
                           "labels": [], "assignees": [], "milestone": None, **extra})


@pytest.mark.asyncio
async def test_update_issue_빈칸은_보내지_않는다(client):
    client.patch = AsyncMock(return_value=_updated())
    result = json.loads(await github_update_issue(token="t", owner="o", repo="r", issue_number="#7", title="새 제목"))
    _assert_outputs("github.update_issue", result)
    assert client.patch.call_args.args[0] == "https://api.github.com/repos/o/r/issues/7"
    assert client.patch.call_args.kwargs["json"] == {"title": "새 제목"}


@pytest.mark.asyncio
async def test_update_issue_닫는_이유(client):
    client.patch = AsyncMock(return_value=_updated(state="closed"))
    result = json.loads(await github_update_issue(
        token="t", owner="o", repo="r", issue_number="7", state="closed", state_reason="not_planned"))
    _assert_outputs("github.update_issue", result)
    assert client.patch.call_args.kwargs["json"] == {"state": "closed", "state_reason": "not_planned"}

    client.patch = AsyncMock(return_value=_updated())
    result = json.loads(await github_update_issue(
        token="t", owner="o", repo="r", issue_number="7", title="새 제목", state_reason="completed"))
    assert client.patch.call_args.kwargs["json"] == {"title": "새 제목"}
    assert result["warnings"] == ["닫는 이유는 상태를 바꿀 때만 적용돼요."]

    client.patch = AsyncMock()
    result = json.loads(await github_update_issue(
        token="t", owner="o", repo="r", issue_number="7", state_reason="completed"))
    assert result == {"error": "바꿀 칸이 없습니다."}
    client.patch.assert_not_called()


@pytest.mark.asyncio
async def test_update_issue_라벨_통째_교체_경고(client):
    client.patch = AsyncMock(return_value=_updated())
    result = json.loads(await github_update_issue(token="t", owner="o", repo="r", issue_number="7", labels="a"))
    assert client.patch.call_args.kwargs["json"] == {"labels": ["a"]}
    assert result["warnings"] == ["반영되지 않은 라벨: a"]


@pytest.mark.asyncio
@pytest.mark.parametrize("issue_number", ["0", "abc"])
async def test_update_issue_번호_오류(client, issue_number):
    client.patch = AsyncMock()
    result = json.loads(await github_update_issue(
        token="t", owner="o", repo="r", issue_number=issue_number, title="x"))
    assert "error" in result
    client.patch.assert_not_called()


@pytest.mark.asyncio
async def test_add_labels(client):
    client.post = AsyncMock(return_value=_response(200, [{"name": "old"}, {"name": "bug"}, {"name": "p1"}]))
    result = json.loads(await github_add_labels(token="t", owner="o", repo="r", issue_number="7", labels="bug,p1"))
    _assert_outputs("github.add_labels", result)
    assert client.post.call_args.args[0] == "https://api.github.com/repos/o/r/issues/7/labels"
    assert client.post.call_args.kwargs["json"] == {"labels": ["bug", "p1"]}
    assert result["labels"] == ["old", "bug", "p1"]

    client.post = AsyncMock()
    result = json.loads(await github_add_labels(token="t", owner="o", repo="r", issue_number="7", labels=" , "))
    assert "error" in result
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_create_comment(client):
    client.post = AsyncMock(return_value=_response(201, {"id": 1, "html_url": "u"}))
    result = json.loads(await github_create_comment(token="t", owner="o", repo="r", issue_number="7", body="좋아요"))
    _assert_outputs("github.create_comment", result)
    assert result == {"success": True, "id": 1, "url": "u"}
    assert client.post.call_args.args[0] == "https://api.github.com/repos/o/r/issues/7/comments"

    client.post = AsyncMock()
    result = json.loads(await github_create_comment(
        token="t", owner="o", repo="r", issue_number="7", body="a" * 65537))
    assert "error" in result
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_find_or_create_issue_있으면_만들지_않는다(client):
    client.get = AsyncMock(return_value=_response(200, {"items": [_issue(4)]}))
    client.post = AsyncMock()
    result = json.loads(await github_find_or_create_issue(
        token="t", owner="o", repo="r", search_by="title", value="이슈 4", title="이슈 4"))
    _assert_outputs("github.find_or_create_issue", result)
    assert result["created"] is False
    assert result["number"] == 4
    assert result["warnings"] == []
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_find_or_create_issue_제목은_완전_일치만(client):
    """`in:title`은 토큰 일치라 부분 일치 이슈가 걸린다 — 제목 완전 일치만 '있음'으로 본다."""
    client.get = AsyncMock(return_value=_response(200, {"items": [
        {**_issue(4), "title": "로그인 화면 오류 수정"},
        {**_issue(5), "title": "  Login   Error "},
    ]}))
    client.post = AsyncMock()
    result = json.loads(await github_find_or_create_issue(
        token="t", owner="o", repo="r", search_by="title", value="login error", title="login error"))
    assert (result["created"], result["number"]) == (False, 5)
    client.post.assert_not_called()

    client.get = AsyncMock(return_value=_response(200, {"items": [{**_issue(4), "title": "로그인 화면 오류 수정"}]}))
    client.post = AsyncMock(return_value=_response(201, {
        "number": 9, "title": "로그인 오류", "state": "open", "html_url": "u"}))
    result = json.loads(await github_find_or_create_issue(
        token="t", owner="o", repo="r", search_by="title", value="로그인 오류", title="로그인 오류"))
    assert (result["created"], result["number"]) == (True, 9)
    client.post.assert_called_once()


@pytest.mark.asyncio
async def test_find_or_create_issue_없으면_만든다(client):
    client.get = AsyncMock(return_value=_response(200, {"items": []}))
    client.post = AsyncMock(return_value=_response(201, {
        "number": 9, "title": "새 이슈", "state": "open", "html_url": "u"}))
    result = json.loads(await github_find_or_create_issue(
        token="t", owner="o", repo="r", search_by="title", value="새 이슈", title="새 이슈"))
    _assert_outputs("github.find_or_create_issue", result)
    assert client.post.call_count == 1
    assert result["created"] is True
    assert result["number"] == 9


@pytest.mark.asyncio
async def test_find_or_create_issue_찾기_오류면_만들지_않는다(client):
    client.get = AsyncMock(return_value=_response(500, text="boom"))
    client.post = AsyncMock()
    result = json.loads(await github_find_or_create_issue(
        token="t", owner="o", repo="r", search_by="title", value="x", title="x"))
    assert "error" in result
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_find_or_create_issue_제목_빈값은_검색_전에_거부(client):
    client.get = AsyncMock()
    client.post = AsyncMock()
    result = json.loads(await github_find_or_create_issue(
        token="t", owner="o", repo="r", search_by="title", value="x", title="  "))
    assert result == {"error": "title을 입력해 주세요."}
    client.get.assert_not_called()
    client.post.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["", None])
async def test_find_issue_state_빈값은_open(client, state):
    client.get = AsyncMock(return_value=_response(200, {"items": []}))
    result = json.loads(await github_find_issue(
        token="t", owner="o", repo="r", search_by="title", value="x", state=state))
    assert "error" not in result
    assert client.get.call_args.kwargs["params"]["q"].endswith(" state:open")


# ---------------------------------------------------------------------------
# PR 묶음 — create·update·request_reviewers·find·find_or_create·get_pr_reviews
# ---------------------------------------------------------------------------

def _pr(number: int, *, repo_url: str = "https://api.github.com/repos/o/r", issue: bool = False) -> dict:
    pr = {
        "number": number, "title": f"PR {number}", "state": "open", "html_url": f"https://github.com/o/r/pull/{number}",
        "draft": False, "user": {"login": "alice"}, "created_at": "2026-11-01T00:00:00Z", "repository_url": repo_url,
    }
    if not issue:
        pr["pull_request"] = {}
    return pr


@pytest.mark.asyncio
async def test_create_pull_request(client):
    client.post = AsyncMock(return_value=_response(201, _pr(3)))
    result = json.loads(await github_create_pull_request(
        token="t", owner="o", repo="r", title="t", head="user:feat/x", base="main"))
    _assert_outputs("github.create_pull_request", result)
    assert client.post.call_args.args[0] == "https://api.github.com/repos/o/r/pulls"
    assert client.post.call_args.kwargs["json"] == {
        "title": "t", "head": "user:feat/x", "base": "main", "body": "", "draft": False, "maintainer_can_modify": True}

    client.post = AsyncMock()
    result = json.loads(await github_create_pull_request(
        token="t", owner="o", repo="r", title="t", head="../x", base="main"))
    assert "error" in result
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_update_pull_request_빈칸은_보내지_않는다(client):
    client.patch = AsyncMock(return_value=_response(200, {**_pr(3), "state": "closed"}))
    result = json.loads(await github_update_pull_request(token="t", owner="o", repo="r", pull_number="3", state="closed"))
    _assert_outputs("github.update_pull_request", result)
    assert client.patch.call_args.args[0] == "https://api.github.com/repos/o/r/pulls/3"
    assert client.patch.call_args.kwargs["json"] == {"state": "closed"}

    client.patch = AsyncMock()
    result = json.loads(await github_update_pull_request(token="t", owner="o", repo="r", pull_number="3"))
    assert result == {"error": "바꿀 칸이 없습니다."}
    client.patch.assert_not_called()


@pytest.mark.asyncio
async def test_request_reviewers(client):
    client.post = AsyncMock(return_value=_response(201, {
        "number": 3, "requested_reviewers": [{"login": "alice"}, {"login": "bob"}]}))
    result = json.loads(await github_request_reviewers(
        token="t", owner="o", repo="r", pull_number="3", reviewers="alice, bob"))
    _assert_outputs("github.request_reviewers", result)
    assert client.post.call_args.args[0] == "https://api.github.com/repos/o/r/pulls/3/requested_reviewers"
    assert client.post.call_args.kwargs["json"] == {"reviewers": ["alice", "bob"]}
    assert result["requested_reviewers"] == ["alice", "bob"]

    client.post = AsyncMock()
    result = json.loads(await github_request_reviewers(
        token="t", owner="o", repo="r", pull_number="3", reviewers="a b"))
    assert "error" in result
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_find_pull_request_번호_404는_0건(client):
    client.get = AsyncMock(return_value=_response(404, text="Not Found"))
    result = json.loads(await github_find_pull_request(token="t", owner="o", repo="r", search_by="number", value="3"))
    assert result == {"success": True, "found": False, "total": 0, "items": [], "message": "결과가 없습니다"}
    assert client.get.call_args.args[0] == "https://api.github.com/repos/o/r/pulls/3"


@pytest.mark.asyncio
async def test_find_pull_request_브랜치(client):
    client.get = AsyncMock(return_value=_response(200, [_pr(3)]))
    result = json.loads(await github_find_pull_request(
        token="t", owner="o", repo="r", search_by="branch", value="feature/x"))
    _assert_outputs("github.find_pull_request", result)
    assert client.get.call_args.args[0] == "https://api.github.com/repos/o/r/pulls"
    assert client.get.call_args.kwargs["params"]["head"] == "o:feature/x"
    assert [i["number"] for i in result["items"]] == [3]


@pytest.mark.asyncio
async def test_find_pull_request_작성자_검색(client):
    client.get = AsyncMock(return_value=_response(200, {"items": [
        _pr(1, repo_url="https://api.github.com/repos/a/secret"),
        _pr(2),
        _pr(3, issue=True),
    ]}))
    result = json.loads(await github_find_pull_request(
        token="t", owner="o", repo="r", search_by="author", value="alice"))
    _assert_outputs("github.find_pull_request", result)
    assert set(client.get.call_args.kwargs["params"]["q"].split()) == {"author:alice", "is:pr", "repo:o/r", "state:open"}
    assert [i["number"] for i in result["items"]] == [2]


@pytest.mark.asyncio
async def test_find_or_create_pull_request_있으면_만들지_않는다(client):
    client.get = AsyncMock(return_value=_response(200, [_pr(5)]))
    client.post = AsyncMock()
    result = json.loads(await github_find_or_create_pull_request(
        token="t", owner="o", repo="r", head="feat/x", base="main", title="t"))
    _assert_outputs("github.find_or_create_pull_request", result)
    assert result["created"] is False
    assert result["number"] == 5
    params = client.get.call_args.kwargs["params"]
    assert (params["head"], params["base"], params["state"], params["per_page"]) == ("o:feat/x", "main", "open", 1)
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_find_or_create_pull_request_없으면_만든다(client):
    client.get = AsyncMock(return_value=_response(200, []))
    client.post = AsyncMock(return_value=_response(201, _pr(6)))
    result = json.loads(await github_find_or_create_pull_request(
        token="t", owner="o", repo="r", head="feat/x", base="main", title="t"))
    _assert_outputs("github.find_or_create_pull_request", result)
    assert client.post.call_count == 1
    assert result["created"] is True
    assert result["number"] == 6


@pytest.mark.asyncio
async def test_find_or_create_pull_request_목록_오류면_만들지_않는다(client):
    client.get = AsyncMock(return_value=_response(404, text="Not Found"))
    client.post = AsyncMock()
    result = json.loads(await github_find_or_create_pull_request(
        token="t", owner="o", repo="r", head="feat/x", base="main", title="t"))
    assert "error" in result
    client.post.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["", None])
async def test_find_pull_request_state_빈값은_open(client, state):
    client.get = AsyncMock(return_value=_response(200, [_pr(3)]))
    result = json.loads(await github_find_pull_request(
        token="t", owner="o", repo="r", search_by="branch", value="feature/x", state=state))
    assert "error" not in result
    assert client.get.call_args.kwargs["params"]["state"] == "open"


def _review(review_id: int, state: str, user_type: str = "User", body: str = "b", login: str | None = None) -> dict:
    return {"id": review_id, "state": state, "body": body,
            "user": {"login": login or f"u{review_id}", "type": user_type},
            "submitted_at": "2026-11-02T00:00:00Z", "commit_id": "abc"}


def _comment(n: int, body: str = "c", user_type: str = "User") -> dict:
    return {"path": f"f{n}.py", "line": n, "body": body, "user": {"login": f"c{n}", "type": user_type},
            "created_at": "2026-11-02T00:00:00Z"}


def _reviews_client(client, reviews: list, comments: list = ()) -> None:
    """reviews는 GitHub처럼 오래된 것부터, comments는 최신순 요청(sort=created&direction=desc) 결과로 준다."""
    def get(url, **kwargs):
        page = kwargs["params"]["page"]
        data = reviews if url.endswith("/reviews") else list(comments)
        return _response(200, data[(page - 1) * 100: page * 100])
    client.get = AsyncMock(side_effect=get)


def _comment_gets(client) -> list:
    return [c for c in client.get.call_args_list if c.args[0].endswith("/comments")]


@pytest.mark.asyncio
async def test_get_pr_reviews_필터와_수치(client):
    _reviews_client(client, [
        _review(1, "APPROVED"), _review(2, "APPROVED", "Bot"), _review(3, "CHANGES_REQUESTED"), _review(4, "PENDING"),
    ], [
        {"path": "a.py", "line": None, "original_line": 4, "body": "c", "user": {"login": "u1", "type": "User"},
         "created_at": "2026-11-02T00:00:00Z"},
        {"path": "b.py", "line": 2, "body": "봇", "user": {"login": "bot", "type": "Bot"},
         "created_at": "2026-11-02T00:00:00Z"},
    ])
    result = json.loads(await github_get_pr_reviews(token="t", owner="o", repo="r", pull_number="3", author_type="human"))
    _assert_outputs("github.get_pr_reviews", result)
    assert [i["id"] for i in result["items"]] == [3, 1]  # 최신 우선
    assert set(result["items"][0]) == {"id", "author", "author_type", "state", "body", "submitted_at", "commit_id"}
    assert (result["approved_count"], result["changes_requested_count"]) == (1, 1)
    assert result["comments"] == [
        {"path": "a.py", "line": 4, "body": "c", "author": "u1", "created_at": "2026-11-02T00:00:00Z"}]
    assert result["truncated"] is False
    params = _comment_gets(client)[0].kwargs["params"]
    assert (params["sort"], params["direction"]) == ("created", "desc")


@pytest.mark.asyncio
async def test_get_pr_reviews_리뷰어별_마지막_결정(client):
    """GitHub PR 화면처럼 리뷰어마다 마지막 APPROVED|CHANGES_REQUESTED 하나로 센다. COMMENTED는 결정을 바꾸지 않는다."""
    _reviews_client(client, [
        _review(1, "APPROVED", login="alice"),
        _review(2, "CHANGES_REQUESTED", login="alice"),
        _review(3, "COMMENTED", login="alice"),
    ])
    result = json.loads(await github_get_pr_reviews(token="t", owner="o", repo="r", pull_number="3"))
    assert (result["approved_count"], result["changes_requested_count"]) == (0, 1)


@pytest.mark.asyncio
async def test_get_pr_reviews_해제된_승인은_세지_않는다(client):
    """승인이 dismiss되면 그 리뷰 자체가 DISMISSED가 된다 — GitHub 화면처럼 그 리뷰어는 어디에도 세지 않는다."""
    _reviews_client(client, [
        _review(1, "APPROVED", login="alice"),
        _review(2, "APPROVED", login="alice"),
        _review(3, "DISMISSED", login="alice"),
    ])
    result = json.loads(await github_get_pr_reviews(token="t", owner="o", repo="r", pull_number="3"))
    assert (result["approved_count"], result["changes_requested_count"]) == (0, 0)


@pytest.mark.asyncio
async def test_get_pr_reviews_본문_합계_상한(client):
    reviews = [_review(i, "COMMENTED", body="r" * 5_000) for i in range(50)]
    comments = [_comment(i, body="c" * 5_000) for i in range(100)]
    _reviews_client(client, reviews, comments)
    result = json.loads(await github_get_pr_reviews(token="t", owner="o", repo="r", pull_number="3"))
    _assert_outputs("github.get_pr_reviews", result)
    assert {len(i["body"]) for i in result["items"]} == {2_000}
    assert len(result["comments"]) == 100
    assert all(len(c["body"]) <= 1_000 for c in result["comments"])
    total = sum(len(i["body"]) for i in result["items"]) + sum(len(c["body"]) for c in result["comments"])
    assert total == 150_000
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_get_pr_reviews_코멘트_100개면_페이지_중단(client):
    _reviews_client(client, [_review(1, "COMMENTED")], [_comment(i) for i in range(250)])
    result = json.loads(await github_get_pr_reviews(token="t", owner="o", repo="r", pull_number="3"))
    assert [c["path"] for c in result["comments"]][:2] == ["f0.py", "f1.py"]  # 받은 최신순 그대로
    assert len(result["comments"]) == 100
    assert len(_comment_gets(client)) == 1
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_get_pr_reviews_상태_필터(client):
    _reviews_client(client, [_review(1, "APPROVED"), _review(2, "COMMENTED"), _review(3, "APPROVED", "Bot")])
    result = json.loads(await github_get_pr_reviews(token="t", owner="o", repo="r", pull_number="3", states="approved"))
    _assert_outputs("github.get_pr_reviews", result)
    assert {i["state"] for i in result["items"]} == {"APPROVED"}
    assert len(result["items"]) == 2

    client.get = AsyncMock()
    result = json.loads(await github_get_pr_reviews(token="t", owner="o", repo="r", pull_number="3", states="LGTM"))
    assert "error" in result
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_get_pr_reviews_상한(client):
    reviews = [_review(i, "APPROVED", body="x" * 20_000 if i == 149 else "b") for i in range(150)]
    _reviews_client(client, reviews)
    result = json.loads(await github_get_pr_reviews(
        token="t", owner="o", repo="r", pull_number="3", include_comments=False))
    _assert_outputs("github.get_pr_reviews", result)
    assert [i["id"] for i in result["items"]] == list(range(149, 99, -1))  # 최신 50개, 최신 우선
    assert result["approved_count"] == 150  # 수치는 자르기 전 전체 기준
    assert len(result["items"][0]["body"]) == 2_000
    assert result["truncated"] is True
    assert result["comments"] == []
    assert all(c.args[0].endswith("/reviews") for c in client.get.call_args_list)
    assert client.get.call_count == 2


@pytest.mark.asyncio
async def test_get_pr_reviews_0건(client):
    _reviews_client(client, [])
    result = json.loads(await github_get_pr_reviews(token="t", owner="o", repo="r", pull_number="3"))
    _assert_outputs("github.get_pr_reviews", result)
    assert result["found"] is False
    assert result["message"] == "결과가 없습니다"
    assert (result["total"], result["approved_count"], result["changes_requested_count"]) == (0, 0, 0)


# ---------------------------------------------------------------------------
# 조회 묶음 — find_branch·get_file·find_repo·find_org·check_org_membership·find_user
# ---------------------------------------------------------------------------

_EMPTY = {"success": True, "found": False, "total": 0, "items": [], "message": "결과가 없습니다"}


def _branch(name: str) -> dict:
    return {"name": name, "commit": {"sha": f"sha-{name}", "url": "x"}, "protected": False}


@pytest.mark.asyncio
async def test_find_branch_목록과_단건(client):
    client.get = AsyncMock(return_value=_response(200, [_branch("main"), _branch("dev")]))
    result = json.loads(await github_find_branch(token="t", owner="o", repo="r"))
    _assert_outputs("github.find_branch", result)
    assert client.get.call_args.args[0] == "https://api.github.com/repos/o/r/branches"
    assert client.get.call_args.kwargs["params"]["per_page"] == 100
    assert result["items"][0] == {"name": "main", "sha": "sha-main", "protected": False}

    client.get = AsyncMock(return_value=_response(200, _branch("feature/a b")))
    result = json.loads(await github_find_branch(token="t", owner="o", repo="r", branch="feature/a b"))
    _assert_outputs("github.find_branch", result)
    assert client.get.call_args.args[0] == "https://api.github.com/repos/o/r/branches/feature%2Fa%20b"
    assert result["total"] == 1

    # `/`가 경로 구분자로 남으면 브랜치 보호(…/branches/main/protection) 같은 하위 API로 닿는다.
    await github_find_branch(token="t", owner="o", repo="r", branch="main/protection")
    assert client.get.call_args.args[0] == "https://api.github.com/repos/o/r/branches/main%2Fprotection"

    client.get = AsyncMock(return_value=_response(404, text="Not Found"))
    assert json.loads(await github_find_branch(token="t", owner="o", repo="r", branch="x")) == _EMPTY

    client.get = AsyncMock()
    result = json.loads(await github_find_branch(token="t", owner="o", repo="r", branch="../main"))
    assert "error" in result
    client.get.assert_not_called()


def _file(text: bytes | str, **extra) -> dict:
    raw = text.encode() if isinstance(text, str) else text
    return {"type": "file", "encoding": "base64", "content": base64.b64encode(raw).decode(), "size": len(raw),
            "name": "a b.md", "path": "docs/a b.md", "sha": "s1", **extra}


@pytest.mark.asyncio
async def test_get_file_내용(client):
    client.get = AsyncMock(return_value=_response(200, _file("안녕\n")))
    result = json.loads(await github_get_file(token="t", owner="o", repo="r", path="docs/a b.md", ref="main"))
    _assert_outputs("github.get_file", result)
    item = result["items"][0]
    assert set(item) == {"name", "path", "type", "sha", "size", "content", "truncated"}
    assert (item["content"], item["truncated"]) == ("안녕\n", False)
    assert client.get.call_args.args[0] == "https://api.github.com/repos/o/r/contents/docs/a%20b.md"
    assert client.get.call_args.kwargs["params"] == {"ref": "main"}


@pytest.mark.asyncio
async def test_get_file_큰_파일은_자른다(client):
    client.get = AsyncMock(return_value=_response(200, _file("가" * 150_000)))
    item = json.loads(await github_get_file(token="t", owner="o", repo="r", path="big.txt"))["items"][0]
    assert (len(item["content"]), item["truncated"]) == (100_000, True)

    client.get = AsyncMock(return_value=_response(200, {**_file(""), "content": "", "size": 2_000_000,
                                                        "encoding": "none"}))
    item = json.loads(await github_get_file(token="t", owner="o", repo="r", path="big.bin"))["items"][0]
    assert (item["content"], item["truncated"]) == ("", True)


@pytest.mark.asyncio
async def test_get_file_바이너리는_빈_내용(client):
    client.get = AsyncMock(return_value=_response(200, _file(b"\xff\xfe\x00")))
    item = json.loads(await github_get_file(token="t", owner="o", repo="r", path="a.bin"))["items"][0]
    assert (item["content"], item["truncated"]) == ("", False)


@pytest.mark.asyncio
async def test_get_file_디렉터리(client):
    entries = [{"type": "file", "name": f"f{i}", "path": f"d/f{i}", "sha": "s", "size": 1} for i in range(1200)]
    client.get = AsyncMock(return_value=_response(200, entries))
    result = json.loads(await github_get_file(token="t", owner="o", repo="r", path="d"))
    _assert_outputs("github.get_file", result)
    assert (len(result["items"]), result["total"]) == (1000, 1000)
    assert result["items"][0] == {"name": "f0", "path": "d/f0", "type": "file", "sha": "s", "size": 1,
                                  "content": "", "truncated": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["../../x", "a//b"])
async def test_get_file_경로_거부(client, path):
    client.get = AsyncMock()
    result = json.loads(await github_get_file(token="t", owner="o", repo="r", path=path))
    assert "error" in result
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_find_repo(client):
    client.get = AsyncMock(return_value=_response(200, {
        "name": "r", "full_name": "o/r", "owner": {"login": "o"}, "private": False, "default_branch": "main",
        "html_url": "https://github.com/o/r", "description": None}))
    result = json.loads(await github_find_repo(token="t", owner="o", repo="r"))
    _assert_outputs("github.find_repo", result)
    assert client.get.call_args.args[0] == "https://api.github.com/repos/o/r"
    assert result["items"] == [{"name": "r", "full_name": "o/r", "owner": "o", "private": False,
                                "default_branch": "main", "url": "https://github.com/o/r", "description": ""}]

    client.get = AsyncMock(return_value=_response(404, text="Not Found"))
    assert json.loads(await github_find_repo(token="t", owner="o", repo="r")) == _EMPTY


@pytest.mark.asyncio
async def test_find_org(client):
    client.get = AsyncMock(return_value=_response(200, {
        "login": "acme", "name": "Acme", "description": "d", "html_url": "https://github.com/acme"}))
    result = json.loads(await github_find_org(token="t", org="acme"))
    _assert_outputs("github.find_org", result)
    assert client.get.call_args.args[0] == "https://api.github.com/orgs/acme"
    assert set(result["items"][0]) == {"login", "name", "description", "url"}

    client.get = AsyncMock(return_value=_response(404, text="Not Found"))
    assert json.loads(await github_find_org(token="t", org="acme")) == _EMPTY


@pytest.mark.asyncio
async def test_find_user(client):
    client.get = AsyncMock(return_value=_response(200, {
        "login": "alice", "name": "Alice", "type": "User", "html_url": "https://github.com/alice"}))
    result = json.loads(await github_find_user(token="t", username="alice"))
    _assert_outputs("github.find_user", result)
    assert client.get.call_args.args[0] == "https://api.github.com/users/alice"
    assert set(result["items"][0]) == {"login", "name", "type", "url"}

    await github_find_user(token="t", username="")
    assert client.get.call_args.args[0] == "https://api.github.com/user"

    client.get = AsyncMock(return_value=_response(404, text="Not Found"))
    assert json.loads(await github_find_user(token="t", username="alice")) == _EMPTY


@pytest.mark.asyncio
async def test_check_org_membership(client):
    client.get = AsyncMock(return_value=_response(204))
    result = json.loads(await github_check_org_membership(token="t", org="acme", username="alice"))
    _assert_outputs("github.check_org_membership", result)
    assert (result["member"], result["found"]) == (True, True)
    assert result["items"] == [{"org": "acme", "username": "alice"}]
    assert client.get.call_args.args[0] == "https://api.github.com/orgs/acme/members/alice"

    client.get = AsyncMock(return_value=_response(404))
    result = json.loads(await github_check_org_membership(token="t", org="acme", username="alice"))
    _assert_outputs("github.check_org_membership", result)
    assert (result["member"], result["message"]) == (False, "멤버가 아닙니다")

    client.get = AsyncMock(side_effect=[_response(302), _response(204)])
    result = json.loads(await github_check_org_membership(token="t", org="acme", username="alice"))
    assert result["member"] is True
    assert client.get.call_args.args[0] == "https://api.github.com/orgs/acme/public_members/alice"


# ---------------------------------------------------------------------------
# github_list_issues — 카탈로그 출력 선언과 반환이 같아야 한다
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_issues_성공(client):
    client.get = AsyncMock(return_value=_response(200, [
        {"number": 3, "title": "첫 이슈", "state": "open", "created_at": "2026-10-01T00:00:00Z",
         "labels": [{"name": "bug"}, {"name": "p1"}]},
        {"number": 4, "title": "PR이라 제외", "state": "open", "created_at": "2026-10-02T00:00:00Z",
         "labels": [], "pull_request": {}},
    ]))

    result = json.loads(await github_list_issues(token="gh-token", owner="o", repo="r"))

    assert_matches_outputs("github.list_issues", result)
    assert result["total"] == 1
    assert result["issues"][0] == {
        "number": 3, "title": "첫 이슈", "state": "open", "labels": ["bug", "p1"],
        "created_at": "2026-10-01T00:00:00Z",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("owner, repo", [("o/../../user", "r"), ("..", "r"), ("o", "r/../x")])
async def test_list_issues_owner_repo에_경로_문자가_있으면_요청하지_않는다(client, owner, repo):
    client.get = AsyncMock()
    result = json.loads(await github_list_issues(token="gh-token", owner=owner, repo=repo))
    assert "error" in result
    client.get.assert_not_called()


# ---------------------------------------------------------------------------
# github_get_pr_files
# ---------------------------------------------------------------------------

def _pr_file(filename: str, patch: str | None = "@@ -1 +1 @@\n-a\n+b") -> dict:
    f = {"filename": filename, "status": "modified", "additions": 1, "deletions": 1, "changes": 2}
    if patch is not None:
        f["patch"] = patch
    return f


def _pr_get(files_pages: list[list[dict]], head_sha: str = "abc123"):
    """GET pulls/{n} → head.sha, GET pulls/{n}/files?page=k → files_pages[k-1](없으면 빈 목록)."""
    async def get(url, **kwargs):
        if url.endswith("/files"):
            page = kwargs["params"]["page"]
            return _response(200, files_pages[page - 1] if page <= len(files_pages) else [])
        return _response(200, {"head": {"sha": head_sha}, "title": "t"})
    return AsyncMock(side_effect=get)


def _files_calls(client) -> list:
    return [c for c in client.get.call_args_list if c.args[0].endswith("/files")]


@pytest.mark.asyncio
async def test_get_pr_files_성공(client):
    client.get = _pr_get([[_pr_file("a.py"), _pr_file("src/b.py")]])

    result = json.loads(await github_get_pr_files(token="gh-token", owner="o", repo="r", pull_number=5))

    assert_matches_outputs("github.get_pr_files", result)
    assert result["found"] is True
    assert result["total"] == 2
    assert all("patch" not in item for item in result["items"])
    assert result["items"][0] == {"filename": "a.py", "status": "modified", "additions": 1, "deletions": 1,
                                  "changes": 2}
    assert "--- a.py\n" in result["diff"] and "--- src/b.py\n" in result["diff"]
    assert result["head_sha"] == "abc123"
    assert result["skipped"] == [] and result["truncated"] is False
    assert client.get.call_args_list[0].args[0] == "https://api.github.com/repos/o/r/pulls/5"
    assert _files_calls(client)[0].args[0] == "https://api.github.com/repos/o/r/pulls/5/files"


@pytest.mark.asyncio
async def test_get_pr_files_기본_제외(client):
    client.get = _pr_get([[_pr_file("web/package-lock.json"), _pr_file("x.lock"), _pr_file("a.py")]])

    result = json.loads(await github_get_pr_files(token="t", owner="o", repo="r", pull_number=1))

    assert result["skipped"] == [
        {"filename": "web/package-lock.json", "reason": "filtered"},
        {"filename": "x.lock", "reason": "filtered"},
    ]
    assert "--- a.py\n" in result["diff"] and "lock" not in result["diff"]


@pytest.mark.asyncio
async def test_get_pr_files_포함_경로(client):
    client.get = _pr_get([[_pr_file("src/a.py"), _pr_file("docs/a.md")]])

    result = json.loads(await github_get_pr_files(
        token="t", owner="o", repo="r", pull_number=1, include_paths="src/**"))

    assert result["skipped"] == [{"filename": "docs/a.md", "reason": "filtered"}]
    assert "--- src/a.py\n" in result["diff"]


@pytest.mark.asyncio
async def test_get_pr_files_max_files(client):
    client.get = _pr_get([[_pr_file("a.py"), _pr_file("b.py"), _pr_file("c.py")]])

    result = json.loads(await github_get_pr_files(token="t", owner="o", repo="r", pull_number=1, max_files=2))

    assert result["skipped"] == [{"filename": "c.py", "reason": "max_files"}]
    assert "--- c.py" not in result["diff"]


@pytest.mark.asyncio
async def test_get_pr_files_max_diff_chars(client):
    big = "@@ -1 +1 @@\n+" + "x" * 600
    client.get = _pr_get([[_pr_file("a.py", big), _pr_file("b.py", big), _pr_file("c.py", "@@ -1 +1 @@\n+y")]])

    result = json.loads(await github_get_pr_files(
        token="t", owner="o", repo="r", pull_number=1, max_diff_chars=1000))

    assert result["skipped"] == [{"filename": "b.py", "reason": "max_diff"}, {"filename": "c.py", "reason": "max_diff"}]
    assert "--- a.py\n" in result["diff"]
    assert len(result["diff"]) <= 1000


@pytest.mark.asyncio
async def test_get_pr_files_patch_없음은_no_patch(client):
    client.get = _pr_get([[_pr_file("img.png", patch=None), _pr_file("a.py")]])

    result = json.loads(await github_get_pr_files(token="t", owner="o", repo="r", pull_number=1))

    assert result["skipped"] == [{"filename": "img.png", "reason": "no_patch"}]
    assert "--- img.png" not in result["diff"]


@pytest.mark.asyncio
async def test_get_pr_files_0개면_found_false(client):
    client.get = _pr_get([[]])

    result = json.loads(await github_get_pr_files(token="t", owner="o", repo="r", pull_number=1))

    assert result == {"success": True, "found": False, "total": 0, "items": [], "diff": "", "head_sha": "abc123",
                      "skipped": [], "truncated": False}


@pytest.mark.asyncio
async def test_get_pr_files_10페이지에서_멈춤(client):
    page = [_pr_file(f"f{i}.py") for i in range(100)]
    client.get = _pr_get([page] * 11)

    result = json.loads(await github_get_pr_files(token="t", owner="o", repo="r", pull_number=1))

    assert len(_files_calls(client)) == 10
    assert result["truncated"] is True
    assert result["total"] == 1000


@pytest.mark.asyncio
async def test_get_pr_files_범위_밖_인자_고정(client):
    big = "@@ -1 +1 @@\n+" + "x" * 2000
    client.get = _pr_get([[_pr_file("a.py"), _pr_file("b.py")]])
    result = json.loads(await github_get_pr_files(token="t", owner="o", repo="r", pull_number=1, max_files=0))
    assert result["skipped"] == [{"filename": "b.py", "reason": "max_files"}]

    client.get = _pr_get([[_pr_file(f"f{i}.py") for i in range(100)] for _ in range(4)])
    result = json.loads(await github_get_pr_files(token="t", owner="o", repo="r", pull_number=1, max_files=999))
    assert [s["reason"] for s in result["skipped"]] == ["max_files"] * 100

    client.get = _pr_get([[_pr_file("a.py", big)]])
    result = json.loads(await github_get_pr_files(
        token="t", owner="o", repo="r", pull_number=1, max_diff_chars=10))
    assert result["skipped"] == [{"filename": "a.py", "reason": "max_diff"}]
    client.get = _pr_get([[_pr_file("a.py", "@@ -1 +1 @@\n+" + "x" * 900)]])
    result = json.loads(await github_get_pr_files(
        token="t", owner="o", repo="r", pull_number=1, max_diff_chars=10))
    assert result["skipped"] == [] and len(result["diff"]) <= 1000


@pytest.mark.asyncio
@pytest.mark.parametrize("owner, repo, pull_number", [
    ("a/b", "r", 1), ("o", "r/../x", 1), ("..", "r", 1), ("o", "r", 0), ("o", "r", -1), ("o", "r", "1/../x"),
    ("o", "r", True),
])
async def test_get_pr_files_owner_형식_오류(client, owner, repo, pull_number):
    client.get = AsyncMock()
    result = json.loads(await github_get_pr_files(token="t", owner=owner, repo=repo, pull_number=pull_number))
    assert "error" in result
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_get_pr_files_api_오류(client):
    client.get = AsyncMock(return_value=_response(404, text='{"message":"Not Found"}'))
    result = json.loads(await github_get_pr_files(token="t", owner="o", repo="r", pull_number=1))
    assert result == {"error": 'GitHub API 오류 (404): {"message":"Not Found"}'}


def test_right_side_lines():
    assert _right_side_lines("@@ -1,2 +1,3 @@\n a\n-b\n+c\n+d") == {1, 2, 3}


@pytest.mark.parametrize("patch, expected", [
    ("@@ -10,2 +20,3 @@\n a\n+b\n c\n@@ -50,1 +60,2 @@ def f():\n x\n+y", {20, 21, 22, 60, 61}),
    ("@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b\n\\ No newline at end of file", {1}),
    ("@@ -3,2 +2,0 @@\n-a\n-b", set()),
    ("@@ -0,0 +1 @@\n+a", {1}),
    ("@@ -1,2 +1,2 @@\n a\n+b\n", {1, 2}),
])
def test_right_side_lines_경계(patch, expected):
    assert _right_side_lines(patch) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("pull_number", ["²", "１", "٣"])
async def test_get_pr_files_비ASCII_숫자는_error(client, pull_number):
    client.get = AsyncMock()
    result = json.loads(await github_get_pr_files(token="t", owner="o", repo="r", pull_number=pull_number))
    assert result == {"error": "pull_number는 양의 정수여야 합니다."}
    client.get.assert_not_called()


# ---------------------------------------------------------------------------
# github_submit_pr_review
# ---------------------------------------------------------------------------

_PATCH = "@@ -1,2 +1,3 @@\n a\n-b\n+c\n+d"  # a.py 오른쪽 줄 {1, 2, 3}


def _review_client(client, *, me: str = "me-login", pr_author: str = "other", files=None):
    """GET user·pulls/{n}·pulls/{n}/files, POST reviews(200)를 흉내 낸다."""
    files = [_pr_file("a.py", _PATCH)] if files is None else files

    async def get(url, **kwargs):
        if url.endswith("/user"):
            return _response(200, {"login": me})
        if url.endswith("/files"):
            return _response(200, files if kwargs["params"]["page"] == 1 else [])
        return _response(200, {"head": {"sha": "abc"}, "user": {"login": pr_author}})

    client.get = AsyncMock(side_effect=get)
    client.post = AsyncMock(return_value=_response(200, {
        "id": 77, "html_url": "https://github.com/o/r/pull/5#pullrequestreview-77", "state": "COMMENTED",
    }))


def _posted(client) -> dict:
    return client.post.call_args.kwargs["json"]


def _get_urls(client) -> list[str]:
    return [c.args[0] for c in client.get.call_args_list]


async def _submit(**kwargs) -> dict:
    return json.loads(await github_submit_pr_review(token="t", owner="o", repo="r", pull_number=5, **kwargs))


@pytest.mark.asyncio
async def test_submit_review_줄코멘트_범위_안(client):
    _review_client(client)

    result = await _submit(body="총평", comments=[{"path": "a.py", "line": 2, "body": "여기"}])

    assert_matches_outputs("github.submit_pr_review", result)
    assert client.post.call_args.args[0] == "https://api.github.com/repos/o/r/pulls/5/reviews"
    assert _posted(client)["comments"][0] == {"path": "a.py", "line": 2, "side": "RIGHT", "body": "여기"}
    assert _posted(client)["event"] == "COMMENT" and _posted(client)["body"] == "총평"
    assert result["posted_comments"] == 1
    assert result == {"success": True, "review_id": 77, "url": "https://github.com/o/r/pull/5#pullrequestreview-77",
                      "state": "COMMENTED", "posted_comments": 1, "moved_comments": 0, "warnings": []}


@pytest.mark.asyncio
async def test_submit_review_범위_밖은_본문으로(client):
    _review_client(client)

    result = await _submit(body="총평", comments=[
        {"path": "없는.py", "line": 1, "body": "파일 없음"},
        {"path": "a.py", "line": 9, "body": "줄 없음"},
    ])

    assert_matches_outputs("github.submit_pr_review", result)
    assert _posted(client)["comments"] == []
    body = _posted(client)["body"]
    assert body.startswith("총평")
    section = body[body.index("다른 줄에 대한 의견"):]
    assert "없는.py:1 — 파일 없음" in section and "a.py:9 — 줄 없음" in section
    assert result["moved_comments"] == 2 and result["posted_comments"] == 0


@pytest.mark.asyncio
async def test_submit_review_comments_펜스_문자열(client):
    _review_client(client)

    result = await _submit(
        body="총평", comments='```json\n[{"path": "a.py", "line": 1, "body": "펜스"}]\n```')

    assert result["posted_comments"] == 1
    assert _posted(client)["comments"][0]["body"] == "펜스"
    assert result["warnings"] == []


@pytest.mark.asyncio
async def test_submit_review_comments_못읽으면_본문만(client):
    _review_client(client)

    result = await _submit(body="총평", comments="이건 JSON 아님")

    assert "error" not in result
    assert _posted(client)["comments"] == []
    assert _posted(client)["body"].endswith("줄 코멘트를 읽지 못했습니다")
    assert len(result["warnings"]) == 1


@pytest.mark.asyncio
async def test_submit_review_형식틀린_항목_버림(client):
    _review_client(client)

    result = await _submit(body="총평", comments=[
        {"path": "a.py", "line": 0, "body": "줄 0"},
        {"path": "a.py", "line": 1, "body": ""},
        {"path": "a.py", "line": 1, "body": "정상"},
    ])

    assert [c["body"] for c in _posted(client)["comments"]] == ["정상"]
    assert len(result["warnings"]) == 1


@pytest.mark.asyncio
async def test_submit_review_50개_초과_버림(client):
    _review_client(client)

    result = await _submit(body="총평", comments=[{"path": "a.py", "line": 1, "body": f"c{i}"} for i in range(55)])

    assert len(_posted(client)["comments"]) == 50
    assert result["posted_comments"] == 50
    assert len(result["warnings"]) == 1


@pytest.mark.asyncio
async def test_submit_review_me_내PR_승인은_코멘트로(client):
    _review_client(client, me="hodoon", pr_author="hodoon")

    result = await _submit(author="me", event="APPROVE", body="좋아요")

    assert "https://api.github.com/user" in _get_urls(client)
    assert _posted(client)["event"] == "COMMENT"
    assert len(result["warnings"]) == 1


@pytest.mark.asyncio
async def test_submit_review_bot은_GET_user_안부름(client):
    _review_client(client)

    await _submit(event="APPROVE", body="좋아요")

    assert "https://api.github.com/user" not in _get_urls(client)
    assert _posted(client)["event"] == "APPROVE"


@pytest.mark.asyncio
async def test_submit_review_본문없고_코멘트없으면_error(client):
    _review_client(client)

    result = await _submit()

    assert result == {"error": "리뷰 내용이 없습니다."}
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_submit_review_본문없고_코멘트있으면_IEUM_리뷰(client):
    _review_client(client)

    await _submit(comments=[{"path": "a.py", "line": 1, "body": "여기"}])

    assert _posted(client)["body"] == "IEUM 리뷰"


@pytest.mark.asyncio
async def test_submit_review_commit_id_전달(client):
    _review_client(client)
    await _submit(body="총평", commit_id="abc123")
    assert _posted(client)["commit_id"] == "abc123"

    _review_client(client)
    await _submit(body="총평")
    assert "commit_id" not in _posted(client)


@pytest.mark.asyncio
async def test_submit_review_event_선택지_밖이면_error(client):
    _review_client(client)

    result = await _submit(event="DISMISS", body="총평")

    assert "error" in result
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_submit_review_본문_상한(client):
    _review_client(client)

    result = await _submit(body="x" * 70000)

    assert len(_posted(client)["body"]) == 65536
    assert len(result["warnings"]) == 1


@pytest.mark.asyncio
async def test_submit_review_본문없고_comments_못읽으면_error(client):
    _review_client(client)

    result = await _submit(body="", comments="JSON 아님")

    assert result == {"error": "리뷰 내용이 없습니다."}
    client.post.assert_not_called()


_REVIEW_OK = {"id": 78, "html_url": "https://github.com/o/r/pull/5#pullrequestreview-78", "state": "COMMENTED"}


@pytest.mark.asyncio
async def test_submit_review_422면_줄코멘트를_본문으로_옮겨_한번_재시도(client):
    _review_client(client)
    client.post = AsyncMock(side_effect=[_response(422, text="line could not be resolved"), _response(200, _REVIEW_OK)])

    result = await _submit(body="총평", comments=[{"path": "a.py", "line": 2, "body": "여기"}])

    assert client.post.call_count == 2
    first, second = (c.kwargs["json"] for c in client.post.call_args_list)
    assert len(first["comments"]) == 1
    assert second["comments"] == []
    assert "다른 줄에 대한 의견" in second["body"] and "a.py:2 — 여기" in second["body"]
    assert result["moved_comments"] == 1 and result["posted_comments"] == 0
    assert result["review_id"] == 78 and len(result["warnings"]) == 1
    assert [u for u in _get_urls(client) if not u.endswith("/files")] == []  # 재시도에 GET pulls/{n} 없음


@pytest.mark.asyncio
async def test_submit_review_재시도도_422면_error(client):
    _review_client(client)
    client.post = AsyncMock(return_value=_response(422, text="nope"))

    result = await _submit(body="총평", comments=[{"path": "a.py", "line": 2, "body": "여기"}])

    assert result == {"error": "GitHub API 오류 (422): nope"}
    assert client.post.call_count == 2


@pytest.mark.asyncio
async def test_submit_review_줄코멘트_없이_422면_재시도_없음(client):
    _review_client(client)
    client.post = AsyncMock(return_value=_response(422, text="nope"))

    result = await _submit(body="총평")

    assert result == {"error": "GitHub API 오류 (422): nope"}
    assert client.post.call_count == 1


@pytest.mark.asyncio
async def test_submit_review_깊게_중첩된_comments는_본문만(client):
    _review_client(client)

    result = await _submit(body="총평", comments="[" * 100000)

    assert "error" not in result
    assert _posted(client)["comments"] == []
    assert _posted(client)["body"].endswith("줄 코멘트를 읽지 못했습니다")


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", ["", None])
async def test_submit_review_author_event_빈값은_기본값(client, empty):
    _review_client(client)

    result = await _submit(author=empty, event=empty, body="총평")

    assert "error" not in result
    assert _posted(client)["event"] == "COMMENT"
    assert "https://api.github.com/user" not in _get_urls(client)


@pytest.mark.asyncio
async def test_submit_review_줄코멘트_본문_상한(client):
    _review_client(client)

    result = await _submit(body="총평", comments=[{"path": "a.py", "line": 1, "body": "y" * 70000}])

    assert len(_posted(client)["comments"][0]["body"]) == 65536
    assert len(result["warnings"]) == 1


@pytest.mark.asyncio
async def test_submit_review_옮긴_코멘트로_본문_상한(client):
    _review_client(client)

    result = await _submit(body="x" * 60000, comments=[
        {"path": "없는.py", "line": 1, "body": "z" * 10000},
    ])

    assert result["moved_comments"] == 1
    assert len(_posted(client)["body"]) <= 65536
    assert any("65536" in w for w in result["warnings"])


@pytest.mark.asyncio
@pytest.mark.parametrize("owner, repo, pull_number", [("a/b", "r", 1), ("o", "r/../x", 1), ("o", "r", 0)])
async def test_submit_review_owner_형식_오류(client, owner, repo, pull_number):
    client.get = AsyncMock()
    client.post = AsyncMock()
    result = json.loads(await github_submit_pr_review(
        token="t", owner=owner, repo=repo, pull_number=pull_number, body="총평"))
    assert "error" in result
    client.get.assert_not_called()
    client.post.assert_not_called()

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.action_outputs import assert_matches_outputs
from tools.github import _right_side_lines, github_create_issue, github_get_pr_files, github_list_issues


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

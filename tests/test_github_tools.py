import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.action_outputs import assert_matches_outputs
from tools.github import (
    _right_side_lines,
    github_create_issue,
    github_get_pr_files,
    github_list_issues,
    github_submit_pr_review,
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

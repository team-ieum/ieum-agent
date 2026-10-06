import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.github import github_create_issue


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

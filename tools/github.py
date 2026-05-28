import json
import httpx
from tools.http_client import get_http_client

_GITHUB_API_BASE = "https://api.github.com"
_TIMEOUT = 30.0


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def github_list_orgs(token: str) -> str:
    """
    인증된 사용자가 속한 GitHub 조직 목록을 조회합니다.

    Args:
        token: GitHub access token

    Returns:
        조직 목록 (login, description)을 포함한 JSON 문자열
    """
    try:
        client = get_http_client()
        response = await client.get(
            f"{_GITHUB_API_BASE}/user/orgs",
            headers=_headers(token),
            params={"per_page": 30},
            timeout=_TIMEOUT,
        )
        if response.status_code != 200:
            return json.dumps(
                {"error": f"GitHub API 오류 ({response.status_code}): {response.text}"},
                ensure_ascii=False,
            )
        orgs = response.json()
        results = [{"login": o["login"], "description": o.get("description") or ""} for o in orgs]
        return json.dumps({"success": True, "orgs": results, "total": len(results)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


async def github_list_issues(token: str, owner: str, repo: str, state: str = "open") -> str:
    """
    GitHub 레포지토리의 이슈 목록을 조회합니다.

    Args:
        token: GitHub access token
        owner: 레포지토리 소유자 (조직 또는 사용자 이름)
        repo: 레포지토리 이름
        state: 이슈 상태 (open | closed | all, 기본값: open)

    Returns:
        이슈 목록 (number, title, state, labels, created_at)을 포함한 JSON 문자열
    """
    try:
        client = get_http_client()
        response = await client.get(
            f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/issues",
            headers=_headers(token),
            params={"per_page": 20, "state": state, "sort": "updated"},
            timeout=_TIMEOUT,
        )
        if response.status_code != 200:
            return json.dumps(
                {"error": f"GitHub API 오류 ({response.status_code}): {response.text}"},
                ensure_ascii=False,
            )
        issues = response.json()
        # GitHub API는 이슈와 PR을 함께 반환 — PR 제외
        results = [
            {
                "number": i["number"],
                "title": i["title"],
                "state": i["state"],
                "labels": [label["name"] for label in i.get("labels", [])],
                "created_at": i["created_at"],
            }
            for i in issues
            if "pull_request" not in i
        ]
        return json.dumps({"success": True, "issues": results, "total": len(results)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


async def github_list_repos(token: str, owner: str = None) -> str:
    """
    GitHub 레포지토리 목록을 조회합니다.

    Args:
        token: GitHub access token
        owner: 조직 또는 사용자 이름 (없으면 인증된 사용자의 전체 레포 조회)

    Returns:
        레포지토리 목록 (name, full_name, owner, description, private)을 포함한 JSON 문자열
    """
    try:
        client = get_http_client()
        if owner:
            response = await client.get(
                f"{_GITHUB_API_BASE}/orgs/{owner}/repos",
                headers=_headers(token),
                params={"per_page": 30, "sort": "updated"},
                timeout=_TIMEOUT,
            )
            if response.status_code == 404:
                response = await client.get(
                    f"{_GITHUB_API_BASE}/users/{owner}/repos",
                    headers=_headers(token),
                    params={"per_page": 30, "sort": "updated"},
                    timeout=_TIMEOUT,
                )
        else:
            response = await client.get(
                f"{_GITHUB_API_BASE}/user/repos",
                headers=_headers(token),
                params={"per_page": 30, "sort": "updated", "affiliation": "owner,organization_member"},
                timeout=_TIMEOUT,
            )

        if response.status_code != 200:
            return json.dumps(
                {"error": f"GitHub API 오류 ({response.status_code}): {response.text}"},
                ensure_ascii=False,
            )
        repos = response.json()
        results = [
            {
                "name": r["name"],
                "full_name": r["full_name"],
                "owner": r["owner"]["login"],
                "description": r.get("description") or "",
                "private": r["private"],
            }
            for r in repos
        ]
        return json.dumps({"success": True, "repos": results, "total": len(results)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


async def github_list_pull_requests(token: str, owner: str, repo: str, state: str = "closed", per_page: int = 30) -> str:
    """
    GitHub 레포지토리의 Pull Request(PR) 목록을 조회합니다.

    Args:
        token: GitHub access token
        owner: 레포지토리 소유자 (조직 또는 사용자 이름)
        repo: 레포지토리 이름
        state: PR 상태 (open | closed | all, 기본값: closed)
        per_page: 페이지당 가져올 개수 (기본값: 30)

    Returns:
        PR 목록 (number, title, state, html_url, merged_at, created_at)을 포함한 JSON 문자열
    """
    try:
        client = get_http_client()
        response = await client.get(
            f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/pulls",
            headers=_headers(token),
            params={"per_page": per_page, "state": state, "sort": "updated"},
            timeout=_TIMEOUT,
        )
        if response.status_code != 200:
            return json.dumps(
                {"error": f"GitHub API 오류 ({response.status_code}): {response.text}"},
                ensure_ascii=False,
            )
        pulls = response.json()
        results = [
            {
                "number": p["number"],
                "title": p["title"],
                "state": p["state"],
                "html_url": p["html_url"],
                "created_at": p["created_at"],
                "merged_at": p.get("merged_at"),
            }
            for p in pulls
        ]
        return json.dumps({"success": True, "pulls": results, "total": len(results)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


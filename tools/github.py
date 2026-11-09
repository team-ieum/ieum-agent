import fnmatch
import json
import re
from pathlib import PurePosixPath

import httpx
from tools.http_client import get_http_client

_GITHUB_API_BASE = "https://api.github.com"
_TIMEOUT = 30.0


# GitHub 요청의 URL 경로 조각. `/`·`..`·공백·`?`가 들어가면 다른 GitHub API 경로로 샌다.
_PATH_NAME = re.compile(r"[A-Za-z0-9_.-]+")


def _is_path_name(value) -> bool:
    return isinstance(value, str) and _PATH_NAME.fullmatch(value) is not None and value.strip(".") != ""


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
    if not (_is_path_name(owner) and _is_path_name(repo)):
        return json.dumps({"error": "owner/repo 형식이 올바르지 않습니다."}, ensure_ascii=False)
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


async def github_create_issue(token: str, owner: str, repo: str, title: str, body: str = "") -> str:
    """
    GitHub 레포지토리에 새 이슈를 만듭니다.

    Args:
        token: GitHub access token
        owner: 레포지토리 소유자 (조직 또는 사용자 이름)
        repo: 레포지토리 이름
        title: 이슈 제목
        body: 이슈 본문 (선택)

    Returns:
        만들어진 이슈의 number, url, title을 포함한 JSON 문자열
    """
    if not (_is_path_name(owner) and _is_path_name(repo)):
        return json.dumps({"error": "owner/repo 형식이 올바르지 않습니다."}, ensure_ascii=False)
    try:
        client = get_http_client()
        response = await client.post(
            f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/issues",
            headers=_headers(token),
            json={"title": title, "body": body},
            timeout=_TIMEOUT,
        )
        if response.status_code != 201:
            return json.dumps(
                {"error": f"GitHub API 오류 ({response.status_code}): {response.text}"},
                ensure_ascii=False,
            )
        issue = response.json()
        return json.dumps(
            {"success": True, "number": issue["number"], "url": issue["html_url"], "title": issue["title"]},
            ensure_ascii=False,
        )
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
    GitHub 레포지토리의 Pull Request(PR) 목록을 조회합니다. PR 목록 조회에는 이 도구를 사용하고
    검색(search) 도구는 인덱싱·날짜 경계로 결과가 누락될 수 있으므로 사용하지 않습니다.

    Args:
        token: GitHub access token
        owner: 레포지토리 소유자 (조직 또는 사용자 이름)
        repo: 레포지토리 이름
        state: PR 상태 (open | closed | all, 기본값: closed)
        per_page: 페이지당 가져올 개수 (기본값: 30, 최대: 30)

    Returns:
        PR 목록 (number, title, state, html_url, merged_at, created_at)을 포함한 JSON 문자열.
        '머지된 PR'을 요구받으면 merged_at이 null이 아닌 항목만, 기간 조건이 있으면 merged_at
        날짜로 직접 필터링해 최종 응답에는 number·title·merged_at·html_url만 간결히 반환하세요.
    """
    try:
        # 대량 조회 시 LLM 요약 단계에서 토큰/처리시간이 폭증해 타임아웃이 발생하므로,
        # LLM이 큰 값을 넘겨도 코드가 강제로 상한을 적용한다.
        per_page = max(1, min(per_page, 30))
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
        return json.dumps(
            {"success": True, "pulls": results, "total": len(results), "truncated": len(results) >= per_page},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


DEFAULT_EXCLUDE_PATHS = "*.lock, package-lock.json, yarn.lock, pnpm-lock.yaml, dist/**, build/**, *.min.js, *.map"
# 결과 크기 상한 — 처음부터 고정(spec 6절). 10페이지 × 100개 = 파일 1000개.
_MAX_FILE_PAGES = 10
_FILE_META_KEYS = ("filename", "status", "additions", "deletions", "changes")
_HUNK_HEADER = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _to_int(value):
    """정수 또는 숫자 문자열(참조식 결과) → int. 아니면 None. bool은 int가 아니다."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _clamp(value, default: int, lo: int, hi: int) -> int:
    n = _to_int(value)
    return max(lo, min(n if n is not None else default, hi))


def _split_patterns(value) -> list[str]:
    return [p.strip() for p in value.split(",") if p.strip()] if isinstance(value, str) else []


def _path_matches(path: str, patterns: list[str]) -> bool:
    """`/`가 없는 패턴은 파일 이름에도 맞춘다(`*.lock`이 `web/x.lock`에 맞게)."""
    name = PurePosixPath(path).name
    return any(fnmatch.fnmatchcase(path, p) or ("/" not in p and fnmatch.fnmatchcase(name, p)) for p in patterns)


def _right_side_lines(patch: str) -> set[int]:
    """patch에서 PR 리뷰 줄 코멘트(side RIGHT)를 달 수 있는 새 파일 줄 번호(추가·문맥 줄)."""
    lines: set[int] = set()
    new_line = None
    for raw in patch.split("\n"):
        header = _HUNK_HEADER.match(raw)
        if header:
            new_line = int(header.group(1))
        elif new_line is None or raw.startswith("-") or raw.startswith("\\"):
            continue
        else:
            lines.add(new_line)
            new_line += 1
    return lines


async def github_get_pr_files(
    token: str,
    owner: str,
    repo: str,
    pull_number: int,
    include_paths: str = "",
    exclude_paths: str = DEFAULT_EXCLUDE_PATHS,
    max_files: int = 50,
    max_diff_chars: int = 60000,
) -> str:
    """
    GitHub PR에서 바뀐 파일 목록과 합친 diff를 가져옵니다.

    Args:
        token: GitHub access token
        owner: 레포지토리 소유자 (조직 또는 사용자 이름)
        repo: 레포지토리 이름
        pull_number: PR 번호
        include_paths: 포함할 경로 glob, 쉼표 구분 (비우면 전부)
        exclude_paths: 제외할 경로 glob, 쉼표 구분
        max_files: diff에 넣을 최대 파일 수 (1~300)
        max_diff_chars: diff 최대 길이 (1000~200000자)

    Returns:
        found, total, items(파일 메타), diff, head_sha, skipped, truncated를 포함한 JSON 문자열
    """
    number = _to_int(pull_number)
    if not (_is_path_name(owner) and _is_path_name(repo)):
        return json.dumps({"error": "owner/repo 형식이 올바르지 않습니다."}, ensure_ascii=False)
    if number is None or number < 1:
        return json.dumps({"error": "pull_number는 양의 정수여야 합니다."}, ensure_ascii=False)
    max_files = _clamp(max_files, 50, 1, 300)
    max_diff_chars = _clamp(max_diff_chars, 60000, 1000, 200000)
    includes = _split_patterns(include_paths)
    excludes = _split_patterns(exclude_paths)
    try:
        client = get_http_client()
        pr_url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}"
        response = await client.get(pr_url, headers=_headers(token), timeout=_TIMEOUT)
        if response.status_code != 200:
            return json.dumps(
                {"error": f"GitHub API 오류 ({response.status_code}): {response.text}"},
                ensure_ascii=False,
            )
        head_sha = response.json()["head"]["sha"]

        files: list[dict] = []
        truncated = False
        for page in range(1, _MAX_FILE_PAGES + 1):
            response = await client.get(
                f"{pr_url}/files",
                headers=_headers(token),
                params={"per_page": 100, "page": page},
                timeout=_TIMEOUT,
            )
            if response.status_code != 200:
                return json.dumps(
                    {"error": f"GitHub API 오류 ({response.status_code}): {response.text}"},
                    ensure_ascii=False,
                )
            batch = response.json()
            files.extend(batch)
            if len(batch) < 100:
                break
        else:
            truncated = True

        # 처리 순서: 경로 필터 → max_files → diff 누적(넘친 파일부터 max_diff).
        skipped: list[dict] = []
        diff = ""
        taken = 0
        overflow = False
        for f in files:
            path = f["filename"]
            if (includes and not _path_matches(path, includes)) or _path_matches(path, excludes):
                reason = "filtered"
            elif taken >= max_files:
                reason = "max_files"
            else:
                taken += 1
                patch = f.get("patch")
                piece = f"--- {path}\n{patch}\n" if patch is not None else ""
                if patch is None:
                    reason = "no_patch"
                elif overflow or len(diff) + len(piece) > max_diff_chars:
                    overflow = True
                    reason = "max_diff"
                else:
                    diff += piece
                    continue
            skipped.append({"filename": path, "reason": reason})

        items = [{k: f.get(k) for k in _FILE_META_KEYS} for f in files]
        return json.dumps(
            {
                "success": True,
                "found": bool(items),
                "total": len(items),
                "items": items,
                "diff": diff,
                "head_sha": head_sha,
                "skipped": skipped,
                "truncated": truncated,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


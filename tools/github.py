import base64
import fnmatch
import json
import re
from pathlib import PurePosixPath
from urllib.parse import quote

import httpx
from tools.http_client import get_http_client

_GITHUB_API_BASE = "https://api.github.com"
_TIMEOUT = 30.0


# GitHub 요청의 URL 경로 조각. `/`·`..`·공백·`?`가 들어가면 다른 GitHub API 경로로 샌다.
_PATH_NAME = re.compile(r"[A-Za-z0-9_.-]+")
# GitHub login(사용자·조직). BE `GitHubReposOptionSource.OWNER`와 같다.
_LOGIN = re.compile(r"[A-Za-z0-9_-]{1,39}")
_CONTROL_OR_BACKSLASH = re.compile(r"[\x00-\x1f\x7f\\]")
_MAX_REF_PATH = 1024


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
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        return int(value.strip())
    return None


_MAX_ITEMS = 100


def _issue_no(value) -> int | None:
    """이슈·PR 번호 칸. 사람이 친 `#12`·앞뒤 공백은 12로 받는다. 1 미만·숫자 아님은 None."""
    if isinstance(value, str):
        value = value.strip()
        value = value[1:] if value.startswith("#") else value
    n = _to_int(value)
    return n if n is not None and n >= 1 else None


def _ref_path(value) -> str | None:
    """브랜치·파일 경로 → URL 경로 조각. `/`는 두고 세그먼트마다 인코딩해 다른 API 경로로 새지 않게 한다."""
    # 제어 문자는 strip 전에 본다 — 앞뒤 `\n`도 거부.
    if not isinstance(value, str) or _CONTROL_OR_BACKSLASH.search(value):
        return None
    value = value.strip()
    if not value or len(value) > _MAX_REF_PATH:
        return None
    segments = value.split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        return None
    return "/".join(quote(seg, safe="") for seg in segments)


def _is_login(value) -> bool:
    return isinstance(value, str) and _LOGIN.fullmatch(value) is not None


def _head_ref(value) -> str | None:
    """PR head/base. `user:branch`(포크) 또는 브랜치. JSON·쿼리로 보내므로 인코딩 전 원문을 돌려준다."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    user, sep, branch = value.partition(":")
    if sep:
        # 원문을 그대로 보내므로 branch 앞뒤 공백·두 번째 `:`가 있으면 거부한다.
        ok = _is_login(user) and branch == branch.strip() and ":" not in branch and _ref_path(branch) is not None
        return value if ok else None
    return value if _ref_path(value) is not None else None


def _csv(value) -> list[str]:
    """쉼표 구분 문자열(라벨·담당자·리뷰어) → 순서 유지 중복 제거 목록."""
    if not isinstance(value, str):
        return []
    return list(dict.fromkeys(p.strip() for p in value.split(",") if p.strip()))


def _found(items: list, message: str | None = None, **extra) -> str:
    """Search 공통 출력. 0건은 에러가 아니다."""
    if message is None:
        message = "" if items else "결과가 없습니다"
    return json.dumps(
        {"success": True, "found": bool(items), "total": len(items), "items": items, "message": message, **extra},
        ensure_ascii=False,
    )


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
        elif new_line is not None and raw[:1] in ("+", " "):
            lines.add(new_line)
            new_line += 1
    return lines


async def _fetch_pages(client, token: str, url: str):
    """목록 API를 100개씩 최대 10페이지. (items, truncated, error JSON 또는 None)."""
    files: list[dict] = []
    for page in range(1, _MAX_FILE_PAGES + 1):
        response = await client.get(
            url,
            headers=_headers(token),
            params={"per_page": 100, "page": page},
            timeout=_TIMEOUT,
        )
        if response.status_code != 200:
            return files, False, json.dumps(
                {"error": f"GitHub API 오류 ({response.status_code}): {response.text}"},
                ensure_ascii=False,
            )
        batch = response.json()
        files.extend(batch)
        if len(batch) < 100:
            return files, False, None
    return files, True, None


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

        files, truncated, error = await _fetch_pages(client, token, f"{pr_url}/files")
        if error:
            return error

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


_REVIEW_EVENTS = ("COMMENT", "APPROVE", "REQUEST_CHANGES")
_MAX_REVIEW_COMMENTS = 50
_MAX_REVIEW_BODY = 65536
_CODE_FENCE = re.compile(r"```[A-Za-z]*")


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _api_error(response) -> str:
    message = f"GitHub API 오류 ({response.status_code}): {response.text}"
    if response.status_code == 403:
        message += " — GitHub App 새 권한을 승인했는지 확인해 주세요."
    return _error(message)


def _parse_comments(value):
    """줄 코멘트 목록. AI 출력은 JSON 문자열에 코드 펜스·설명이 붙어 오기 쉽다. 못 읽으면 None."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return None
    # RecursionError: `[`를 깊게 중첩한 입력.
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, RecursionError):
        text = _CODE_FENCE.sub("", value)
        start, end = text.find("["), text.rfind("]")
        try:
            parsed = json.loads(text[start:end + 1]) if 0 <= start < end else None
        except (json.JSONDecodeError, RecursionError):
            parsed = None
    return parsed if isinstance(parsed, list) else None


def _valid_comment(c) -> dict | None:
    if not isinstance(c, dict):
        return None
    path, line, body = c.get("path"), _to_int(c.get("line")), c.get("body")
    if not (isinstance(path, str) and path and line and line > 0 and isinstance(body, str) and body.strip()):
        return None
    return {"path": path, "line": line, "body": body}


async def github_submit_pr_review(
    token: str,
    owner: str,
    repo: str,
    pull_number: int,
    author: str = "bot",
    event: str = "COMMENT",
    body: str = "",
    comments=None,
    commit_id: str = "",
) -> str:
    """
    GitHub PR에 리뷰(본문·줄 코멘트)를 제출합니다. 되돌릴 수 없는 쓰기라 ACTION 노드에서만 씁니다.

    Args:
        token: GitHub access token (author=bot이면 BE가 봇 설치 토큰을 싣는다)
        owner: 레포지토리 소유자 (조직 또는 사용자 이름)
        repo: 레포지토리 이름
        pull_number: PR 번호
        author: 리뷰 작성자 (bot | me, 기본값: bot)
        event: 리뷰 결과 (COMMENT | APPROVE | REQUEST_CHANGES, 기본값: COMMENT)
        body: 리뷰 본문
        comments: 줄 코멘트 목록 [{path, line, body}] 또는 그 JSON 문자열
        commit_id: 리뷰할 커밋 SHA (비우면 최신 head)

    Returns:
        review_id, url, state, posted_comments, moved_comments, warnings를 포함한 JSON 문자열
    """
    number = _to_int(pull_number)
    if not (_is_path_name(owner) and _is_path_name(repo)):
        return _error("owner/repo 형식이 올바르지 않습니다.")
    if number is None or number < 1:
        return _error("pull_number는 양의 정수여야 합니다.")
    author = author or "bot"  # 빈 문자열·None은 기본값(BE도 author 없음을 봇으로 본다)
    event = event or "COMMENT"
    if author not in ("bot", "me"):
        return _error("author는 bot 또는 me여야 합니다.")
    if event not in _REVIEW_EVENTS:
        return _error(f"event는 {', '.join(_REVIEW_EVENTS)} 중 하나여야 합니다.")
    body = body if isinstance(body, str) else ""
    warnings: list[str] = []

    # 0~1. 줄 코멘트 파싱·검사
    parsed = _parse_comments(comments)
    unreadable = parsed is None
    if unreadable:
        warnings.append("줄 코멘트를 읽지 못해 본문만 제출했습니다.")
        parsed = []
    valid = [v for v in map(_valid_comment, parsed) if v]
    if len(valid) < len(parsed):
        warnings.append(f"형식이 틀린 줄 코멘트 {len(parsed) - len(valid)}개를 버렸습니다.")
    if len(valid) > _MAX_REVIEW_COMMENTS:
        warnings.append(f"줄 코멘트는 {_MAX_REVIEW_COMMENTS}개까지라 {len(valid) - _MAX_REVIEW_COMMENTS}개를 버렸습니다.")
        valid = valid[:_MAX_REVIEW_COMMENTS]
    long_bodies = [c for c in valid if len(c["body"]) > _MAX_REVIEW_BODY]
    if long_bodies:
        warnings.append(f"{_MAX_REVIEW_BODY}자를 넘는 줄 코멘트 {len(long_bodies)}개를 잘랐습니다.")
        for c in long_bodies:
            c["body"] = c["body"][:_MAX_REVIEW_BODY]

    try:
        client = get_http_client()
        pr_url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}"

        # 2. patch 오른쪽 줄 밖 코멘트는 본문으로 — 하나라도 범위 밖이면 리뷰 전체가 422다.
        inline, moved = [], []
        if valid:
            files, _, error = await _fetch_pages(client, token, f"{pr_url}/files")
            if error:
                return error
            paths = {c["path"] for c in valid}
            ranges = {f["filename"]: _right_side_lines(f["patch"])
                      for f in files if f.get("patch") and f["filename"] in paths}
            for c in valid:
                (inline if c["line"] in ranges.get(c["path"], set()) else moved).append(c)

        # 3. 자기 PR은 승인·변경 요청을 못 한다(422) → 코멘트로 낮춘다.
        if author == "me" and event != "COMMENT":
            me = await client.get(f"{_GITHUB_API_BASE}/user", headers=_headers(token), timeout=_TIMEOUT)
            if me.status_code != 200:
                return _api_error(me)
            pr = await client.get(pr_url, headers=_headers(token), timeout=_TIMEOUT)
            if pr.status_code != 200:
                return _api_error(pr)
            if me.json().get("login") == (pr.json().get("user") or {}).get("login"):
                warnings.append(f"자기 PR에는 {event}를 할 수 없어 코멘트로 제출했습니다.")
                event = "COMMENT"

        # 4. 본문 없는 COMMENT·REQUEST_CHANGES는 GitHub가 거절한다. 안내 문구만 있는 빈 리뷰도 내지 않는다.
        if not body.strip() and not moved and not inline and event != "APPROVE":
            return _error("리뷰 내용이 없습니다.")

        def review_body() -> str:
            sections = [body] if body.strip() else []
            if moved:
                sections.append("### 다른 줄에 대한 의견\n" + "\n".join(
                    f"- {c['path']}:{c['line']} — {c['body']}" for c in moved))
            if not sections and event != "APPROVE":
                sections.append("IEUM 리뷰")
            if unreadable:
                sections.append("줄 코멘트를 읽지 못했습니다")
            return "\n\n".join(sections)

        async def post_review():
            # 5. 본문 상한 / 6. 제출
            text = review_body()
            payload = {
                "event": event,
                "body": text[:_MAX_REVIEW_BODY],
                "comments": [{"path": c["path"], "line": c["line"], "side": "RIGHT", "body": c["body"]}
                             for c in inline],
            }
            if isinstance(commit_id, str) and commit_id.strip():
                payload["commit_id"] = commit_id.strip()
            response = await client.post(f"{pr_url}/reviews", headers=_headers(token), json=payload,
                                         timeout=_TIMEOUT)
            return response, len(text) > _MAX_REVIEW_BODY

        response, cut = await post_review()
        # commit_id가 최신 head가 아니면 줄 범위가 다른 diff 기준이라 422가 날 수 있다 → 줄 코멘트를 본문으로 옮겨 한 번만 다시.
        if response.status_code == 422 and inline:
            warnings.append(f"GitHub가 줄 코멘트를 받지 않아 {len(inline)}개를 본문으로 옮겨 다시 제출했습니다.")
            moved, inline = moved + inline, []
            response, cut = await post_review()
        if response.status_code != 200:
            return _api_error(response)
        if cut:
            warnings.append(f"본문이 {_MAX_REVIEW_BODY}자를 넘어 잘랐습니다.")
        review = response.json()
        return json.dumps(
            {
                "success": True,
                "review_id": review["id"],
                "url": review["html_url"],
                "state": review["state"],
                "posted_comments": len(inline),
                "moved_comments": len(moved),
                "warnings": warnings,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return _error(str(e))


# ---------------------------------------------------------------------------
# 이슈 묶음 — create_issue 보강·find_issue·update_issue·add_labels·create_comment·find_or_create_issue
# ---------------------------------------------------------------------------

_ISSUE_SEARCH_BY = ("number", "label", "assignee", "author", "title", "body")
_ISSUE_STATES = ("open", "closed", "all")
# 목록 API(`GET repos/{o}/{r}/issues`)로 찾는 search_by → 쿼리 이름.
_ISSUE_LIST_PARAM = {"label": "labels", "assignee": "assignee", "author": "creator"}
_MAX_SEARCH_VALUE = 200
_STATE_REASONS = ("completed", "not_planned", "duplicate", "reopened")
_MAX_COMMENT_BODY = 65536
_ISSUE_NUMBER_ERROR = "issue_number는 양의 정수여야 합니다."


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _issue_fields(labels, assignees, milestone) -> tuple[dict, str | None]:
    """비어 있지 않은 라벨·담당자·마일스톤만. 라벨·담당자는 보내면 통째 교체라 빈 값은 넣지 않는다."""
    fields: dict = {}
    if names := _csv(labels):
        fields["labels"] = names
    if logins := _csv(assignees):
        fields["assignees"] = logins
    if milestone is not None and str(milestone).strip():
        number = _issue_no(milestone)
        if number is None:
            return {}, _error("milestone은 양의 정수여야 합니다.")
        fields["milestone"] = number
    return fields, None


def _dropped(sent: dict, issue: dict) -> list[str]:
    """보냈는데 응답에 없는 라벨·담당자·마일스톤(권한 없음·없는 이름은 GitHub가 조용히 버린다)."""
    warnings = []
    got_labels = {str(label.get("name", "")).lower() for label in issue.get("labels") or []}
    if missing := [n for n in sent.get("labels", []) if n.lower() not in got_labels]:
        warnings.append("반영되지 않은 라벨: " + ", ".join(missing))
    got_logins = {str(a.get("login", "")).lower() for a in issue.get("assignees") or []}
    if missing := [n for n in sent.get("assignees", []) if n.lower() not in got_logins]:
        warnings.append("반영되지 않은 담당자: " + ", ".join(missing))
    if "milestone" in sent and (issue.get("milestone") or {}).get("number") != sent["milestone"]:
        warnings.append(f"반영되지 않은 마일스톤: {sent['milestone']}")
    return warnings


def _same_title(a, b) -> bool:
    """앞뒤 공백 정리·연속 공백 하나로·대소문자 무시."""
    def norm(s) -> str:
        return " ".join(str(s or "").split()).casefold()
    return norm(a) == norm(b)


def _issue_item(issue: dict) -> dict:
    return {
        "number": issue["number"],
        "title": issue.get("title"),
        "state": issue.get("state"),
        "url": issue.get("html_url"),
        "labels": [label.get("name") for label in issue.get("labels") or []],
        "assignees": [a.get("login") for a in issue.get("assignees") or []],
        "author": (issue.get("user") or {}).get("login"),
        "created_at": issue.get("created_at"),
    }


def _search_hits(body: dict, owner: str, repo: str, pulls: bool) -> list[dict]:
    """검색 결과 중 대상 레포·종류만. 검색어에 끼운 `repo:`·`is:pr` 한정자로 다른 결과가 섞일 수 있다."""
    repo_url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}".lower()
    return [
        item for item in body.get("items") or []
        if str(item.get("repository_url", "")).lower() == repo_url and ("pull_request" in item) == pulls
    ]


async def _create_issue(client, token: str, owner: str, repo: str, payload: dict) -> tuple[dict | None, str | None]:
    response = await client.post(
        f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/issues", headers=_headers(token), json=payload, timeout=_TIMEOUT)
    if response.status_code != 201:
        return None, _api_error(response)
    return response.json(), None


async def _find_issues(client, token, owner, repo, search_by, value, state, limit) -> tuple[list[dict], str | None]:
    """(items, error JSON). 입력 검사는 HTTP 호출 전에 끝낸다. 404는 0건."""
    if search_by not in _ISSUE_SEARCH_BY:
        return [], _error(f"search_by는 {', '.join(_ISSUE_SEARCH_BY)} 중 하나여야 합니다.")
    state = _text(state) or "open"
    if state not in _ISSUE_STATES:
        return [], _error("state는 open, closed, all 중 하나여야 합니다.")
    # 번호는 참조식 결과로 int가 올 수 있다.
    value = str(value) if isinstance(value, int) and not isinstance(value, bool) else _text(value)
    if not value:
        return [], _error("value를 입력해 주세요.")
    repo_api = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}"

    if search_by == "number":
        number = _issue_no(value)
        if number is None:
            return [], _error("이슈 번호는 양의 정수여야 합니다.")
        response = await client.get(f"{repo_api}/issues/{number}", headers=_headers(token), timeout=_TIMEOUT)
        if response.status_code == 404:
            return [], None
        if response.status_code != 200:
            return [], _api_error(response)
        issue = response.json()
        return ([] if "pull_request" in issue else [_issue_item(issue)]), None

    if search_by in _ISSUE_LIST_PARAM:
        if search_by != "label" and not _is_login(value):
            return [], _error("GitHub 사용자 이름 형식이 올바르지 않습니다.")
        items: list[dict] = []
        for page in range(1, _MAX_FILE_PAGES + 1):
            response = await client.get(
                f"{repo_api}/issues",
                headers=_headers(token),
                params={_ISSUE_LIST_PARAM[search_by]: value, "state": state, "per_page": 100, "page": page},
                timeout=_TIMEOUT,
            )
            if response.status_code == 404:
                return [], None
            if response.status_code != 200:
                return [], _api_error(response)
            batch = response.json()
            items.extend(_issue_item(i) for i in batch if "pull_request" not in i)
            if len(items) >= limit or len(batch) < 100:
                break
        return items[:limit], None

    if len(value) > _MAX_SEARCH_VALUE:
        return [], _error(f"검색어는 {_MAX_SEARCH_VALUE}자 이하여야 합니다.")
    state_q = "" if state == "all" else f" state:{state}"
    hits, error = await _search(
        client, token, owner, repo, f"{value} in:{search_by} repo:{owner}/{repo} is:issue{state_q}", limit, pulls=False)
    return [_issue_item(i) for i in hits][:limit], error


async def _search(client, token, owner, repo, q: str, limit: int, pulls: bool) -> tuple[list[dict], str | None]:
    """`GET search/issues` 1페이지 → 대상 레포·종류만 남긴 원본 항목. 404는 0건."""
    response = await client.get(
        f"{_GITHUB_API_BASE}/search/issues", headers=_headers(token), params={"q": q, "per_page": limit},
        timeout=_TIMEOUT,
    )
    if response.status_code == 404:
        return [], None
    if response.status_code != 200:
        return [], _api_error(response)
    return _search_hits(response.json(), owner, repo, pulls=pulls), None


async def github_find_issue(
    token: str, owner: str, repo: str, search_by: str, value: str, state: str = "open", max_results: int = 20,
) -> str:
    """
    GitHub 레포지토리에서 이슈를 찾습니다.

    Args:
        token: GitHub access token
        owner: 레포지토리 소유자 (조직 또는 사용자 이름)
        repo: 레포지토리 이름
        search_by: 찾는 기준 (number | label | assignee | author | title | body)
        value: 찾을 값
        state: 이슈 상태 (open | closed | all, 기본값: open)
        max_results: 최대 결과 수 (1~100)

    Returns:
        found, total, items, message를 포함한 JSON 문자열
    """
    if not (_is_path_name(owner) and _is_path_name(repo)):
        return _error("owner/repo 형식이 올바르지 않습니다.")
    try:
        items, error = await _find_issues(
            get_http_client(), token, owner, repo, search_by, value, state, _clamp(max_results, 20, 1, _MAX_ITEMS))
        return error or _found(items)
    except Exception as e:
        return _error(str(e))


async def github_update_issue(
    token: str,
    owner: str,
    repo: str,
    issue_number: str,
    title: str = "",
    body: str = "",
    state: str = "",
    state_reason: str = "",
    labels: str = "",
    assignees: str = "",
    milestone: str = "",
) -> str:
    """
    GitHub 이슈를 수정합니다. 비어 있는 칸은 바꾸지 않습니다.

    Args:
        token: GitHub access token
        owner: 레포지토리 소유자 (조직 또는 사용자 이름)
        repo: 레포지토리 이름
        issue_number: 이슈 번호
        title: 새 제목 (선택)
        body: 새 본문 (선택)
        state: 상태 (open | closed, 선택)
        state_reason: 닫는 이유 (completed | not_planned | duplicate | reopened, 선택)
        labels: 라벨, 쉼표 구분 — 기존 라벨을 통째로 바꿉니다 (선택)
        assignees: 담당자 login, 쉼표 구분 — 기존 담당자를 통째로 바꿉니다 (선택)
        milestone: 마일스톤 번호 (선택)

    Returns:
        number, url, title, state, warnings를 포함한 JSON 문자열
    """
    if not (_is_path_name(owner) and _is_path_name(repo)):
        return _error("owner/repo 형식이 올바르지 않습니다.")
    number = _issue_no(issue_number)
    if number is None:
        return _error(_ISSUE_NUMBER_ERROR)
    fields, error = _issue_fields(labels, assignees, milestone)
    if error:
        return error
    state, state_reason = _text(state), _text(state_reason)
    if state and state not in ("open", "closed"):
        return _error("state는 open, closed 중 하나여야 합니다.")
    if state_reason and state_reason not in _STATE_REASONS:
        return _error(f"state_reason은 {', '.join(_STATE_REASONS)} 중 하나여야 합니다.")

    payload = {key: value for key, value in (("title", title), ("body", body)) if _text(value)}
    warnings = []
    if state:
        payload["state"] = state
        if state_reason:
            payload["state_reason"] = state_reason
    elif state_reason:
        warnings.append("닫는 이유는 상태를 바꿀 때만 적용돼요.")
    payload.update(fields)
    if not payload:
        return _error("바꿀 칸이 없습니다.")
    try:
        response = await get_http_client().patch(
            f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{number}",
            headers=_headers(token), json=payload, timeout=_TIMEOUT,
        )
        if response.status_code != 200:
            return _api_error(response)
        issue = response.json()
        return json.dumps(
            {"success": True, "number": issue["number"], "url": issue["html_url"], "title": issue["title"],
             "state": issue["state"], "warnings": warnings + _dropped(fields, issue)},
            ensure_ascii=False,
        )
    except Exception as e:
        return _error(str(e))


async def github_add_labels(token: str, owner: str, repo: str, issue_number: str, labels: str) -> str:
    """
    GitHub 이슈·PR에 라벨을 더합니다(기존 라벨은 그대로).

    Args:
        token: GitHub access token
        owner: 레포지토리 소유자 (조직 또는 사용자 이름)
        repo: 레포지토리 이름
        issue_number: 이슈 또는 PR 번호
        labels: 더할 라벨, 쉼표 구분

    Returns:
        number, labels(더한 뒤 전체 라벨 이름)를 포함한 JSON 문자열
    """
    if not (_is_path_name(owner) and _is_path_name(repo)):
        return _error("owner/repo 형식이 올바르지 않습니다.")
    number = _issue_no(issue_number)
    if number is None:
        return _error(_ISSUE_NUMBER_ERROR)
    names = _csv(labels)
    if not names:
        return _error("labels를 입력해 주세요.")
    try:
        response = await get_http_client().post(
            f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{number}/labels",
            headers=_headers(token), json={"labels": names}, timeout=_TIMEOUT,
        )
        if response.status_code != 200:
            return _api_error(response)
        return json.dumps(
            {"success": True, "number": number, "labels": [label.get("name") for label in response.json()]},
            ensure_ascii=False,
        )
    except Exception as e:
        return _error(str(e))


async def github_create_comment(token: str, owner: str, repo: str, issue_number: str, body: str) -> str:
    """
    GitHub 이슈·PR에 코멘트를 답니다.

    Args:
        token: GitHub access token
        owner: 레포지토리 소유자 (조직 또는 사용자 이름)
        repo: 레포지토리 이름
        issue_number: 이슈 또는 PR 번호
        body: 코멘트 내용

    Returns:
        id, url을 포함한 JSON 문자열
    """
    if not (_is_path_name(owner) and _is_path_name(repo)):
        return _error("owner/repo 형식이 올바르지 않습니다.")
    number = _issue_no(issue_number)
    if number is None:
        return _error(_ISSUE_NUMBER_ERROR)
    if not _text(body):
        return _error("body를 입력해 주세요.")
    if len(body) > _MAX_COMMENT_BODY:
        return _error(f"코멘트는 {_MAX_COMMENT_BODY}자 이하여야 합니다.")
    try:
        response = await get_http_client().post(
            f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{number}/comments",
            headers=_headers(token), json={"body": body}, timeout=_TIMEOUT,
        )
        if response.status_code != 201:
            return _api_error(response)
        comment = response.json()
        return json.dumps({"success": True, "id": comment["id"], "url": comment["html_url"]}, ensure_ascii=False)
    except Exception as e:
        return _error(str(e))


async def github_find_or_create_issue(
    token: str,
    owner: str,
    repo: str,
    search_by: str,
    value: str,
    title: str,
    body: str = "",
    labels: str = "",
    assignees: str = "",
    milestone: str = "",
    state: str = "open",
) -> str:
    """
    GitHub 이슈를 찾고, 없으면 새로 만듭니다.

    Args:
        token: GitHub access token
        owner: 레포지토리 소유자 (조직 또는 사용자 이름)
        repo: 레포지토리 이름
        search_by: 찾는 기준 (number | label | assignee | author | title | body)
        value: 찾을 값
        title: 없을 때 만들 이슈 제목
        body: 없을 때 만들 이슈 본문 (선택)
        labels: 없을 때 붙일 라벨, 쉼표 구분 (선택)
        assignees: 없을 때 지정할 담당자 login, 쉼표 구분 (선택)
        milestone: 없을 때 지정할 마일스톤 번호 (선택)
        state: 찾을 이슈 상태 (open | closed | all, 기본값: open)

    Returns:
        created, number, url, title, state, warnings를 포함한 JSON 문자열
    """
    if not (_is_path_name(owner) and _is_path_name(repo)):
        return _error("owner/repo 형식이 올바르지 않습니다.")
    fields, error = _issue_fields(labels, assignees, milestone)
    if error:
        return error
    if not _text(title):
        return _error("title을 입력해 주세요.")
    try:
        client = get_http_client()
        # 제목 검색(`in:title`)은 토큰 일치라 부분 일치 이슈가 걸린다 → 여러 건 받아 제목 완전 일치만 남긴다.
        by_title = search_by == "title"
        items, error = await _find_issues(
            client, token, owner, repo, search_by, value, state, _MAX_ITEMS if by_title else 1)
        if error:
            return error
        if by_title:
            items = [i for i in items if _same_title(i["title"], value)]
        if items:
            found = items[0]
            return json.dumps(
                {"success": True, "created": False, "number": found["number"], "url": found["url"],
                 "title": found["title"], "state": found["state"], "warnings": []},
                ensure_ascii=False,
            )
        issue, error = await _create_issue(client, token, owner, repo, {"title": title, "body": body, **fields})
        if error:
            return error
        return json.dumps(
            {"success": True, "created": True, "number": issue["number"], "url": issue["html_url"],
             "title": issue["title"], "state": issue.get("state", "open"), "warnings": _dropped(fields, issue)},
            ensure_ascii=False,
        )
    except Exception as e:
        return _error(str(e))


# ---------------------------------------------------------------------------
# PR 묶음 — create·update·request_reviewers·find·find_or_create·get_pr_reviews
# ---------------------------------------------------------------------------

_PR_SEARCH_BY = ("number", "branch", "title", "body", "author")
_PR_NUMBER_ERROR = "pull_number는 양의 정수여야 합니다."
_REVIEW_STATES = ("APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED")
_AUTHOR_TYPES = ("human", "bot", "all")
# get_pr_reviews 출력 상한(2026-11-09 최종 리뷰 결정) — BE 응답 상한(4MB) 안에 들고 뒤 AI 노드 컨텍스트로 들어간다.
_REVIEW_DECISIONS = ("APPROVED", "CHANGES_REQUESTED")
_MAX_REVIEWS_OUT = 50
_MAX_REVIEW_TEXT = 2_000
_MAX_REVIEW_COMMENTS_OUT = 100
_MAX_COMMENT_TEXT = 1_000
_MAX_REVIEW_TEXT_TOTAL = 150_000


def _pr_item(pr: dict) -> dict:
    """검색 응답에도 있는 키만(head·base는 검색 응답에 없다)."""
    return {
        "number": pr["number"],
        "title": pr.get("title"),
        "state": pr.get("state"),
        "url": pr.get("html_url"),
        "draft": bool(pr.get("draft")),
        "author": (pr.get("user") or {}).get("login"),
        "created_at": pr.get("created_at"),
    }


def _pr_out(pr: dict) -> dict:
    return {"number": pr["number"], "url": pr["html_url"], "title": pr["title"], "state": pr["state"],
            "draft": bool(pr.get("draft"))}


def _head_query(owner: str, head: str) -> str:
    """PR 목록 API의 head 필터는 `owner:branch` 형식이어야 한다."""
    return head if ":" in head else f"{owner}:{head}"


def _base_ref(value) -> str | None:
    base = _head_ref(value)
    return base if base is not None and ":" not in base else None


def _pr_refs(head, base) -> tuple[str | None, str | None, str | None]:
    """(head, base, error JSON)."""
    head = _head_ref(head)
    if head is None:
        return None, None, _error("head 브랜치 형식이 올바르지 않습니다.")
    base = _base_ref(base)
    if base is None:
        return None, None, _error("base 브랜치 형식이 올바르지 않습니다.")
    return head, base, None


async def _create_pr(client, token: str, owner: str, repo: str, payload: dict) -> tuple[dict | None, str | None]:
    response = await client.post(
        f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/pulls", headers=_headers(token), json=payload, timeout=_TIMEOUT)
    if response.status_code != 201:
        return None, _api_error(response)
    return response.json(), None


async def github_create_pull_request(
    token: str,
    owner: str,
    repo: str,
    title: str,
    head: str,
    base: str,
    body: str = "",
    draft: bool = False,
    maintainer_can_modify: bool = True,
) -> str:
    """
    GitHub PR을 만듭니다.

    Args:
        token: GitHub access token
        owner: 레포지토리 소유자 (조직 또는 사용자 이름)
        repo: 레포지토리 이름
        title: PR 제목
        head: 변경을 담은 브랜치 (포크면 user:branch)
        base: 합칠 대상 브랜치
        body: PR 본문 (선택)
        draft: 초안 PR로 만들지 여부
        maintainer_can_modify: 메인테이너가 head 브랜치를 수정할 수 있게 할지 여부

    Returns:
        number, url, title, state, draft를 포함한 JSON 문자열
    """
    if not (_is_path_name(owner) and _is_path_name(repo)):
        return _error("owner/repo 형식이 올바르지 않습니다.")
    head, base, error = _pr_refs(head, base)
    if error:
        return error
    if not _text(title):
        return _error("title을 입력해 주세요.")
    payload = {"title": title, "head": head, "base": base, "body": body or "", "draft": bool(draft),
               "maintainer_can_modify": bool(maintainer_can_modify)}
    try:
        pr, error = await _create_pr(get_http_client(), token, owner, repo, payload)
        return error or json.dumps({"success": True, **_pr_out(pr)}, ensure_ascii=False)
    except Exception as e:
        return _error(str(e))


async def github_update_pull_request(
    token: str,
    owner: str,
    repo: str,
    pull_number: str,
    title: str = "",
    body: str = "",
    state: str = "",
    base: str = "",
) -> str:
    """
    GitHub PR을 수정합니다. 비어 있는 칸은 바꾸지 않습니다.

    Args:
        token: GitHub access token
        owner: 레포지토리 소유자 (조직 또는 사용자 이름)
        repo: 레포지토리 이름
        pull_number: PR 번호
        title: 새 제목 (선택)
        body: 새 본문 (선택)
        state: 상태 (open | closed, 선택)
        base: 새 대상 브랜치 (선택)

    Returns:
        number, url, title, state를 포함한 JSON 문자열
    """
    if not (_is_path_name(owner) and _is_path_name(repo)):
        return _error("owner/repo 형식이 올바르지 않습니다.")
    number = _issue_no(pull_number)
    if number is None:
        return _error(_PR_NUMBER_ERROR)
    payload = {key: value for key, value in (("title", title), ("body", body)) if _text(value)}
    state = _text(state)
    if state:
        if state not in ("open", "closed"):
            return _error("state는 open, closed 중 하나여야 합니다.")
        payload["state"] = state
    if _text(base):
        payload["base"] = _base_ref(base)
        if payload["base"] is None:
            return _error("base 브랜치 형식이 올바르지 않습니다.")
    if not payload:
        return _error("바꿀 칸이 없습니다.")
    try:
        response = await get_http_client().patch(
            f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}",
            headers=_headers(token), json=payload, timeout=_TIMEOUT,
        )
        if response.status_code != 200:
            return _api_error(response)
        pr = response.json()
        return json.dumps(
            {"success": True, "number": pr["number"], "url": pr["html_url"], "title": pr["title"], "state": pr["state"]},
            ensure_ascii=False,
        )
    except Exception as e:
        return _error(str(e))


async def github_request_reviewers(token: str, owner: str, repo: str, pull_number: str, reviewers: str) -> str:
    """
    GitHub PR에 리뷰어를 지정합니다.

    Args:
        token: GitHub access token
        owner: 레포지토리 소유자 (조직 또는 사용자 이름)
        repo: 레포지토리 이름
        pull_number: PR 번호
        reviewers: 리뷰어 login, 쉼표 구분

    Returns:
        number, requested_reviewers(요청된 리뷰어 login 목록)를 포함한 JSON 문자열
    """
    if not (_is_path_name(owner) and _is_path_name(repo)):
        return _error("owner/repo 형식이 올바르지 않습니다.")
    number = _issue_no(pull_number)
    if number is None:
        return _error(_PR_NUMBER_ERROR)
    names = _csv(reviewers)
    if not names or not all(_is_login(n) for n in names):
        return _error("reviewers는 쉼표로 구분한 GitHub 사용자 이름이어야 합니다.")
    try:
        response = await get_http_client().post(
            f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}/requested_reviewers",
            headers=_headers(token), json={"reviewers": names}, timeout=_TIMEOUT,
        )
        if response.status_code != 201:
            return _api_error(response)
        requested = [u.get("login") for u in response.json().get("requested_reviewers") or []]
        return json.dumps({"success": True, "number": number, "requested_reviewers": requested}, ensure_ascii=False)
    except Exception as e:
        return _error(str(e))


async def github_find_pull_request(
    token: str, owner: str, repo: str, search_by: str, value: str, state: str = "open", max_results: int = 20,
) -> str:
    """
    GitHub 레포지토리에서 PR을 찾습니다.

    Args:
        token: GitHub access token
        owner: 레포지토리 소유자 (조직 또는 사용자 이름)
        repo: 레포지토리 이름
        search_by: 찾는 기준 (number | branch | title | body | author)
        value: 찾을 값
        state: PR 상태 (open | closed | all, 기본값: open)
        max_results: 최대 결과 수 (1~100)

    Returns:
        found, total, items, message를 포함한 JSON 문자열
    """
    if not (_is_path_name(owner) and _is_path_name(repo)):
        return _error("owner/repo 형식이 올바르지 않습니다.")
    if search_by not in _PR_SEARCH_BY:
        return _error(f"search_by는 {', '.join(_PR_SEARCH_BY)} 중 하나여야 합니다.")
    state = _text(state) or "open"
    if state not in _ISSUE_STATES:
        return _error("state는 open, closed, all 중 하나여야 합니다.")
    # 번호는 참조식 결과로 int가 올 수 있다.
    value = str(value) if isinstance(value, int) and not isinstance(value, bool) else _text(value)
    if not value:
        return _error("value를 입력해 주세요.")
    limit = _clamp(max_results, 20, 1, _MAX_ITEMS)
    number = head = None
    if search_by == "number" and (number := _issue_no(value)) is None:
        return _error("PR 번호는 양의 정수여야 합니다.")
    if search_by == "branch" and (head := _head_ref(value)) is None:
        return _error("브랜치 형식이 올바르지 않습니다.")
    if search_by == "author" and not _is_login(value):
        return _error("GitHub 사용자 이름 형식이 올바르지 않습니다.")
    if search_by in ("title", "body") and len(value) > _MAX_SEARCH_VALUE:
        return _error(f"검색어는 {_MAX_SEARCH_VALUE}자 이하여야 합니다.")

    repo_api = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}"
    try:
        client = get_http_client()
        if search_by in ("number", "branch"):
            if number is not None:
                response = await client.get(f"{repo_api}/pulls/{number}", headers=_headers(token), timeout=_TIMEOUT)
            else:
                response = await client.get(
                    f"{repo_api}/pulls", headers=_headers(token),
                    params={"head": _head_query(owner, head), "state": state, "per_page": limit}, timeout=_TIMEOUT,
                )
            if response.status_code == 404:
                return _found([])
            if response.status_code != 200:
                return _api_error(response)
            data = response.json()
            return _found([_pr_item(p) for p in (data if isinstance(data, list) else [data])][:limit])

        state_q = "" if state == "all" else f" state:{state}"
        if search_by == "author":
            q = f"author:{value} is:pr repo:{owner}/{repo}{state_q}"
        else:
            q = f"{value} in:{search_by} repo:{owner}/{repo} is:pr{state_q}"
        hits, error = await _search(client, token, owner, repo, q, limit, pulls=True)
        return error or _found([_pr_item(h) for h in hits][:limit])
    except Exception as e:
        return _error(str(e))


async def github_find_or_create_pull_request(
    token: str,
    owner: str,
    repo: str,
    head: str,
    base: str,
    title: str,
    body: str = "",
    draft: bool = False,
    maintainer_can_modify: bool = True,
) -> str:
    """
    head·base가 같은 열린 PR을 찾고, 없으면 새로 만듭니다.

    Args:
        token: GitHub access token
        owner: 레포지토리 소유자 (조직 또는 사용자 이름)
        repo: 레포지토리 이름
        head: 변경을 담은 브랜치 (포크면 user:branch)
        base: 합칠 대상 브랜치
        title: 없을 때 만들 PR 제목
        body: 없을 때 만들 PR 본문 (선택)
        draft: 없을 때 초안 PR로 만들지 여부
        maintainer_can_modify: 메인테이너가 head 브랜치를 수정할 수 있게 할지 여부

    Returns:
        created, number, url, title, state, draft를 포함한 JSON 문자열
    """
    if not (_is_path_name(owner) and _is_path_name(repo)):
        return _error("owner/repo 형식이 올바르지 않습니다.")
    head, base, error = _pr_refs(head, base)
    if error:
        return error
    if not _text(title):
        return _error("title을 입력해 주세요.")
    try:
        client = get_http_client()
        response = await client.get(
            f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/pulls", headers=_headers(token),
            params={"head": _head_query(owner, head), "base": base, "state": "open", "per_page": 1}, timeout=_TIMEOUT,
        )
        if response.status_code != 200:
            return _api_error(response)
        pulls = response.json()
        if pulls:
            return json.dumps({"success": True, "created": False, **_pr_out(pulls[0])}, ensure_ascii=False)
        payload = {"title": title, "head": head, "base": base, "body": body or "", "draft": bool(draft),
                   "maintainer_can_modify": bool(maintainer_can_modify)}
        pr, error = await _create_pr(client, token, owner, repo, payload)
        return error or json.dumps({"success": True, "created": True, **_pr_out(pr)}, ensure_ascii=False)
    except Exception as e:
        return _error(str(e))


def _author_type(user) -> str:
    return "bot" if (user or {}).get("type") == "Bot" else "human"


async def github_get_pr_reviews(
    token: str,
    owner: str,
    repo: str,
    pull_number: str,
    include_comments: bool = True,
    states: str = "",
    author_type: str = "all",
) -> str:
    """
    GitHub PR에 달린 리뷰와 줄 코멘트를 가져옵니다.

    Args:
        token: GitHub access token
        owner: 레포지토리 소유자 (조직 또는 사용자 이름)
        repo: 레포지토리 이름
        pull_number: PR 번호
        include_comments: 줄 코멘트도 가져올지 여부
        states: 리뷰 상태 필터, 쉼표 구분 (APPROVED | CHANGES_REQUESTED | COMMENTED | DISMISSED, 비우면 전부)
        author_type: 작성자 종류 (human | bot | all, 기본값: all)

    Returns:
        found, total, items, comments, approved_count, changes_requested_count, message, truncated를 포함한 JSON 문자열
    """
    if not (_is_path_name(owner) and _is_path_name(repo)):
        return _error("owner/repo 형식이 올바르지 않습니다.")
    number = _issue_no(pull_number)
    if number is None:
        return _error(_PR_NUMBER_ERROR)
    wanted = [s.upper() for s in _csv(states)]
    if any(s not in _REVIEW_STATES for s in wanted):
        return _error(f"states는 {', '.join(_REVIEW_STATES)} 중에서 골라야 합니다.")
    author_type = _text(author_type) or "all"
    if author_type not in _AUTHOR_TYPES:
        return _error("author_type은 human, bot, all 중 하나여야 합니다.")

    def kept(user) -> bool:
        return author_type == "all" or _author_type(user) == author_type

    try:
        client = get_http_client()
        pr_url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}"
        reviews, truncated, error = await _fetch_pages(client, token, f"{pr_url}/reviews")
        if error:
            return error
        comments: list[dict] = []
        if include_comments:
            comments, more, error = await _fetch_recent_comments(client, token, pr_url, kept)
            if error:
                return error
            truncated = truncated or more

        # PENDING은 아직 제출 전인 리뷰라 뺀다. GitHub 응답은 오래된 것부터.
        submitted = [r for r in reviews if r.get("state") != "PENDING" and kept(r.get("user"))]
        # GitHub PR 화면처럼 리뷰어마다 마지막 결정 하나로 센다(states 필터·자르기 전 전체 기준).
        decisions = {
            (r.get("user") or {}).get("login"): r["state"] for r in submitted if r.get("state") in _REVIEW_DECISIONS
        }
        picked = [r for r in reversed(submitted) if not wanted or r.get("state") in wanted]  # 최신 우선
        truncated = truncated or len(picked) > _MAX_REVIEWS_OUT

        budget = _MAX_REVIEW_TEXT_TOTAL

        def cut(text, limit: int) -> str:
            nonlocal budget, truncated
            text = text or ""
            out = text[:min(limit, budget)]
            budget -= len(out)
            truncated = truncated or len(out) < len(text)
            return out

        items = [
            {
                "id": r.get("id"),
                "author": (r.get("user") or {}).get("login"),
                "author_type": _author_type(r.get("user")),
                "state": r.get("state"),
                "body": cut(r.get("body"), _MAX_REVIEW_TEXT),
                "submitted_at": r.get("submitted_at"),
                "commit_id": r.get("commit_id"),
            }
            for r in picked[:_MAX_REVIEWS_OUT]
        ]
        out_comments = [
            {
                "path": c.get("path"),
                "line": c.get("line") if c.get("line") is not None else c.get("original_line"),
                "body": cut(c.get("body"), _MAX_COMMENT_TEXT),
                "author": (c.get("user") or {}).get("login"),
                "created_at": c.get("created_at"),
            }
            for c in comments
        ]
        return _found(
            items,
            comments=out_comments,
            approved_count=sum(s == "APPROVED" for s in decisions.values()),
            changes_requested_count=sum(s == "CHANGES_REQUESTED" for s in decisions.values()),
            truncated=truncated,
        )
    except Exception as e:
        return _error(str(e))


async def _fetch_recent_comments(client, token: str, pr_url: str, kept) -> tuple[list[dict], bool, str | None]:
    """줄 코멘트를 최신순으로 받아 `kept`를 통과한 것이 100개 모이면 멈춘다. (comments, 더 있을 수 있음, error)."""
    comments: list[dict] = []
    batch: list = []
    for page in range(1, _MAX_FILE_PAGES + 1):
        response = await client.get(
            f"{pr_url}/comments",
            headers=_headers(token),
            params={"per_page": 100, "page": page, "sort": "created", "direction": "desc"},
            timeout=_TIMEOUT,
        )
        if response.status_code != 200:
            return [], False, _api_error(response)
        batch = response.json()
        comments.extend(c for c in batch if kept(c.get("user")))
        if len(comments) >= _MAX_REVIEW_COMMENTS_OUT or len(batch) < 100:
            break
    more = len(comments) > _MAX_REVIEW_COMMENTS_OUT or len(batch) == 100
    return comments[:_MAX_REVIEW_COMMENTS_OUT], more, None


# ---------------------------------------------------------------------------
# 조회 묶음 — find_branch·get_file·find_repo·find_org·check_org_membership·find_user
# ---------------------------------------------------------------------------

_MAX_FILE_CHARS = 100_000
_MAX_FILE_BYTES = 1_048_576
_MAX_DIR_ITEMS = 1000
_NOT_MEMBER = "멤버가 아닙니다"


async def _get_json(token: str, url: str, params: dict | None = None):
    """200 → (본문, None), 404 → (None, None) — Search는 404를 0건으로 본다, 그 밖 → (None, error JSON)."""
    response = await get_http_client().get(url, headers=_headers(token), params=params, timeout=_TIMEOUT)
    if response.status_code == 404:
        return None, None
    if response.status_code != 200:
        return None, _api_error(response)
    return response.json(), None


def _branch_item(branch: dict) -> dict:
    return {"name": branch.get("name"), "sha": (branch.get("commit") or {}).get("sha"),
            "protected": bool(branch.get("protected"))}


async def github_find_branch(token: str, owner: str, repo: str, branch: str = "") -> str:
    """
    GitHub 레포지토리의 브랜치를 찾습니다.

    Args:
        token: GitHub access token
        owner: 레포지토리 소유자 (조직 또는 사용자 이름)
        repo: 레포지토리 이름
        branch: 브랜치 이름 (비우면 브랜치 목록, 최대 100개)

    Returns:
        found, total, items(name, sha, protected), message를 포함한 JSON 문자열
    """
    if not (_is_path_name(owner) and _is_path_name(repo)):
        return _error("owner/repo 형식이 올바르지 않습니다.")
    branch_path = None
    if _text(branch):
        if _ref_path(branch) is None:
            return _error("브랜치 형식이 올바르지 않습니다.")
        # 이름 전체를 한 조각으로 — `/`가 남으면 …/branches/main/protection 같은 하위 API로 닿는다.
        branch_path = quote(branch.strip(), safe="")
    url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/branches"
    try:
        if branch_path is None:
            data, error = await _get_json(token, url, {"per_page": 100})
            branches = data or []
        else:
            data, error = await _get_json(token, f"{url}/{branch_path}")
            branches = [data] if data else []
        return error or _found([_branch_item(b) for b in branches[:_MAX_ITEMS]])
    except Exception as e:
        return _error(str(e))


def _file_item(entry: dict, content: str = "", truncated: bool = False) -> dict:
    return {"name": entry.get("name"), "path": entry.get("path"), "type": entry.get("type"), "sha": entry.get("sha"),
            "size": entry.get("size"), "content": content, "truncated": truncated}


def _file_content(entry: dict) -> tuple[str, bool]:
    """(content, truncated). 1MB 넘는 파일은 GitHub가 내용을 주지 않는다(encoding none). 바이너리는 ""."""
    if (entry.get("size") or 0) > _MAX_FILE_BYTES or entry.get("encoding") == "none":
        return "", True
    try:
        text = base64.b64decode(entry.get("content") or "").decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return "", False
    return text[:_MAX_FILE_CHARS], len(text) > _MAX_FILE_CHARS


async def github_get_file(token: str, owner: str, repo: str, path: str, ref: str = "") -> str:
    """
    GitHub 레포지토리의 파일 내용이나 디렉터리 목록을 가져옵니다.

    Args:
        token: GitHub access token
        owner: 레포지토리 소유자 (조직 또는 사용자 이름)
        repo: 레포지토리 이름
        path: 파일 또는 디렉터리 경로
        ref: 브랜치·태그·커밋 SHA (비우면 기본 브랜치)

    Returns:
        found, total, items(name, path, type, sha, size, content, truncated), message를 포함한 JSON 문자열
    """
    if not (_is_path_name(owner) and _is_path_name(repo)):
        return _error("owner/repo 형식이 올바르지 않습니다.")
    file_path = _ref_path(path)
    if file_path is None:
        return _error("path 형식이 올바르지 않습니다.")
    params = None
    if _text(ref):
        params = {"ref": _base_ref(ref)}
        if params["ref"] is None:
            return _error("ref 형식이 올바르지 않습니다.")
    try:
        data, error = await _get_json(token, f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{file_path}", params)
        if error:
            return error
        if data is None:
            return _found([])
        if isinstance(data, list):
            return _found([_file_item(e) for e in data[:_MAX_DIR_ITEMS]])
        return _found([_file_item(data, *_file_content(data))])
    except Exception as e:
        return _error(str(e))


async def github_find_repo(token: str, owner: str, repo: str) -> str:
    """
    GitHub 레포지토리를 찾습니다.

    Args:
        token: GitHub access token
        owner: 레포지토리 소유자 (조직 또는 사용자 이름)
        repo: 레포지토리 이름

    Returns:
        found, total, items(name, full_name, owner, private, default_branch, url, description), message를 포함한 JSON 문자열
    """
    if not (_is_path_name(owner) and _is_path_name(repo)):
        return _error("owner/repo 형식이 올바르지 않습니다.")
    try:
        data, error = await _get_json(token, f"{_GITHUB_API_BASE}/repos/{owner}/{repo}")
        if error:
            return error
        items = [{
            "name": data.get("name"),
            "full_name": data.get("full_name"),
            "owner": (data.get("owner") or {}).get("login"),
            "private": bool(data.get("private")),
            "default_branch": data.get("default_branch"),
            "url": data.get("html_url"),
            "description": data.get("description") or "",
        }] if data else []
        return _found(items)
    except Exception as e:
        return _error(str(e))


async def github_find_org(token: str, org: str) -> str:
    """
    GitHub 조직을 찾습니다.

    Args:
        token: GitHub access token
        org: 조직 이름

    Returns:
        found, total, items(login, name, description, url), message를 포함한 JSON 문자열
    """
    if not _is_login(org):
        return _error("org 형식이 올바르지 않습니다.")
    try:
        data, error = await _get_json(token, f"{_GITHUB_API_BASE}/orgs/{org}")
        if error:
            return error
        items = [{"login": data.get("login"), "name": data.get("name"), "description": data.get("description") or "",
                  "url": data.get("html_url")}] if data else []
        return _found(items)
    except Exception as e:
        return _error(str(e))


async def github_check_org_membership(token: str, org: str, username: str) -> str:
    """
    GitHub 사용자가 조직의 멤버인지 확인합니다.

    Args:
        token: GitHub access token
        org: 조직 이름
        username: 확인할 사용자 login

    Returns:
        found, total, items, message, member를 포함한 JSON 문자열
    """
    if not (_is_login(org) and _is_login(username)):
        return _error("org/username 형식이 올바르지 않습니다.")
    try:
        client = get_http_client()
        response = await client.get(
            f"{_GITHUB_API_BASE}/orgs/{org}/members/{username}", headers=_headers(token), timeout=_TIMEOUT)
        # 302: 요청자가 그 조직 멤버가 아니면 공개 멤버 확인 주소로 보낸다. 공유 클라이언트는 리다이렉트를 따라가지 않는다.
        if response.status_code == 302:
            response = await client.get(
                f"{_GITHUB_API_BASE}/orgs/{org}/public_members/{username}", headers=_headers(token), timeout=_TIMEOUT)
        if response.status_code == 204:
            return _found([{"org": org, "username": username}], member=True)
        if response.status_code == 404:
            return _found([], _NOT_MEMBER, member=False)
        return _api_error(response)
    except Exception as e:
        return _error(str(e))


async def github_find_user(token: str, username: str = "") -> str:
    """
    GitHub 사용자를 찾습니다.

    Args:
        token: GitHub access token
        username: 사용자 login (비우면 연결된 계정 본인)

    Returns:
        found, total, items(login, name, type, url), message를 포함한 JSON 문자열
    """
    username = _text(username)
    if username and not _is_login(username):
        return _error("username 형식이 올바르지 않습니다.")
    url = f"{_GITHUB_API_BASE}/users/{username}" if username else f"{_GITHUB_API_BASE}/user"
    try:
        data, error = await _get_json(token, url)
        if error:
            return error
        items = [{"login": data.get("login"), "name": data.get("name"), "type": data.get("type"),
                  "url": data.get("html_url")}] if data else []
        return _found(items)
    except Exception as e:
        return _error(str(e))


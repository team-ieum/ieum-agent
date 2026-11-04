"""앱 트리거(빌더 전용) 카탈로그 계약 — spec 2026-10-11-app-triggers-design §2."""
from core import template_registry as tr
from core.node_catalog import node_catalog

GMAIL, GITHUB = "gmail.new_email", "github.new_issue"


def _entry(tid: str) -> dict:
    return next(e for e in node_catalog()["entries"] if e["id"] == tid)


def test_gmail_entry_shape():
    assert _entry(GMAIL) == {
        "id": GMAIL, "kind": "trigger", "provider": "google",
        "nodeType": "TRIGGER", "app": "gmail", "appTitle": "Gmail",
        "title": "새 Gmail 메일", "description": "받은편지함에 새 메일이 오면 시작해요.",
        "fixed": {"type": "TRIGGER", "event": GMAIL, "config": {"brand": "gmail"}},
        "inputFields": [{"key": "query", "title": "검색 조건", "type": "string", "ref": False,
                         "path": "config.query",
                         "description": "Gmail 검색식 (예: from:boss@example.com). 비우면 받은편지함 전체"}],
        "outputFields": [
            {"key": "messageId", "title": "메일 ID", "type": "string"},
            {"key": "threadId", "title": "스레드 ID", "type": "string"},
            {"key": "from", "title": "보낸 사람", "type": "string"},
            {"key": "to", "title": "받는 사람", "type": "string"},
            {"key": "subject", "title": "제목", "type": "string"},
            {"key": "snippet", "title": "미리보기", "type": "text"},
            {"key": "bodyText", "title": "본문", "type": "text"},
            {"key": "receivedAt", "title": "받은 시각", "type": "datetime"},
            {"key": "labels", "title": "라벨 ID", "type": "string", "list": True},
        ],
    }


def test_github_entry_shape():
    assert _entry(GITHUB) == {
        "id": GITHUB, "kind": "trigger", "provider": "github",
        "nodeType": "TRIGGER", "app": "github", "appTitle": "GitHub",
        "title": "새 GitHub 이슈", "description": "저장소에 새 이슈가 열리면 시작해요.",
        "fixed": {"type": "TRIGGER", "event": GITHUB, "config": {"brand": "github"}},
        "inputFields": [{"key": "repoId", "title": "저장소", "type": "string", "required": True, "ref": False,
                         "path": "config.repoId", "optionsSource": "github.installed_repos",
                         "description": "새 이슈를 지켜볼 저장소"}],
        "outputFields": [
            {"key": "issueNumber", "title": "이슈 번호", "type": "integer"},
            {"key": "title", "title": "제목", "type": "string"},
            {"key": "body", "title": "본문", "type": "text"},
            {"key": "url", "title": "링크", "type": "string"},
            {"key": "author", "title": "작성자", "type": "string"},
            {"key": "labels", "title": "라벨", "type": "string", "list": True},
            {"key": "repo", "title": "저장소", "type": "string"},
            {"key": "createdAt", "title": "만든 시각", "type": "datetime"},
        ],
    }


def test_앱_트리거는_생성_경로에_없다():
    for tid in (GMAIL, GITHUB):
        t = tr.get_template(tid)
        assert t["builder"] is True

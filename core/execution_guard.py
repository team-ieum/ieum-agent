import re
import urllib.parse
import ipaddress
import socket
from typing import Optional
from api.schemas.request import AgentNodeRequest

class ExecutionGuardError(ValueError):
    """실행 가드 조건 불충족 시 발생하는 예외"""
    pass

class ExecutionGuard:
    """실행(Execution) 전 단계의 안전벨트 및 SSRF 차단 가드"""

    # 사설 대역 및 루프백 정규식 패턴 (URL 정적 필터링용)
    PRIVATE_IP_PATTERNS = [
        r"^localhost$",
        r"^127\.",
        r"^10\.",
        r"^172\.(1[6-9]|2[0-9]|3[0-1])\.",
        r"^192\.168\.",
        r"^169\.254\."
    ]

    @classmethod
    def validate_execution(
        cls,
        request: AgentNodeRequest,
        google_access_token: Optional[str] = None,
        notion_token: Optional[str] = None,
        github_token: Optional[str] = None
    ) -> None:
        """실행 직전 권한/보안 가드 검사를 수행한다."""
        cls.validate_tools(request.tools, google_access_token, notion_token, github_token)

    @classmethod
    def validate_tools(
        cls,
        tools: Optional[list],
        google_access_token: Optional[str] = None,
        notion_token: Optional[str] = None,
        github_token: Optional[str] = None
    ) -> None:
        """도구 목록([{"name", "config"}, ...]) 가드. AI 노드(request.tools)와 ACTION 실행이 같이 쓴다."""
        if not tools:
            return

        # 1. 도구 인자 및 설정 내의 모든 URL SSRF 차단 검사
        cls._scan_urls_recursively(tools)

        # 2. 필수 서비스 토큰 유무 사전 검사
        # (AI 노드의 GitHub는 tools: [] + 서브에이전트 마운트라 이 검사에 걸리지 않는다.)
        for tool in tools:
            tname = tool.get("name", "")
            if tname.startswith("builtin:notion_") and not notion_token:
                raise ExecutionGuardError(
                    f"노드 실행 시 Notion 도구({tname})를 사용하지만 Notion API Token이 주입되지 않았습니다."
                )
            if tname.startswith("builtin:google_") and not google_access_token:
                raise ExecutionGuardError(
                    f"노드 실행 시 Google 도구({tname})를 사용하지만 Google Access Token이 주입되지 않았습니다."
                )
            if tname.startswith("builtin:github_") and not github_token:
                raise ExecutionGuardError(
                    f"노드 실행 시 GitHub 도구({tname})를 사용하지만 GitHub Token이 주입되지 않았습니다."
                )

    @classmethod
    def _scan_urls_recursively(cls, data) -> None:
        """딕셔너리, 리스트 등 중첩 구조를 재귀 탐색하여 URL 형태의 문자열을 찾아 검증한다."""
        if isinstance(data, dict):
            for val in data.values():
                cls._scan_urls_recursively(val)
        elif isinstance(data, list):
            for item in data:
                cls._scan_urls_recursively(item)
        elif isinstance(data, str):
            if data.startswith("http://") or data.startswith("https://"):
                cls._check_url_safety(data)

    @staticmethod
    def _is_blocked_ip(ip) -> bool:
        """사설·루프백·링크로컬·미지정 주소. IPv4 매핑 IPv6(::ffff:a.b.c.d)는 매핑된 v4로 판정한다."""
        ip = getattr(ip, "ipv4_mapped", None) or ip
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified

    @classmethod
    def _check_url_safety(cls, url: str) -> None:
        """URL이 로컬 또는 사설망 대역을 가리키는지 확인하고 차단한다."""
        try:
            parsed = urllib.parse.urlparse(url)
            hostname = parsed.hostname
            if not hostname:
                return

            # 0. IP 리터럴(대괄호 IPv6 포함)은 DNS 조회 없이 바로 판정한다.
            #    gethostbyname은 IPv6 리터럴에 gaierror를 내 통과시키고, 정규식은 IPv6·0.0.0.0을 못 본다.
            try:
                literal = ipaddress.ip_address(hostname)
            except ValueError:
                literal = None
            if literal is not None:
                if cls._is_blocked_ip(literal):
                    raise ExecutionGuardError(
                        f"보안 위험: 허용되지 않는 사설망 또는 로컬호스트 주소에 대한 요청이 감지되어 차단되었습니다: {url}"
                    )
                return

            # 1. 정규식 매칭을 통한 간단한 도메인 정적 검사
            for pattern in cls.PRIVATE_IP_PATTERNS:
                if re.search(pattern, hostname, re.IGNORECASE):
                    raise ExecutionGuardError(
                        f"보안 위험: 허용되지 않는 사설망 또는 로컬호스트 주소에 대한 요청이 감지되어 차단되었습니다: {url}"
                    )

            # 2. DNS Resolve를 통한 실제 IP 대역 검사 (DNS Rebinding/SSRF 방지)
            try:
                ip = socket.gethostbyname(hostname)
                ip_obj = ipaddress.ip_address(ip)
                if cls._is_blocked_ip(ip_obj):
                    raise ExecutionGuardError(
                        f"보안 위험: 조회된 IP({ip})가 사설망/루프백 대역에 해당하여 실행이 차단되었습니다: {url}"
                    )
            except socket.gaierror:
                pass

        except ExecutionGuardError:
            raise
        except Exception:
            pass

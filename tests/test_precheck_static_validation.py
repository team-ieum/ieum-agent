"""scripts/precheck_static_validation.py — TSV 한 줄에 싣는 사용자 값 이스케이프."""
from scripts.precheck_static_validation import _esc


def test_제어문자_탭_개행은_xNN으로_한글은_그대로():
    assert _esc("a\tb\nc\x1b[2J\x07한글") == "a\\x09b\\x0ac\\x1b[2J\\x07한글"
    assert _esc(None) == "None"

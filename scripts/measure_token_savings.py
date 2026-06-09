"""검색층 도입 전/후 design_rules 주입 크기를 측정한다(#8 토큰 절감 측정).

비교:
- baseline(폴백): 무매칭 → AI 템플릿 전체 주입 (구 통주입과 동등 규모)
- focused: 실제 요청 태그에 매칭된 템플릿만 주입

사용: python -m scripts.measure_token_savings
"""
from core.skill_loader import load_design_rules

# 대략적 토큰 추정(한국어/JSON 혼합 ~3.5자/토큰)
CHARS_PER_TOKEN = 3.5

PROMPTS = [
    "매일 아침 최신 IT 뉴스를 검색해서 노션 페이지에 저장해줘",
    "매주 월요일 GitHub PR 목록을 슬랙으로 알려줘",
    "고객 문의를 분석해서 긴급하면 Gmail로 알림 보내줘",
    "매시간 구글 시트에 상태를 기록해줘",
]


def _tok(s: str) -> int:
    return round(len(s) / CHARS_PER_TOKEN)


def main() -> None:
    baseline = load_design_rules("zzz 무관한 텍스트")  # 폴백(전체 AI 주입)
    base_len = len(baseline)
    print(f"{'요청':40} {'문자수':>8} {'~토큰':>7} {'절감%':>7}")
    print("-" * 66)
    print(f"{'[baseline] 폴백(AI 전체)':40} {base_len:>8} {_tok(baseline):>7} {'-':>7}")
    for p in PROMPTS:
        r = load_design_rules(p)
        saving = (1 - len(r) / base_len) * 100
        label = (p[:36] + "…") if len(p) > 37 else p
        print(f"{label:40} {len(r):>8} {_tok(r):>7} {saving:>6.1f}%")


if __name__ == "__main__":
    main()

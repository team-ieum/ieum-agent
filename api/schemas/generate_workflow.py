"""트리거 종류 상수."""

# BE workflow-core TriggerType.java 미러. 레지스트리에서 파생하지 않는다(BE 계약을 따른다) —
# 레지스트리 TRIGGER 템플릿의 trigger_type이 여기 있는지는 template_registry 로더가 검사한다.
# tuple인 이유: frozenset이면 list 같은 unhashable 값의 `in` 검사가 TypeError(→ 500)가 된다.
TRIGGER_TYPES = ("SCHEDULE", "MANUAL", "WEBHOOK", "GMAIL_NEW_EMAIL", "GITHUB_NEW_ISSUE", "GITHUB_NEW_PR",
                 "FORMS_NEW_RESPONSE", "FORMS_ANY_RESPONSE")

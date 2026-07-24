"""provider/role/api_key에 따라 ADK LlmAgent의 model 파라미터를 생성하는 팩토리.

분기 규칙:
- GEMINI                                        : CustomGemini (api_key 직접 주입)
- 자체 LLM 활성 + 허용 role + 등록 API 키 없음   : LiteLlm("openai/<model>", api_base=...) — 자체 호스팅 OpenAI 호환 엔드포인트
- 그 외(일반 role / 키 등록됨 / 자체 LLM 비활성) : 모델명 문자열 → ADK가 ANTHROPIC_API_KEY/OPENAI_API_KEY 사용

개발/테스트 계정(ROLE_ADMIN/ROLE_TESTER)은 자신의 API 키를 등록하지 않았을 때만 자체 LLM으로 라우팅된다.
키를 등록하면 그 키가 우선되어, 동일 계정으로 자체 LLM과 자기 키를 모두 쓸 수 있다.
SELF_HOSTED_LLM_BASE_URL이 비어 있으면(모델 준비 전) 전 기능이 비활성화되어 전원 등록 API 키 경로로 동작한다.
"""
from core.config import settings
from core.custom_gemini import CustomGemini


_LITELLM_PREFIX = {"CLAUDE": "anthropic", "OPENAI": "openai"}


def _litellm_model_name(provider: str, model: str) -> str:
    """provider를 LiteLlm 라우팅용 prefix가 붙은 모델명으로 변환한다.

    ADK registry가 'anthropic/'·'openai/' prefix로 LiteLlm을 선택하므로 필수.
    이미 prefix가 붙어있으면 그대로 두고, 매핑 없는 provider는 원본을 반환한다.
    """
    prefix = _LITELLM_PREFIX.get(provider.upper())
    if not prefix:
        return model
    if model.startswith(f"{prefix}/"):
        return model
    return f"{prefix}/{model}"


def _self_hosted_configured() -> bool:
    """자체 LLM 엔드포인트가 설정되어 활성 상태인지. BASE_URL과 MODEL이 모두 있어야 활성으로 본다.
    (MODEL이 비면 LiteLlm이 "openai/"로 생성돼 호출이 실패하므로 둘 다 요구한다.)"""
    return bool(settings.SELF_HOSTED_LLM_BASE_URL) and bool(settings.SELF_HOSTED_LLM_MODEL)


def is_self_hosted_eligible(user_role: str | None) -> bool:
    """이 role이 자체 LLM을 쓸 자격이 있고 엔드포인트가 활성인지 (api_key 유무와 무관).

    "이 계정이 자체 LLM을 쓸 수 있는가"를 나타낸다. api_key 필수 완화 판정에 사용한다.
    엔드포인트 미설정 시에는 항상 False이므로, 모델 준비 전에는 일반 유저와 동일하게 키가 필수다.
    """
    return (
        _self_hosted_configured()
        and user_role is not None
        and user_role in settings.SELF_HOSTED_ALLOWED_ROLES
    )


def _use_self_hosted(api_key: str | None, user_role: str | None) -> bool:
    """실제로 자체 LLM으로 라우팅되는지 여부.

    자격이 있고(엔드포인트 활성 + 허용 role) 등록 API 키가 없을 때만 True.
    자격이 있어도 사용자가 자신의 API 키를 등록했다면 그 키를 우선 사용한다(키 우선, 없으면 자체 LLM).
    """
    return is_self_hosted_eligible(user_role) and not api_key


def build_model_param(provider: str, model: str, api_key: str | None, user_role: str | None = None):
    """LlmAgent(model=...)에 전달할 값을 생성한다.

    기존 동작 보존: 자체 LLM 분기 밖(role 미해당/None, 키 등록됨, 엔드포인트 미설정)에서는 기존과 동일하게 모델명 문자열을 반환한다.
    """
    # self-hosted 분기를 GEMINI보다 먼저 둔다. GEMINI provider + 자체 LLM 자격 + 키 없음일 때
    # CustomGemini(api_key=None)으로 빠지지 않고 provider 무관하게 자체 LLM으로 라우팅하기 위함이다.
    if _use_self_hosted(api_key, user_role):
        # lazy import: self-hosted 경로에서만 LiteLlm(litellm)을 로드해 기본 경로의 import 부담을 줄인다.
        from google.adk.models.lite_llm import LiteLlm
        return LiteLlm(
            model=f"openai/{settings.SELF_HOSTED_LLM_MODEL}",
            api_base=settings.SELF_HOSTED_LLM_BASE_URL,
            api_key=settings.SELF_HOSTED_LLM_API_KEY or "not-needed",
        )
    if provider.upper() == "GEMINI":
        return CustomGemini(model=model, api_key=api_key)
    # 신규: Claude/OpenAI + 키 있음 → LiteLlm 인스턴스(요청별 api_key 격리, os.environ 미오염)
    if api_key and provider.upper() in _LITELLM_PREFIX:
        from google.adk.models.lite_llm import LiteLlm
        return LiteLlm(model=_litellm_model_name(provider, model), api_key=api_key)
    # 키 없음/미매핑 → 기존 문자열 경로(ADK env 키 fallback) 보존
    return model


def cost_model_name(provider: str, model: str, api_key: str | None, user_role: str | None = None) -> str | None:
    """cost 계산용 모델명. build_model_param의 실제 라우팅을 반영한다.

    self-hosted로 라우팅되면 상용 단가가 아니므로 None(cost 생략, self-hosted는 미과금).
    """
    if _use_self_hosted(api_key, user_role):
        return None
    return _litellm_model_name(provider, model)


def uses_env_key(provider: str, api_key: str | None, user_role: str | None = None) -> bool:
    """os.environ에 API Key 주입이 필요한지 여부.

    Gemini(CustomGemini 직접 주입)와 자체 LLM(엔드포인트 자격증명 사용)은 환경변수 주입이 불필요하다.
    """
    if not api_key:
        # 주입할 키가 없으면 os.environ에 None을 넣어 TypeError가 나는 것을 방어한다.
        return False
    if provider.upper() == "GEMINI":
        return False
    if _use_self_hosted(api_key, user_role):
        return False
    # 신규: Claude/OpenAI + 키 있음은 LiteLlm 인스턴스가 키를 주입받으므로 env 불필요
    if provider.upper() in _LITELLM_PREFIX:
        return False
    return True

"""BE ProviderRegistry 카탈로그(IEUM-BE-67)와 agent resolve_model의 계약.

카탈로그의 모든 id는 승격·강등 없이 자기 자신으로 실행돼야 한다. 여기서 깨지면
사용자가 고른 모델이 조용히 다른 모델로 바뀐다(또는 채팅 편집마다 기본값으로 스냅백).

CATALOG는 ieum-backend api/.../provider/service/ProviderRegistry.java의 복제다 —
크로스레포라 코드로 묶을 수 없다. BE 카탈로그가 바뀌면 여기도 바꾼다.
실 litellm.model_cost를 그대로 쓴다(폐기일 판정이 목적이라 monkeypatch하지 않는다).

이 계약이 못 잡는 방향이 하나 있다: BE가 카탈로그에 모델을 **추가**해도 여기 CATALOG는 그대로라
테스트가 전부 초록인 채 새 모델이 승격·강등에 걸릴 수 있다. BE ProviderRegistry를 고칠 때
이 파일도 같이 고쳐야 한다는 규약이 유일한 방어다.
"""
from datetime import date

import litellm
import pytest
from litellm.litellm_core_utils.get_model_cost_map import get_model_cost_map_source_info

from core.provider_config import _catalog_key, _is_deprecated, resolve_model

CATALOG = [
    ("CLAUDE", "claude-haiku-4-5"),
    ("CLAUDE", "claude-sonnet-5"),
    ("CLAUDE", "claude-opus-5"),
    ("OPENAI", "gpt-5.6-luna"),
    ("OPENAI", "gpt-5.6-terra"),
    ("OPENAI", "gpt-5.6-sol"),
    ("GEMINI", "gemini-3.5-flash-lite"),
    ("GEMINI", "gemini-3.7-flash"),
    ("GEMINI", "gemini-2.5-pro"),
]

# BE 카탈로그가 광고하는 모델의 알려진 폐기 예정일(litellm model_cost 기준).
# 폐기일이 지나면 resolve_model이 그 id를 기본 모델로 강등하므로 아래 두 테스트가 함께 깨진다.
# 그런데 이 pytest는 .github/workflows/agent-dev-server.yml의 배포 게이트라, 그대로 두면
# 그날부터 무관한 PR까지 dev 배포가 막힌다. 배포를 막는 대신 xfail로 신호만 남긴다.
# 교체 후속 이슈: https://app.notion.com/p/3c66db85d7f3816999ccf0807bd1f8cd
SUNSET_DATES = {"claude-haiku-4-5": date(2026, 10, 15)}


def _catalog_params():
    """CATALOG를 pytest 파라미터로 변환하되, 폐기일이 지난 모델은 xfail로 표시한다."""
    params = []
    for provider, model in CATALOG:
        sunset = SUNSET_DATES.get(model)
        marks = []
        if sunset is not None:
            marks.append(pytest.mark.xfail(
                date.today() >= sunset,
                reason=f"{model} 폐기일({sunset}) 경과 — BE ProviderRegistry에서 후계 모델로 교체 필요",
                strict=False,
            ))
        params.append(pytest.param(provider, model, marks=marks))
    return params


@pytest.mark.parametrize("provider,model", _catalog_params())
def test_catalog_model_resolves_to_itself(provider, model):
    assert resolve_model(provider, model) == model


@pytest.mark.parametrize("provider,model", _catalog_params())
def test_catalog_model_not_deprecated(provider, model):
    # litellm은 import 시점에 원격 model_cost를 받아오고, 실패하면 조용히 패키지 백업으로 폴백한다.
    # 그 백업엔 카탈로그 9개 중 7개가 없어서, 네트워크가 없으면 아래 등록 단언이 무더기로 깨진다.
    # 이 pytest는 배포 게이트라(agent-dev-server.yml) 코드와 무관하게 배포가 막힌다 — skip이 맞다.
    if get_model_cost_map_source_info()["source"] != "remote":
        pytest.skip("litellm이 원격 model_cost를 못 받아 로컬 백업으로 폴백 — 등록·폐기일 판정 불가")
    assert _catalog_key(provider, model) in litellm.model_cost  # 미등록이면 _is_deprecated가 공허하게 False
    assert _is_deprecated(provider, model) is False

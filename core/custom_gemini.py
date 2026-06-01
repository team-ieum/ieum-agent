from functools import cached_property
from typing import Optional, Any
from google.genai import Client
from google.genai import types
from google.adk.models.google_llm import Gemini

def _clean_schema(schema: Any):
    if schema is None:
        return
        
    # Pydantic models or objects
    if hasattr(schema, "__dict__"):
        for key in ["additional_properties", "additionalProperties"]:
            if key in schema.__dict__:
                schema.__dict__[key] = None
        try:
            if hasattr(schema, "additional_properties"):
                schema.additional_properties = None
            if hasattr(schema, "additionalProperties"):
                schema.additionalProperties = None
        except Exception:
            pass

    # Standard dicts
    if isinstance(schema, dict):
        for key in ["additional_properties", "additionalProperties"]:
            schema.pop(key, None)
        for v in schema.values():
            _clean_schema(v)
            
    # Recursive field traversals for Schema types
    if hasattr(schema, "properties") and schema.properties:
        if isinstance(schema.properties, dict):
            for v in schema.properties.values():
                _clean_schema(v)
    if hasattr(schema, "items") and schema.items:
        _clean_schema(schema.items)
    if hasattr(schema, "any_of") and schema.any_of:
        for item in schema.any_of:
            _clean_schema(item)

def _clean_tools(tools: Any):
    if not tools:
        return
    for tool in tools:
        if hasattr(tool, "function_declarations") and tool.function_declarations:
            for fd in tool.function_declarations:
                if hasattr(fd, "parameters") and fd.parameters:
                    _clean_schema(fd.parameters)


# Gemini 2.5 thinking 예산(토큰). 0=완전 비활성이나 도구 호출 판단까지 생략되어 에이전트가
# 조회 도구(github_list_repos 등)를 안 부르는 부작용이 있다. 작은 값으로 추론은 유지하되
# thinking 시간을 제한해 지연을 줄인다.
_THINKING_BUDGET = 512


def _limit_thinking(config: Any):
    """Gemini 2.5 계열의 thinking(사고) 예산을 작은 값으로 제한해 호출당 지연을 줄인다.

    완전 비활성(0)이 아니라 작은 예산을 주어 도구 호출 판단 등 최소한의 추론은 유지한다.
    호출자가 명시적으로 thinking_config를 지정하지 않은 경우에만 적용한다."""
    if config is None:
        return
    tc = types.ThinkingConfig(thinking_budget=_THINKING_BUDGET)
    try:
        if isinstance(config, dict):
            config.setdefault("thinking_config", tc)
        elif hasattr(config, "thinking_config"):
            if getattr(config, "thinking_config", None) is None:
                config.thinking_config = tc
    except Exception:
        pass

class CustomGemini(Gemini):
    """API Key를 os.environ 없이 동적으로 주입받는 커스텀 Gemini 모델 객체"""
    api_key: Optional[str] = None

    @cached_property
    def api_client(self) -> Client:
        base_url = self.base_url
        kwargs: dict[str, Any] = {
            'http_options': types.HttpOptions(
                headers=self._tracking_headers(),
                retry_options=self.retry_options,
                base_url=base_url,
            )
        }
        if self.model.startswith('projects/'):
            kwargs['vertexai'] = True

        # os.environ 대신 생성자에서 전달받은 api_key 주입
        if self.api_key:
            kwargs['api_key'] = self.api_key

        client = Client(**kwargs)

        # Wrap generate_content + generate_content_stream (sync/async 모두).
        # ADK가 스트리밍 경로(generate_content_stream)를 사용할 때도 _clean_tools가
        # 적용되도록 하여 Gemini의 additionalProperties 스키마 거부 재발을 막는다.
        orig_generate_content = client.models.generate_content
        orig_generate_content_async = client.aio.models.generate_content
        orig_generate_content_stream = client.models.generate_content_stream
        orig_generate_content_stream_async = client.aio.models.generate_content_stream

        def _clean_config(args, kwargs):
            config = kwargs.get("config") or (args[2] if len(args) > 2 else None)
            if config and hasattr(config, "tools") and config.tools:
                _clean_tools(config.tools)
            _limit_thinking(config)

        def wrapped_generate_content(*args, **kwargs):
            _clean_config(args, kwargs)
            return orig_generate_content(*args, **kwargs)

        async def wrapped_generate_content_async(*args, **kwargs):
            _clean_config(args, kwargs)
            return await orig_generate_content_async(*args, **kwargs)

        def wrapped_generate_content_stream(*args, **kwargs):
            _clean_config(args, kwargs)
            return orig_generate_content_stream(*args, **kwargs)

        async def wrapped_generate_content_stream_async(*args, **kwargs):
            _clean_config(args, kwargs)
            return await orig_generate_content_stream_async(*args, **kwargs)

        client.models.generate_content = wrapped_generate_content
        client.aio.models.generate_content = wrapped_generate_content_async
        client.models.generate_content_stream = wrapped_generate_content_stream
        client.aio.models.generate_content_stream = wrapped_generate_content_stream_async

        return client

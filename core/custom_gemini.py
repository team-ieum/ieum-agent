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

class CustomGemini(Gemini):
    """API Key를 os.environ 없이 동적으로 주입받는 커스텀 Gemini 모델 객체"""
    api_key: Optional[str] = None

    @cached_property
    def api_client(self) -> Client:
        from google.genai import Client

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

        # Wrap models.generate_content and aio.models.generate_content
        orig_generate_content = client.models.generate_content
        orig_generate_content_async = client.aio.models.generate_content

        def wrapped_generate_content(*args, **kwargs):
            config = kwargs.get("config") or (args[2] if len(args) > 2 else None)
            if config and hasattr(config, "tools") and config.tools:
                _clean_tools(config.tools)
            return orig_generate_content(*args, **kwargs)

        async def wrapped_generate_content_async(*args, **kwargs):
            config = kwargs.get("config") or (args[2] if len(args) > 2 else None)
            if config and hasattr(config, "tools") and config.tools:
                _clean_tools(config.tools)
            return await orig_generate_content_async(*args, **kwargs)

        client.models.generate_content = wrapped_generate_content
        client.aio.models.generate_content = wrapped_generate_content_async

        return client

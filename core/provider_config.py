MODEL_MAP: dict[str, str] = {
    "CLAUDE": "claude-sonnet-4-20250514",
    "OPENAI": "gpt-4o",
    "GEMINI": "gemini-2.5-flash",
}

ENV_KEY_MAP: dict[str, str] = {
    "CLAUDE": "ANTHROPIC_API_KEY",
    "OPENAI": "OPENAI_API_KEY",
    "GEMINI": "GOOGLE_API_KEY",
}


def resolve_model(provider: str) -> str:
    return MODEL_MAP.get(provider.upper(), "gemini-2.5-flash")


def resolve_env_key(provider: str) -> str | None:
    return ENV_KEY_MAP.get(provider.upper())

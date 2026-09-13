"""Provider selection.

The rest of the codebase calls build_client() and never imports a vendor SDK.
Swapping Groq for OpenAI is an environment change, not a code change.
"""

from __future__ import annotations

import os
from pathlib import Path

from .base import (
    LLMClient, LLMRequest, LLMResponse, MalformedResponse, extract_json,
)
from .mock import MockLLM
from .replay import CassetteMiss, ReplayClient

__all__ = [
    "LLMClient", "LLMRequest", "LLMResponse", "MalformedResponse",
    "extract_json", "MockLLM", "ReplayClient", "CassetteMiss",
    "build_client", "PROVIDERS",
]

# base_url, env var holding the key, default model
PROVIDERS = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "llama-3.3-70b-versatile"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4o-mini"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "meta-llama/llama-3.3-70b-instruct"),
}


def build_live_client(provider: str | None = None):
    provider = (provider or os.getenv("FF_LLM_PROVIDER", "groq")).lower()
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; expected one of {sorted(PROVIDERS)}")

    from .openai_compat import OpenAICompatClient

    base_url, key_var, default_model = PROVIDERS[provider]
    return OpenAICompatClient(
        api_key=os.getenv(key_var, ""),
        base_url=base_url,
        model=os.getenv("FF_MODEL", default_model),
        provider=provider,
    )


def build_client(mode: str | None = None) -> LLMClient:
    """Return the client selected by FF_LLM_MODE.

        mock    scripted, offline, free            (default)
        replay  play back recorded responses       (record with FF_LLM_RECORD=1)
        live    real API calls
    """
    mode = (mode or os.getenv("FF_LLM_MODE", "mock")).lower()

    if mode == "mock":
        chaos = {c.strip() for c in os.getenv("FF_MOCK_CHAOS", "").split(",") if c.strip()}
        return MockLLM(chaos=chaos)

    if mode == "live":
        return build_live_client()

    if mode == "replay":
        recording = os.getenv("FF_LLM_RECORD", "") == "1"
        provider = os.getenv("FF_LLM_PROVIDER", "groq").lower()
        return ReplayClient(
            cassette_dir=Path(os.getenv("FF_CASSETTE_DIR", "cassettes")),
            inner=build_live_client() if recording else None,
            record=recording,
            model=os.getenv("FF_MODEL", PROVIDERS[provider][2]),
        )

    raise ValueError(f"unknown FF_LLM_MODE {mode!r}; expected mock, replay or live")

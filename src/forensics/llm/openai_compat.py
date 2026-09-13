"""Live client for any OpenAI-compatible endpoint.

Groq, OpenAI, OpenRouter, Together and local servers all speak the same wire
protocol, so one implementation covers all of them; only base_url, api_key and
model change. That is why this file is not called groq.py.
"""

from __future__ import annotations

import time

from .base import LLMRequest, LLMResponse


class OpenAICompatClient:
    def __init__(self, api_key: str, base_url: str, model: str, provider: str = "openai") -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "The live client needs the openai package: pip install openai"
            ) from exc

        if not api_key:
            raise ValueError(f"no API key for provider {provider!r}")

        self.model = model
        self.provider = provider
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=60.0, max_retries=2)

    def complete(self, request: LLMRequest) -> LLMResponse:
        kwargs = {
            "model": request.model or self.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        # JSON mode constrains the model to emit syntactically valid JSON. Not
        # every model on every provider supports it, so we degrade rather than
        # crash -- extract_json() in base.py is the safety net either way.
        if request.json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        try:
            completion = self._client.chat.completions.create(**kwargs)
        except Exception:
            if request.json_mode:
                kwargs.pop("response_format", None)
                completion = self._client.chat.completions.create(**kwargs)
            else:
                raise
        elapsed_ms = (time.perf_counter() - started) * 1000

        choice = completion.choices[0]
        usage = completion.usage
        return LLMResponse(
            text=choice.message.content or "",
            model=completion.model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=round(elapsed_ms, 2),
            finish_reason=choice.finish_reason or "stop",
            source="live",
        )

"""The seam between the pipeline and whatever is generating text.

Everything the pipeline knows about an LLM is in this file. Steps depend on the
LLMClient protocol, never on a vendor SDK, which is what lets the same step code
run against a scripted mock, a recorded cassette, or a live API.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class MalformedResponse(ValueError):
    """The model returned something we could not parse as the requested JSON.

    This is a first-class failure, not an accident: it maps directly onto the
    "Prompt Failure" category in the Phase 3 taxonomy.
    """


class LLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Names the calling step ("extraction", "classification", ...). The mock
    # dispatches on it and Phase 2 spans are labelled with it.
    tag: str
    system: str
    user: str
    model: str = "mock"
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = 1500
    json_mode: bool = True

    def cache_key(self) -> str:
        """Stable fingerprint of everything that can change the answer.

        Two identical requests produce the same key, so a recorded response can
        be replayed. Change the prompt and the key changes, so you get a cache
        miss instead of a stale answer. That is the property Phase 5 relies on.
        """
        blob = json.dumps(
            {
                "tag": self.tag, "system": self.system, "user": self.user,
                "model": self.model, "temperature": self.temperature,
                "max_tokens": self.max_tokens, "json_mode": self.json_mode,
            },
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class LLMResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Raw text exactly as the model returned it, before any parsing. This is the
    # single most important field for debugging: when a step fails to parse a
    # response, this is the evidence that explains why.
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    finish_reason: str = "stop"
    source: str = "live"  # live | mock | replay

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMClient(Protocol):
    """Structural interface: anything with this method is an LLM client.

    A Protocol rather than a base class, so nothing has to inherit from us --
    a test double can be a plain object with a complete() method.
    """

    def complete(self, request: LLMRequest) -> LLMResponse: ...


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def extract_json(text: str) -> dict:
    """Pull a JSON object out of a model response.

    Models wrap JSON in markdown fences, add a preamble, or trail an
    explanation, no matter how firmly the prompt says not to. Tolerating that
    here is deliberate: we want to record cosmetic sloppiness in the span
    without treating it as a pipeline failure, while genuinely unparseable
    output still raises.
    """
    cleaned = _FENCE.sub("", text).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise MalformedResponse(f"no JSON object found in response: {text[:200]!r}")
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise MalformedResponse(f"invalid JSON in response: {exc}") from exc

    if not isinstance(parsed, dict):
        raise MalformedResponse(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed

"""Failures that a step can raise.

The point of this class is the raw_response field. When a step fails because the
model said something unusable, the exception message tells you *that* it broke;
the raw response tells you *why*. Phase 2 writes both into the span, so a failed
trace still carries the evidence needed to diagnose it.
"""

from __future__ import annotations


class StepError(Exception):
    def __init__(self, step: str, message: str, raw_response: str | None = None,
                 cache_key: str | None = None) -> None:
        super().__init__(f"[{step}] {message}")
        self.step = step
        self.message = message
        self.raw_response = raw_response
        self.cache_key = cache_key

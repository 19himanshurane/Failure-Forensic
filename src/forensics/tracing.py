"""Phase 2: instrumentation.

Two independent mechanisms, deliberately layered:

  RecordingLLMClient  -- wraps whatever LLMClient a step is given, and keeps
                         every request/response pair that passes through it.
                         This is where prompt, raw response, model, tokens and
                         cache_key come from.
  traced_step         -- a decorator applied directly to a step function
                         (intake, extract, classify, summarize). One line at
                         the definition turns it into an instrumented step,
                         exactly as Phase 2 asked for. With no trace active it
                         is a pure passthrough, so run_pipeline's untraced
                         path costs nothing and behaves identically.

The two meet through a contextvar: run_traced_pipeline() opens a trace context
around an ordinary call to the four step functions, and each decorated step
notices the open context, times itself, and appends its own Span -- the step
functions themselves stay exactly as ignorant of tracing as runner.py's
docstring insists they must be.

Each step also opens a real OpenTelemetry span (see otel.py) alongside its own
Span object -- "OpenTelemetry + custom spans", not one instead of the other.
"""

from __future__ import annotations

import functools
import inspect
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from . import otel
from .errors import StepError
from .llm import LLMClient, LLMRequest, LLMResponse
from .models import Span

F = TypeVar("F", bound=Callable[..., Any])


class RecordingLLMClient:
    """Wraps a client and keeps every request/response pair it handles.

    A span needs the request's cache_key and prompt text, and the response's
    model/source/tokens/raw text. The only way to get those without changing
    the step functions -- which take a client, not a request/response pair --
    is to watch the calls from outside.
    """

    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.model = getattr(inner, "model", "mock")
        self.calls: list[tuple[LLMRequest, LLMResponse]] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        response = self.inner.complete(request)
        self.calls.append((request, response))
        return response


@dataclass
class _TraceContext:
    recorder: RecordingLLMClient
    spans: list[Span] = field(default_factory=list)


_ACTIVE: ContextVar[_TraceContext | None] = ContextVar("_ACTIVE", default=None)


@contextmanager
def trace_context(recorder: RecordingLLMClient):
    """Activate span collection for the steps called within this block."""
    ctx = _TraceContext(recorder=recorder)
    token = _ACTIVE.set(ctx)
    try:
        yield ctx.spans
    finally:
        _ACTIVE.reset(token)


def _serialize_input(fn: Callable, args: tuple, kwargs: dict) -> dict[str, Any] | None:
    """Best-effort JSON-safe snapshot of a step's arguments.

    Only Pydantic payloads and plain scalars are kept. An LLMClient argument
    (mock, replay, or live) is neither, and is silently dropped -- it is not
    part of "what this step received" in any sense worth persisting.
    """
    try:
        bound = inspect.signature(fn).bind(*args, **kwargs)
    except TypeError:
        return None
    snapshot: dict[str, Any] = {}
    for name, value in bound.arguments.items():
        if isinstance(value, BaseModel):
            snapshot[name] = value.model_dump(mode="json")
        elif value is None or isinstance(value, (str, int, float, bool)):
            snapshot[name] = value
    return snapshot or None


def _make_span(step: str, latency_ms: float, snapshot: dict | None, output: Any,
                error: StepError | None, call: tuple[LLMRequest, LLMResponse] | None) -> Span:
    request, response = call if call else (None, None)
    return Span(
        step=step,
        ok=error is None,
        latency_ms=latency_ms,
        input=snapshot,
        cache_key=request.cache_key() if request else None,
        model=response.model if response else "",
        source=response.source if response else "",
        prompt=f"SYSTEM:\n{request.system}\n\nUSER:\n{request.user}" if request else None,
        prompt_tokens=response.prompt_tokens if response else 0,
        completion_tokens=response.completion_tokens if response else 0,
        confidence=getattr(output, "confidence", None),
        output=output,
        error_message=error.message if error else None,
        raw_response=response.text if response else (error.raw_response if error else None),
    )


def traced_step(name: str) -> Callable[[F], F]:
    """Decorator: makes a step function contribute a Span when a trace is
    active, and a plain, untouched call when it is not."""

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            ctx = _ACTIVE.get()
            if ctx is None:
                return fn(*args, **kwargs)

            snapshot = _serialize_input(fn, args, kwargs)
            before = len(ctx.recorder.calls)
            started = time.perf_counter()
            with otel.span(f"forensics.step.{name}") as otel_span:
                try:
                    output = fn(*args, **kwargs)
                    error = None
                except StepError as exc:
                    output, error = None, exc
                latency_ms = (time.perf_counter() - started) * 1000
                call = ctx.recorder.calls[-1] if len(ctx.recorder.calls) > before else None
                span = _make_span(name, latency_ms, snapshot, output, error, call)
                ctx.spans.append(span)

                otel_span.set_attribute("forensics.step", name)
                otel_span.set_attribute("forensics.latency_ms", latency_ms)
                if span.confidence is not None:
                    otel_span.set_attribute("forensics.confidence", span.confidence)
                if span.model:
                    otel_span.set_attribute("forensics.model", span.model)
                if span.cache_key:
                    otel_span.set_attribute("forensics.cache_key", span.cache_key)
                if error is not None:
                    otel.mark_error(otel_span, error.message, error)

            if error is not None:
                raise error
            return output

        return wrapper  # type: ignore[return-value]

    return decorator

"""Phase 2: the same four steps as runner.py, wrapped for observability.

Deliberately a separate module rather than a change to run_pipeline: the
runner stays the "no timing, no logging, no error swallowing" call chain
tests reach for when they only care about the result, and this is what you
reach for when you need to see what happened even on a failing run.
"""

from __future__ import annotations

import time

from ..errors import StepError
from ..llm import LLMClient, LLMRequest, LLMResponse, build_client
from ..models import PipelineResult, Span, Trace
from .classification import classify
from .extraction import extract
from .intake import intake
from .summarization import summarize


class RecordingLLMClient:
    """Wraps a client and keeps every request/response pair it handles.

    This is the "recording client" runner.py's docstring anticipates. A span
    needs the request's cache_key and the response's model/source/token
    fields, and the only way to get them without changing the step functions
    -- which take a client, not a request/response pair -- is to watch the
    calls from outside.
    """

    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.model = getattr(inner, "model", "mock")
        self.calls: list[tuple[LLMRequest, LLMResponse]] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        response = self.inner.complete(request)
        self.calls.append((request, response))
        return response


def _make_span(step: str, latency_ms: float, output, error: StepError | None,
                call: tuple[LLMRequest, LLMResponse] | None) -> Span:
    request, response = call if call else (None, None)
    return Span(
        step=step,
        ok=error is None,
        latency_ms=latency_ms,
        cache_key=request.cache_key() if request else (error.cache_key if error else None),
        model=response.model if response else "",
        source=response.source if response else "",
        prompt_tokens=response.prompt_tokens if response else 0,
        completion_tokens=response.completion_tokens if response else 0,
        output=output,
        error_message=error.message if error else None,
        raw_response=error.raw_response if error else None,
    )


def run_traced_pipeline(raw_text: str, source_name: str,
                        client: LLMClient | None = None) -> Trace:
    """Run all four steps, producing a Trace instead of raising.

    Same steps, same order, same client contract as run_pipeline -- a failing
    step ends the trace instead of the process, with every span up to and
    including the failure preserved.
    """
    recorder = RecordingLLMClient(client or build_client())
    spans: list[Span] = []

    def run_step(name: str, fn):
        before = len(recorder.calls)
        started = time.perf_counter()
        try:
            output = fn()
            error = None
        except StepError as exc:
            output, error = None, exc
        latency_ms = (time.perf_counter() - started) * 1000
        call = recorder.calls[-1] if len(recorder.calls) > before else None
        spans.append(_make_span(name, latency_ms, output, error, call))
        return output

    def failed() -> Trace:
        return Trace(source_name=source_name, spans=tuple(spans), ok=False, result=None)

    document = run_step("intake", lambda: intake(raw_text, source_name))
    if document is None:
        return failed()

    extraction = run_step("extraction", lambda: extract(document, recorder))
    if extraction is None:
        return failed()

    classification = run_step("classification", lambda: classify(document, extraction, recorder))
    if classification is None:
        return failed()

    summary = run_step("summarization", lambda: summarize(document, extraction, classification, recorder))
    if summary is None:
        return failed()

    result = PipelineResult(
        document=document, extraction=extraction,
        classification=classification, summary=summary,
    )
    return Trace(source_name=source_name, spans=tuple(spans), ok=True, result=result)

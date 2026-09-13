"""Orchestrates a traced run of the four steps.

The steps themselves (intake, extract, classify, summarize) are already
instrumented -- each carries its own @traced_step decorator from
forensics.tracing. This module's only job is to open a trace context around
an ordinary call to those steps, in the same order and with the same
arguments as run_pipeline, and turn what came out of the context into a Trace.
"""

from __future__ import annotations

from .. import otel
from ..analysis import find_propagation_drop
from ..errors import StepError
from ..llm import LLMClient, build_client
from ..models import PipelineResult, Span, Trace, TraceStatus
from ..tracing import RecordingLLMClient, trace_context
from .classification import classify
from .extraction import extract
from .intake import intake
from .summarization import summarize


def _status(ok: bool, result: PipelineResult | None, spans: tuple[Span, ...]) -> TraceStatus:
    if not ok:
        return TraceStatus.FAILURE
    assert result is not None
    degraded = (
        bool(result.extraction.ungrounded(result.document))
        or result.classification.is_ambiguous
        or bool(result.summary.dropped_facts(result.extraction, result.document))
        or find_propagation_drop(spans) is not None
    )
    return TraceStatus.DEGRADED if degraded else TraceStatus.SUCCESS


def run_traced_pipeline(raw_text: str, source_name: str,
                        client: LLMClient | None = None) -> Trace:
    """Run all four steps, producing a Trace instead of raising.

    Same steps, same order, same client contract as run_pipeline -- a failing
    step ends the trace instead of the process, with every span up to and
    including the failure preserved.
    """
    recorder = RecordingLLMClient(client or build_client())

    with otel.span("forensics.pipeline.run", **{"forensics.source_name": source_name}):
        with trace_context(recorder) as spans:
            document = None
            result = None
            ok = True
            try:
                document = intake(raw_text, source_name)
                extraction = extract(document, recorder)
                classification = classify(document, extraction, recorder)
                summary = summarize(document, extraction, classification, recorder)
                result = PipelineResult(
                    document=document, extraction=extraction,
                    classification=classification, summary=summary,
                )
            except StepError:
                ok = False

    return Trace(
        source_name=source_name,
        doc_id=document.doc_id if document else None,
        spans=tuple(spans),
        status=_status(ok, result, tuple(spans)),
        result=result,
    )

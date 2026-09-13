"""Phase 2: a failing run should be exactly as inspectable as a successful one."""

from forensics.llm import MockLLM
from forensics.pipeline import run_pipeline
from forensics.pipeline.tracing import run_traced_pipeline

INVOICE = """INVOICE 2201
From: Acme Corp
Bill to: Globex Ltd

Consulting services, Q1 2026.
Amount due: $4,500.00
Payment terms: net 30. Due 2026-04-01.

Regards,
Priya Sharma
"""


def test_successful_trace_matches_the_untraced_pipeline():
    trace = run_traced_pipeline(INVOICE, "i.txt", client=MockLLM())
    direct = run_pipeline(INVOICE, "i.txt", client=MockLLM())

    assert trace.ok
    # Everything except ingested_at, which is a wall-clock stamp and genuinely
    # differs between two separate runs.
    assert trace.result.document.doc_id == direct.document.doc_id
    assert trace.result.extraction == direct.extraction
    assert trace.result.classification == direct.classification
    assert trace.result.summary == direct.summary
    assert [s.step for s in trace.spans] == [
        "intake", "extraction", "classification", "summarization",
    ]
    assert all(s.ok for s in trace.spans)


def test_intake_span_carries_no_llm_metadata():
    trace = run_traced_pipeline(INVOICE, "i.txt", client=MockLLM())
    intake_span = trace.spans[0]

    assert intake_span.cache_key is None
    assert intake_span.model == ""
    assert intake_span.prompt_tokens == 0


def test_llm_backed_spans_carry_the_request_cache_key_and_model():
    trace = run_traced_pipeline(INVOICE, "i.txt", client=MockLLM())
    extraction_span = trace.spans[1]

    assert extraction_span.cache_key
    assert extraction_span.model == "mock"
    assert extraction_span.source == "mock"
    assert extraction_span.output == trace.result.extraction


def test_a_failing_step_ends_the_trace_but_keeps_every_span_so_far():
    trace = run_traced_pipeline(INVOICE, "i.txt", client=MockLLM(chaos={"bad_json"}))

    assert not trace.ok
    assert trace.result is None
    assert [s.step for s in trace.spans] == ["intake", "extraction"]
    assert trace.spans[0].ok
    assert not trace.spans[-1].ok


def test_the_failing_span_preserves_the_evidence_a_stepError_carries():
    """The whole point of tracing a failure: the raw response and cache key
    that would otherwise vanish with the exception are still on the span."""
    trace = run_traced_pipeline(INVOICE, "i.txt", client=MockLLM(chaos={"bad_json"}))
    failed_span = trace.spans[-1]

    assert failed_span.error_message
    assert "could not find anything useful" in failed_span.raw_response
    assert failed_span.cache_key

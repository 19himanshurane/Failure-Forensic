"""Phase 2: a failing run should be exactly as inspectable as a successful one."""

from forensics.llm import MockLLM
from forensics.models import TraceStatus
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


def test_every_trace_gets_a_unique_id_and_the_document_id():
    a = run_traced_pipeline(INVOICE, "i.txt", client=MockLLM())
    b = run_traced_pipeline(INVOICE, "i.txt", client=MockLLM())

    assert a.trace_id and b.trace_id and a.trace_id != b.trace_id
    assert a.doc_id == a.result.document.doc_id


def test_status_is_success_when_nothing_was_flagged():
    # Short enough that the mock's crude "first few lines" summary genuinely
    # covers every fact it extracted -- INVOICE is a bad fixture for this
    # because the mock's own summarizer drops "Priya Sharma" and the due date
    # by only keeping the first four lines, which is real (if unrelated)
    # context loss, not a false positive from this test.
    clean_doc = (
        "Hello team,\n"
        "Please note that Acme Corp will meet Globex Ltd on 2026-04-01.\n"
        "Thanks,\n"
        "Priya Sharma\n"
    )
    trace = run_traced_pipeline(clean_doc, "i.txt", client=MockLLM())
    assert trace.status is TraceStatus.SUCCESS


def test_status_is_failure_when_a_step_raises():
    trace = run_traced_pipeline(INVOICE, "i.txt", client=MockLLM(chaos={"bad_json"}))
    assert trace.status is TraceStatus.FAILURE
    assert not trace.ok
    # intake succeeded before extraction failed, so the doc_id is still known
    # -- a failure further downstream should not erase what earlier steps learned.
    assert trace.doc_id


def test_status_is_degraded_when_a_detector_fires_but_the_run_completes():
    """A hallucinated entity doesn't stop the pipeline -- it produces a result
    that should not be trusted blindly, which is exactly what DEGRADED means."""
    trace = run_traced_pipeline(INVOICE, "i.txt", client=MockLLM(chaos={"hallucinate"}))
    assert trace.status is TraceStatus.DEGRADED
    assert trace.ok  # a result exists, unlike an outright FAILURE


def test_span_captures_the_serialized_input_and_full_prompt():
    trace = run_traced_pipeline(INVOICE, "i.txt", client=MockLLM())
    extraction_span = trace.spans[1]

    assert extraction_span.input["document"]["source_name"] == "i.txt"
    assert "INVOICE 2201" in extraction_span.prompt


def test_span_carries_the_steps_own_confidence_score():
    trace = run_traced_pipeline(INVOICE, "i.txt", client=MockLLM())
    extraction_span = trace.spans[1]

    assert extraction_span.confidence == trace.result.extraction.confidence


def test_raw_response_is_captured_even_on_a_healthy_span():
    """Phase 2 asked for the raw response every time, not only on failure."""
    trace = run_traced_pipeline(INVOICE, "i.txt", client=MockLLM())
    extraction_span = trace.spans[1]

    assert extraction_span.ok
    assert extraction_span.raw_response

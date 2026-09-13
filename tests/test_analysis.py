"""Phase 3: each named failure category should be diagnosable from its trace,
and the diagnosis should point at the step that actually caused it."""

from datetime import datetime, timezone

from forensics.analysis import diagnose
from forensics.llm import MockLLM
from forensics.models import (
    Classification, Document, DocumentType, ExtractionResult,
    FailureCategory, Span, Summary, Trace, TraceStatus,
)
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

# INVOICE is DEGRADED even under the plain mock (see test_tracing.py): its
# crude summarizer only keeps the first four lines, dropping "Priya Sharma"
# and the due date regardless of chaos. CLEAN_DOC fits within that window, so
# it isolates whatever chaos flag is under test as the only problem present.
CLEAN_DOC = (
    "Hello team,\n"
    "Please note that Acme Corp will meet Globex Ltd on 2026-04-01.\n"
    "Thanks,\n"
    "Priya Sharma\n"
)


def test_healthy_trace_has_no_diagnosis():
    trace = run_traced_pipeline(CLEAN_DOC, "i.txt", client=MockLLM())
    assert trace.status is TraceStatus.SUCCESS
    assert diagnose(trace) is None


def test_prompt_failure_is_diagnosed_at_the_failing_step():
    trace = run_traced_pipeline(INVOICE, "i.txt", client=MockLLM(chaos={"bad_json"}))
    diagnosis = diagnose(trace)

    assert diagnosis.category is FailureCategory.PROMPT_FAILURE
    assert diagnosis.step == "extraction"
    assert diagnosis.evidence


def test_extraction_hallucination_is_diagnosed_with_the_invented_entity_as_evidence():
    trace = run_traced_pipeline(CLEAN_DOC, "i.txt", client=MockLLM(chaos={"hallucinate"}))
    diagnosis = diagnose(trace)

    assert diagnosis.category is FailureCategory.EXTRACTION_HALLUCINATION
    assert diagnosis.step == "extraction"
    assert "John Smith" in diagnosis.explanation


def test_misclassification_is_diagnosed_at_classification():
    trace = run_traced_pipeline(CLEAN_DOC, "i.txt", client=MockLLM(chaos={"misclassify"}))
    diagnosis = diagnose(trace)

    assert diagnosis.category is FailureCategory.MISCLASSIFICATION
    assert diagnosis.step == "classification"


def test_context_loss_is_diagnosed_at_summarization():
    trace = run_traced_pipeline(INVOICE, "i.txt", client=MockLLM(chaos={"drop_context"}))
    diagnosis = diagnose(trace)

    assert diagnosis.category is FailureCategory.CONTEXT_LOSS
    assert diagnosis.step == "summarization"


def _healthy_result() -> tuple[Document, ExtractionResult, Classification, Summary]:
    doc = Document(
        doc_id="d1", source_name="t.txt",
        raw_text="Acme Corp will pay Globex Ltd on 2026-04-01.",
        ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    extraction = ExtractionResult(confidence=5, reasoning="fine")
    classification = Classification(
        doc_type=DocumentType.CONTRACT, runner_up=DocumentType.UNKNOWN,
        margin=1.0, confidence=2, reasoning="fine",
    )
    summary = Summary(
        headline="Acme Corp will pay Globex Ltd", bullets=(),
        tailored_for=DocumentType.CONTRACT, confidence=3,
    )
    return doc, extraction, classification, summary


def test_propagation_error_is_diagnosed_from_a_sharp_confidence_drop():
    """No mock chaos flag produces this on its own -- it's a relationship
    between two steps' confidence, not a single bad field, so it's exercised
    directly against a hand-built trace, the same way test_models.py checks
    the mechanical detectors without going through the pipeline."""
    doc, extraction, classification, summary = _healthy_result()
    from forensics.models import PipelineResult
    result = PipelineResult(
        document=doc, extraction=extraction,
        classification=classification, summary=summary,
    )
    spans = (
        Span(step="intake", ok=True, latency_ms=0.1, output=doc),
        Span(step="extraction", ok=True, latency_ms=1.0, confidence=5, output=extraction),
        # Confidence falls from 5 to 2: extraction was confident, classification was not.
        Span(step="classification", ok=True, latency_ms=1.0, confidence=2, output=classification),
        Span(step="summarization", ok=True, latency_ms=1.0, confidence=3, output=summary),
    )
    trace = Trace(source_name="t.txt", doc_id=doc.doc_id, spans=spans,
                  status=TraceStatus.DEGRADED, result=result)

    diagnosis = diagnose(trace)

    assert diagnosis.category is FailureCategory.PROPAGATION_ERROR
    assert diagnosis.step == "classification"

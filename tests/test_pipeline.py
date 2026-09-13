"""End-to-end pipeline tests against the mock."""

import pytest

from forensics.errors import StepError
from forensics.llm import MockLLM
from forensics.models import DocumentType
from forensics.pipeline import extract, intake, run_pipeline

INVOICE = """INVOICE 2201
From: Acme Corp
Bill to: Globex Ltd

Consulting services, Q1 2026.
Amount due: $4,500.00
Handling: EUR 300
Payment terms: net 30. Due 2026-04-01.

Regards,
Priya Sharma
"""


# -- step 1: intake ----------------------------------------------------------

def test_intake_rejects_empty_input():
    with pytest.raises(StepError, match="empty"):
        intake("   \n  ", "blank.txt")


def test_intake_rejects_input_too_short_to_process():
    with pytest.raises(StepError, match="too short"):
        intake("hello", "tiny.txt")


def test_doc_id_is_content_addressed():
    """Same text always gets the same id, so Phase 5 can recognise a repeat case."""
    a = intake(INVOICE, "a.txt")
    b = intake(INVOICE, "b-different-filename.txt")
    assert a.doc_id == b.doc_id


def test_line_endings_are_canonicalised():
    doc = intake("Windows text here, long enough to pass.\r\nSecond line.\r\n", "w.txt")
    assert "\r" not in doc.raw_text


# -- the false-positive regression ------------------------------------------

def test_no_false_hallucinations_on_a_clean_document():
    """Regression: the mock used to quote across the prompt fences, so entities
    that really were in the document were reported as hallucinations."""
    result = run_pipeline(INVOICE, "invoice.txt", client=MockLLM())
    assert result.extraction.ungrounded(result.document) == ()


def test_summary_describes_the_document_not_the_prompt():
    """Regression: the mock used to summarise the instruction text."""
    result = run_pipeline(INVOICE, "invoice.txt", client=MockLLM())
    assert "Document type:" not in result.summary.headline
    assert "INVOICE 2201" in result.summary.headline


# -- the pipeline as a whole -------------------------------------------------

def test_full_pipeline_produces_a_complete_result():
    r = run_pipeline(INVOICE, "invoice.txt", client=MockLLM())
    assert r.classification.doc_type is DocumentType.INVOICE
    assert r.summary.tailored_for is r.classification.doc_type
    assert r.extraction.currencies() == {"USD", "EUR"}


def test_pipeline_is_deterministic_under_the_mock():
    a = run_pipeline(INVOICE, "i.txt", client=MockLLM())
    b = run_pipeline(INVOICE, "i.txt", client=MockLLM())
    assert a.summary == b.summary and a.extraction == b.extraction


# -- failures surface as StepError, carrying the raw response ----------------

def test_unparseable_response_raises_with_the_evidence_attached():
    with pytest.raises(StepError) as exc:
        run_pipeline(INVOICE, "i.txt", client=MockLLM(chaos={"bad_json"}))
    assert exc.value.step == "extraction"
    assert exc.value.raw_response          # the raw text is preserved for the span
    assert exc.value.cache_key


def test_chaos_hallucination_is_caught_end_to_end():
    r = run_pipeline(INVOICE, "i.txt", client=MockLLM(chaos={"hallucinate"}))
    assert [e.value for e in r.extraction.ungrounded(r.document)] == ["John Smith"]


def test_chaos_drop_context_loses_facts_a_clean_summary_keeps():
    """Regression: truncating the summary should be distinguishable from step 2
    never having found the fact -- the same INVOICE extracts the same entities
    either way, only step 4's output differs."""
    clean = run_pipeline(INVOICE, "i.txt", client=MockLLM())
    chaos = run_pipeline(INVOICE, "i.txt", client=MockLLM(chaos={"drop_context"}))

    clean_dropped = {e.value for e in clean.summary.dropped_facts(clean.extraction)}
    chaos_dropped = {e.value for e in chaos.summary.dropped_facts(chaos.extraction)}

    assert {"Globex Ltd", "$4,500.00"} & clean_dropped == set()
    assert {"Globex Ltd", "$4,500.00"} <= chaos_dropped


def test_chaos_misclassification_is_wrong_and_flagged_ambiguous():
    r = run_pipeline(INVOICE, "i.txt", client=MockLLM(chaos={"misclassify"}))
    assert r.classification.doc_type is not DocumentType.INVOICE
    assert r.classification.is_ambiguous
    # And the damage propagates: step 4 was told to write the wrong kind of summary.
    assert r.summary.tailored_for is r.classification.doc_type

"""These tests do not check that Pydantic works. They check that each failure
mode from the spec is *representable and detectable* at the type level."""

from datetime import datetime, date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from forensics.models import (
    Classification, DateEntity, Document, DocumentType,
    Entity, ExtractionResult, MoneyEntity, Summary,
)


def make_doc(text: str) -> Document:
    return Document(
        doc_id="d1", source_name="t.txt", raw_text=text,
        ingested_at=datetime(2026, 1, 1),
    )


def test_grounded_entity_is_found_in_source():
    doc = make_doc("This agreement is between Acme Corp and Globex.")
    e = Entity(value="Acme Corp", source_quote="between Acme Corp and", confidence=5)
    assert e.is_grounded(doc)


def test_hallucinated_entity_is_detected_mechanically():
    """The core trick: a quote that is not in the source means the model made it up."""
    doc = make_doc("This agreement is between Acme Corp and Globex.")
    real = Entity(value="Acme Corp", source_quote="Acme Corp", confidence=5)
    fake = Entity(value="John Smith", source_quote="signed by John Smith", confidence=4)
    result = ExtractionResult(people=(real, fake), confidence=4)

    assert result.ungrounded(doc) == (fake,)


def test_grounding_survives_whitespace_and_case_differences():
    doc = make_doc("Payment due\n  within   30 DAYS of invoice.")
    e = Entity(value="30 days", source_quote="within 30 days of invoice", confidence=4)
    assert e.is_grounded(doc)


def test_mixed_currency_invoice_is_visible():
    """Failure mode: amounts in different currencies must not be summed naively."""
    result = ExtractionResult(
        amounts=(
            MoneyEntity(value="1000", source_quote="$1,000", amount=Decimal("1000"), currency="usd"),
            MoneyEntity(value="900", source_quote="€900", amount=Decimal("900"), currency="EUR"),
        ),
        confidence=4,
    )
    assert result.currencies() == {"USD", "EUR"}  # validator normalised the case


def test_contract_with_no_dates_is_representable():
    """Failure mode: an empty date list is legal, so downstream steps must cope."""
    result = ExtractionResult(dates=(), confidence=2)
    assert result.dates == ()


def test_unresolvable_date_phrase_keeps_its_quote():
    d = DateEntity(value="upon delivery", source_quote="due upon delivery", iso_date=None, confidence=2)
    assert d.iso_date is None and d.source_quote


def test_ambiguous_classification_is_flagged():
    """Failure mode: a document sitting between two categories."""
    c = Classification(
        doc_type=DocumentType.INVOICE, runner_up=DocumentType.CORRESPONDENCE,
        margin=0.05, confidence=2,
    )
    assert c.is_ambiguous


def test_dropped_fact_is_detected_mechanically():
    """Failure mode: step 2 found it, step 4 lost it. Mirrors the hallucination
    trick, but checks the summary against extraction instead of the source."""
    kept = Entity(value="Acme Corp", source_quote="Acme Corp", confidence=4)
    lost = Entity(value="Globex Ltd", source_quote="Globex Ltd", confidence=4)
    extraction = ExtractionResult(organizations=(kept, lost), confidence=4)
    summary = Summary(
        headline="Invoice from Acme Corp", bullets=(), tailored_for=DocumentType.INVOICE, confidence=3,
    )

    assert summary.dropped_facts(extraction) == (lost,)


def test_low_confidence_facts_are_not_flagged_as_dropped():
    """A shaky extraction that never made it into the summary is not the same
    failure as a solid one that did; only the latter is worth surfacing."""
    shaky = Entity(value="Globex Ltd", source_quote="Globex Ltd", confidence=2)
    extraction = ExtractionResult(organizations=(shaky,), confidence=2)
    summary = Summary(
        headline="Invoice", bullets=(), tailored_for=DocumentType.INVOICE, confidence=3,
    )

    assert summary.dropped_facts(extraction) == ()


def test_confidence_outside_1_to_5_is_rejected():
    with pytest.raises(ValidationError):
        ExtractionResult(confidence=9)


def test_unexpected_field_from_the_llm_fails_loudly():
    """extra="forbid" catches invented fields at the step that produced them."""
    with pytest.raises(ValidationError):
        Classification(doc_type=DocumentType.REPORT, margin=0.5, confidence=3, vibes="good")


def test_payloads_are_immutable():
    doc = make_doc("hello")
    with pytest.raises(ValidationError):
        doc.raw_text = "tampered"

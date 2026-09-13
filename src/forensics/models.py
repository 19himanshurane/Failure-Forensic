"""Typed boundaries for every step of the pipeline.

Every value that crosses a step boundary is a model defined here. That is not
decoration. In Phase 2 the tracer serialises these objects verbatim into spans,
so these definitions *are* the schema of the trace data. If a boundary is an
untyped dict, the span that records it is an untyped blob, and the root-cause
analyser in Phase 3 has nothing to reason about.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

# The model reports how sure it is about its own output, 1 (guess) to 5 (certain).
# Phase 3 treats low-confidence spans as the primary suspects when tracing backward.
Confidence = Annotated[int, Field(ge=1, le=5)]


def normalise(text: str) -> str:
    """Collapse whitespace and case so quotes can be matched against source text."""
    return " ".join(text.split()).casefold()


class Payload(BaseModel):
    """Base class for anything that crosses a step boundary.

    extra="forbid": if an LLM invents a field we did not ask for, construction
        fails loudly at the step that produced it, rather than silently carrying
        junk downstream where the real cause is three steps back.
    frozen=True: once a step emits a value it cannot be mutated. The whole
        premise of this tool is that a recorded span is what actually happened,
        so values must not be editable after the fact.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class DocumentType(str, Enum):
    CONTRACT = "contract"
    INVOICE = "invoice"
    REPORT = "report"
    CORRESPONDENCE = "correspondence"
    # Deliberate: a model forced to choose between four labels will confidently
    # mislabel an ambiguous document. Letting it abstain makes uncertainty
    # visible in the trace instead of hiding it behind a wrong answer.
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------
# Step 1 boundary: raw text -> Document
# --------------------------------------------------------------------------

class Document(Payload):
    doc_id: str
    source_name: str
    raw_text: str
    ingested_at: datetime

    @property
    def char_count(self) -> int:
        return len(self.raw_text)


# --------------------------------------------------------------------------
# Step 2 boundary: Document -> ExtractionResult
# --------------------------------------------------------------------------

class Entity(Payload):
    """A single extracted fact, carrying the source text it came from.

    source_quote is the load-bearing field. Requiring the model to quote the
    document verbatim turns hallucination detection into a substring check
    instead of a judgement call. An entity whose quote is not in the source
    was invented.
    """

    value: str
    source_quote: str
    confidence: Confidence = 3

    def is_grounded(self, document: Document) -> bool:
        if not self.source_quote.strip():
            return False
        return normalise(self.source_quote) in normalise(document.raw_text)


class DateEntity(Entity):
    # None when the document says something like "upon delivery" that reads as a
    # date but resolves to nothing. Failure mode: a contract with no real dates.
    iso_date: date | None = None


class MoneyEntity(Entity):
    amount: Decimal
    currency: str = Field(min_length=3, max_length=3, description="ISO 4217, e.g. USD")

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


class ExtractionResult(Payload):
    people: tuple[Entity, ...] = ()
    organizations: tuple[Entity, ...] = ()
    dates: tuple[DateEntity, ...] = ()
    amounts: tuple[MoneyEntity, ...] = ()
    key_terms: tuple[Entity, ...] = ()
    confidence: Confidence
    reasoning: str = ""

    def all_entities(self) -> tuple[Entity, ...]:
        return (
            *self.people, *self.organizations, *self.dates,
            *self.amounts, *self.key_terms,
        )

    def ungrounded(self, document: Document) -> tuple[Entity, ...]:
        """Entities whose quote does not appear in the source: hallucinations."""
        return tuple(e for e in self.all_entities() if not e.is_grounded(document))

    def currencies(self) -> frozenset[str]:
        """More than one currency means totals must not be naively summed."""
        return frozenset(a.currency for a in self.amounts)


# --------------------------------------------------------------------------
# Step 3 boundary: Document + ExtractionResult -> Classification
# --------------------------------------------------------------------------

class Classification(Payload):
    doc_type: DocumentType
    runner_up: DocumentType | None = None
    # How clearly the winner beat the runner-up. A near-zero margin is the
    # signature of the "ambiguous between two categories" failure mode, and is
    # what Phase 3 looks for when diagnosing a misclassification.
    margin: float = Field(ge=0.0, le=1.0)
    confidence: Confidence
    reasoning: str = ""

    @property
    def is_ambiguous(self) -> bool:
        return self.margin < 0.2


# --------------------------------------------------------------------------
# Step 4 boundary: everything above -> Summary
# --------------------------------------------------------------------------

class Summary(Payload):
    headline: str
    bullets: tuple[str, ...]
    tailored_for: DocumentType
    confidence: Confidence
    reasoning: str = ""

    def dropped_facts(self, extraction: ExtractionResult, min_confidence: int = 3) -> tuple[Entity, ...]:
        """Entities step 2 extracted with confidence but this summary never mentions.

        Only entities extraction actually returned are considered, which is what
        separates this from step 2 never finding the fact in the first place: a
        hit here means the fact existed and was lost in step 4 -- the "Context
        Loss" failure mode described where extraction is passed into summarize().
        """
        haystack = normalise(f"{self.headline} {' '.join(self.bullets)}")
        return tuple(
            e for e in extraction.all_entities()
            if e.confidence >= min_confidence and normalise(e.value) not in haystack
        )


class PipelineResult(Payload):
    """The happy path only.

    Partial and failed runs are represented by the Trace below, not here. This
    type deliberately cannot express a broken run.
    """

    document: Document
    extraction: ExtractionResult
    classification: Classification
    summary: Summary


# --------------------------------------------------------------------------
# Phase 2: tracing
# --------------------------------------------------------------------------

class Span(Payload):
    """One step's contribution to a trace.

    Success and failure are both first-class: `output` is set when the step
    returned a value, `error_message`/`raw_response` when it raised a
    StepError instead -- the same evidence the exception carries, so tracing
    loses nothing when it catches what the caller would otherwise have to.
    cache_key/model/source/tokens are blank for intake, which never calls a
    model.
    """

    step: str
    ok: bool
    latency_ms: float
    cache_key: str | None = None
    model: str = ""
    source: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    output: Document | ExtractionResult | Classification | Summary | None = None
    error_message: str | None = None
    raw_response: str | None = None


class Trace(Payload):
    """A full pipeline run, successful or not.

    This is what a root-cause analyser reasons over: the ordered spans let it
    ask which step first produced something wrong, rather than only seeing
    where the run stopped.
    """

    source_name: str
    spans: tuple[Span, ...]
    ok: bool
    result: PipelineResult | None = None

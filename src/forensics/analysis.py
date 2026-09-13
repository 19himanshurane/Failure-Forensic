"""Phase 3: backward root-cause analysis.

The spec's algorithm: walk a failed or degraded trace backward from its last
span toward its first, and at each step ask "is this step's output a
reasonable transformation of its input?". The first step (in that backward
scan -- i.e. the *latest* one chronologically) that fails that question is the
root cause.

That question turns out to already have a mechanical answer for four of the
five named failure categories, because each existing detector checks exactly
one step's output against exactly that step's own input:

  EXTRACTION_HALLUCINATION  extraction.ungrounded(document)   -- output vs the
                            document extraction itself was given.
  MISCLASSIFICATION         classification.is_ambiguous       -- intrinsic to
                            the classification step's own scores.
  CONTEXT_LOSS              summary.dropped_facts(extraction) -- output vs the
                            extraction summarization was given.
  PROMPT_FAILURE            a StepError already raised          -- the step
                            could not even produce a well-formed output.

PROPAGATION_ERROR is the one case with no existing single-field check, because
by definition it is a *relationship* between two steps: step N's own output
looked fine, but step N+1 -- given exactly that output -- did noticeably
worse. Confidence is the one signal every step reports about itself, so a
sharp drop in a step's self-reported confidence relative to the step before
it, with no other category already explaining that step, is treated as
propagation.
"""

from __future__ import annotations

from .models import Diagnosis, FailureCategory, Span, Trace, TraceStatus

# A drop of this many points (on the 1-5 scale) from one LLM-backed step's
# confidence to the next, unexplained by any other detector, counts as the
# receiving step visibly struggling with input it was not equipped to handle.
CONFIDENCE_DROP_THRESHOLD = 2

_LLM_STEPS = ("extraction", "classification", "summarization")


def _llm_spans(spans: tuple[Span, ...]) -> list[Span]:
    return [s for s in spans if s.step in _LLM_STEPS]


def find_propagation_drop(spans: tuple[Span, ...]) -> Span | None:
    """The first LLM-backed span whose confidence dropped sharply from the
    step before it. Used both to mark a trace DEGRADED and, in diagnose(), as
    the PROPAGATION_ERROR evidence."""
    ordered = _llm_spans(spans)
    for prev, current in zip(ordered, ordered[1:]):
        if prev.confidence is None or current.confidence is None:
            continue
        if prev.confidence - current.confidence >= CONFIDENCE_DROP_THRESHOLD:
            return current
    return None


def _step_problem(step: str, trace: Trace) -> Diagnosis | None:
    result = trace.result
    assert result is not None

    if step == "extraction":
        bad = result.extraction.ungrounded(result.document)
        if not bad:
            return None
        names = ", ".join(repr(e.value) for e in bad)
        return Diagnosis(
            category=FailureCategory.EXTRACTION_HALLUCINATION,
            step="extraction",
            explanation=(
                f"Step 2 (Extraction) invented {len(bad)} entit{'y' if len(bad) == 1 else 'ies'} "
                f"that do not appear in the source document: {names}."
            ),
            evidence=tuple(
                f"{e.value!r} is quoted as {e.source_quote!r}, which is not found in the document"
                for e in bad
            ),
        )

    if step == "classification":
        c = result.classification
        if not c.is_ambiguous:
            return None
        runner_up = c.runner_up.value if c.runner_up else "the runner-up"
        return Diagnosis(
            category=FailureCategory.MISCLASSIFICATION,
            step="classification",
            explanation=(
                f"Step 3 (Classification) chose {c.doc_type.value} over {runner_up} "
                f"by a margin of only {c.margin:.2f} -- too close to call reliably."
            ),
            evidence=(c.reasoning,) if c.reasoning else (),
        )

    if step == "summarization":
        dropped = result.summary.dropped_facts(result.extraction, result.document)
        if not dropped:
            return None
        names = ", ".join(repr(e.value) for e in dropped)
        return Diagnosis(
            category=FailureCategory.CONTEXT_LOSS,
            step="summarization",
            explanation=(
                f"Step 4 (Summarization) dropped {len(dropped)} fact(s) that step 2 had "
                f"already extracted with confidence: {names}."
            ),
            evidence=tuple(
                f"{e.value!r} was extracted but never appears in the final summary"
                for e in dropped
            ),
        )

    return None


def diagnose(trace: Trace) -> Diagnosis | None:
    """Walk the trace backward and return the root-cause diagnosis, or None
    if nothing was wrong with it (status SUCCESS)."""
    if trace.status is TraceStatus.SUCCESS:
        return None

    if trace.status is TraceStatus.FAILURE:
        failing = next(s for s in reversed(trace.spans) if not s.ok)
        return Diagnosis(
            category=FailureCategory.PROMPT_FAILURE,
            step=failing.step,
            explanation=(
                f"Step {failing.step!r} raised an error instead of a usable result: "
                f"{failing.error_message}"
            ),
            evidence=(failing.raw_response,) if failing.raw_response else (),
        )

    # DEGRADED: walk backward through the mechanical, per-step checks first.
    for step in reversed(_LLM_STEPS):
        found = _step_problem(step, trace)
        if found:
            return found

    # No mechanical detector fired but the trace was still marked DEGRADED --
    # only find_propagation_drop() can be responsible for that classification.
    drop = find_propagation_drop(trace.spans)
    if drop:
        prev_confidence = next(
            s.confidence for s in reversed(trace.spans[: trace.spans.index(drop)])
            if s.step in _LLM_STEPS
        )
        return Diagnosis(
            category=FailureCategory.PROPAGATION_ERROR,
            step=drop.step,
            explanation=(
                f"Step {drop.step!r} received input from a step that scored its own "
                f"confidence at {prev_confidence}, but {drop.step}'s confidence fell to "
                f"{drop.confidence} -- it did not handle what it was given as well as the "
                "step before it produced it."
            ),
            evidence=(),
        )

    return None

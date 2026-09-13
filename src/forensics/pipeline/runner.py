"""Chains the four steps.

Deliberately dumb. It calls four functions in order and returns the result --
no timing, no logging, no error swallowing. All of that belongs to the tracing
layer in tracing.py, which wraps these same calls without changing them.

Keeping the runner this thin is the point: if orchestration and observability
were tangled together here, you could not test either one on its own.
"""

from __future__ import annotations

from ..llm import LLMClient, build_client
from ..models import PipelineResult
from .classification import classify
from .extraction import extract
from .intake import intake
from .summarization import summarize


def run_pipeline(raw_text: str, source_name: str,
                 client: LLMClient | None = None) -> PipelineResult:
    """Run all four steps. Raises StepError from whichever step fails first."""
    # The client is a parameter rather than something built inside, so tests can
    # pass a mock and Phase 2 can pass a recording client, with no code change.
    client = client or build_client()

    document = intake(raw_text, source_name)
    extraction = extract(document, client)
    classification = classify(document, extraction, client)
    summary = summarize(document, extraction, classification, client)

    return PipelineResult(
        document=document,
        extraction=extraction,
        classification=classification,
        summary=summary,
    )

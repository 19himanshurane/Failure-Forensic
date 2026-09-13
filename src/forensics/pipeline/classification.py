"""Step 3: Document + ExtractionResult -> Classification.

Design note worth reading. The obvious prompt asks the model for a document type
and a confidence margin. Models are poor at producing calibrated derived numbers
on demand -- ask for a "margin between 0 and 1" and you get a plausible-looking
number with no arithmetic behind it.

So we ask for the primitives instead: a 0-100 score per category. The ranking
and the margin are then computed in Python, from numbers the model actually
produced. Same information, but the derivation is now deterministic, inspectable
and testable, and the scores themselves land in the trace as evidence.

General rule: ask a model for judgements, not for arithmetic.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from ..errors import StepError
from ..llm import LLMClient, LLMRequest, MalformedResponse, extract_json
from ..models import Classification, Document, DocumentType, ExtractionResult

logger = logging.getLogger(__name__)

STEP_VERSION = "classification/v1"

CATEGORIES = ("contract", "invoice", "report", "correspondence")

# Below this, the best category is not convincing enough to claim at all.
UNKNOWN_THRESHOLD = 20

SYSTEM = f"""You classify business documents into exactly one of four types.

  contract        an agreement between parties, with obligations and terms
  invoice         a request for payment, with amounts and payment terms
  report          an analysis or set of findings
  correspondence  a letter, email or message between people

Return a single JSON object with exactly these keys:
  scores      an object with a 0-100 score for EACH of: {", ".join(CATEGORIES)}
  confidence  integer 1-5, how sure you are overall
  reasoning   one sentence naming the evidence that drove the top score

Score each category independently on how well the document fits it. The scores
do not need to add up to 100. If a document genuinely sits between two types,
give both high scores -- do not artificially separate them. That ambiguity is
useful information, not a mistake.

Return only the JSON object."""


def build_user_prompt(document: Document, extraction: ExtractionResult) -> str:
    facts = []
    if extraction.organizations:
        facts.append("Organisations: " + ", ".join(e.value for e in extraction.organizations))
    if extraction.people:
        facts.append("People: " + ", ".join(e.value for e in extraction.people))
    if extraction.amounts:
        facts.append("Amounts: " + ", ".join(f"{a.amount} {a.currency}" for a in extraction.amounts))
    if extraction.dates:
        facts.append("Dates: " + ", ".join(e.value for e in extraction.dates))

    context = "\n".join(facts) if facts else "(no entities were extracted)"
    return (
        f"Facts already extracted from this document:\n{context}\n\n"
        f"Document:\n---\n{document.raw_text}\n---"
    )


def _coerce_scores(raw: object) -> dict[str, float]:
    scores = {c: 0.0 for c in CATEGORIES}
    if not isinstance(raw, dict):
        return scores
    for category in CATEGORIES:
        try:
            scores[category] = max(0.0, min(100.0, float(raw.get(category, 0))))
        except (TypeError, ValueError):
            logger.warning("classification: bad score for %s: %r", category, raw.get(category))
    return scores


def classify(document: Document, extraction: ExtractionResult,
             client: LLMClient) -> Classification:
    request = LLMRequest(
        tag="classification",
        system=SYSTEM,
        user=build_user_prompt(document, extraction),
        model=getattr(client, "model", "mock"),
    )
    response = client.complete(request)

    try:
        payload = extract_json(response.text)
    except MalformedResponse as exc:
        raise StepError("classification", str(exc), response.text, request.cache_key()) from exc

    scores = _coerce_scores(payload.get("scores"))
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (top_name, top_score), (second_name, second_score) = ranked[0], ranked[1]

    total = top_score + second_score
    # Normalised gap between first and second place. 1.0 = the runner-up scored
    # nothing; 0.0 = a dead heat, which is the signature of an ambiguous document.
    margin = 0.0 if total == 0 else round((top_score - second_score) / total, 3)

    doc_type = DocumentType(top_name) if top_score >= UNKNOWN_THRESHOLD else DocumentType.UNKNOWN

    try:
        return Classification(
            doc_type=doc_type,
            runner_up=DocumentType(second_name),
            margin=margin,
            confidence=max(1, min(5, int(payload.get("confidence", 3)))),
            reasoning=str(payload.get("reasoning", "")).strip(),
        )
    except (ValidationError, ValueError) as exc:
        raise StepError(
            "classification", f"could not build a classification: {exc}",
            response.text, request.cache_key(),
        ) from exc

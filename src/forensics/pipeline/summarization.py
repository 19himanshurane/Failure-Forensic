"""Step 4: everything above -> Summary.

This step is where "Context Loss" and "Propagation Error" become observable.
It receives the entities from step 2 and the type from step 3, so when the final
summary is missing a fact, Phase 3 can ask a sharp question: was the fact never
extracted (step 2's fault), or extracted and then dropped here (step 4's fault)?
Passing the entities in explicitly is what makes those two cases separable.
"""

from __future__ import annotations

from pydantic import ValidationError

from ..errors import StepError
from ..llm import LLMClient, LLMRequest, MalformedResponse, extract_json
from ..models import Classification, Document, DocumentType, ExtractionResult, Summary
from ..tracing import traced_step

STEP_VERSION = "summarization/v1"

# What a good summary covers, per document type. This is the "tailored to the
# document type" requirement: the shape of the answer depends on step 3's output,
# which is precisely why a misclassification in step 3 damages step 4.
FOCUS = {
    DocumentType.CONTRACT: (
        "the parties, what each is obliged to do, the term and termination "
        "conditions, and any dates or amounts that bind them"
    ),
    DocumentType.INVOICE: (
        "who is billing whom, what for, the total for EACH currency separately, "
        "and the payment due date"
    ),
    DocumentType.REPORT: (
        "the purpose, the main findings with their supporting numbers, and the "
        "conclusion or recommendation"
    ),
    DocumentType.CORRESPONDENCE: (
        "who is writing to whom, why, what they are asking for, and any deadline"
    ),
    DocumentType.UNKNOWN: (
        "what the document appears to be, and its main factual content"
    ),
}

SYSTEM = """You write short structured summaries of business documents.

Return a single JSON object with exactly these keys:
  headline    one line, under 120 characters, stating what this document is
  bullets     3 to 5 strings, each one factual point
  confidence  integer 1-5
  reasoning   one sentence explaining that confidence

RULES:
1. Use only facts present in the document or in the extracted facts given to you.
2. If an important fact was extracted but you cannot fit it in, say so in
   reasoning rather than silently dropping it.
3. Where amounts appear in more than one currency, keep them separate. Never add
   amounts in different currencies together.
4. If the document is missing something the reader would expect, say so
   explicitly in a bullet.

Return only the JSON object."""


def build_user_prompt(document: Document, extraction: ExtractionResult,
                      classification: Classification) -> str:
    focus = FOCUS.get(classification.doc_type, FOCUS[DocumentType.UNKNOWN])

    facts = []
    for label, items in (
        ("People", [e.value for e in extraction.people]),
        ("Organisations", [e.value for e in extraction.organizations]),
        ("Dates", [f"{e.value}" for e in extraction.dates]),
        ("Amounts", [f"{a.amount} {a.currency}" for a in extraction.amounts]),
        ("Key terms", [e.value for e in extraction.key_terms]),
    ):
        facts.append(f"{label}: {', '.join(items) if items else '(none found)'}")

    warning = ""
    if classification.is_ambiguous:
        # Honesty about upstream uncertainty. Telling the model the type was a
        # close call is better than presenting a shaky label as settled fact.
        warning = (
            f"\nNOTE: the type was a close call between {classification.doc_type.value} "
            f"and {classification.runner_up.value if classification.runner_up else 'another type'}. "
            "Hedge if the document does not clearly fit."
        )

    return (
        f"Document type: {classification.doc_type.value}\n"
        f"For this type, a good summary covers {focus}.{warning}\n\n"
        f"Facts extracted earlier:\n" + "\n".join(facts) + "\n\n"
        f"Document:\n---\n{document.raw_text}\n---"
    )


@traced_step("summarization")
def summarize(document: Document, extraction: ExtractionResult,
              classification: Classification, client: LLMClient) -> Summary:
    request = LLMRequest(
        tag="summarization",
        system=SYSTEM,
        user=build_user_prompt(document, extraction, classification),
        model=getattr(client, "model", "mock"),
    )
    response = client.complete(request)

    try:
        payload = extract_json(response.text)
    except MalformedResponse as exc:
        raise StepError("summarization", str(exc), response.text, request.cache_key()) from exc

    bullets = payload.get("bullets") or []
    if not isinstance(bullets, list):
        bullets = [str(bullets)]

    try:
        return Summary(
            headline=str(payload.get("headline", "")).strip()[:200] or "(no headline)",
            bullets=tuple(str(b).strip() for b in bullets if str(b).strip()),
            # Recorded, not re-derived: the summary carries the type it was
            # written for, so a wrong summary can be blamed on a wrong type.
            tailored_for=classification.doc_type,
            confidence=max(1, min(5, int(payload.get("confidence", 3)))),
            reasoning=str(payload.get("reasoning", "")).strip(),
        )
    except (ValidationError, ValueError) as exc:
        raise StepError(
            "summarization", f"could not build a summary: {exc}",
            response.text, request.cache_key(),
        ) from exc

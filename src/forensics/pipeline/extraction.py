"""Step 2: Document -> ExtractedResult.

The prompt here carries the single most important instruction in the project:
every entity must be accompanied by a verbatim quote from the source. That is
what makes hallucination detection a substring check later on.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from ..errors import StepError
from ..llm import LLMClient, LLMRequest, MalformedResponse, extract_json
from ..models import Document, ExtractionResult
from ..money import SYMBOL_TO_CODE, AmbiguousAmount, parse_amount

logger = logging.getLogger(__name__)

STEP_VERSION = "extraction/v1"

SYSTEM = """You extract structured facts from business documents.

You must return a single JSON object with exactly these keys:
  people          list of entities
  organizations   list of entities
  dates           list of date entities
  amounts         list of money entities
  key_terms       list of entities
  confidence      integer 1-5, how sure you are about this extraction overall
  reasoning       one sentence explaining that confidence

Every entity object has:
  value           the extracted fact, normalised
  source_quote    a span copied VERBATIM from the document
  confidence      integer 1-5 for this specific entity

A date entity also has:
  iso_date        "YYYY-MM-DD", or null if the text does not resolve to a real
                  calendar date (e.g. "upon delivery", "net 30")

A money entity also has:
  amount          the numeric value as a string, e.g. "4500.00"
  currency        ISO 4217 code, e.g. "USD"

RULES, in order of importance:
1. source_quote must be copied from the document character for character. Do not
   paraphrase it, fix its spelling, or reformat it.
2. If you cannot produce a verbatim quote for something, leave it out entirely.
3. Never state a fact the document does not contain. An empty list is a correct
   answer when the document has nothing of that kind.
4. Use a low confidence rather than inventing detail you are unsure about.

Return only the JSON object."""


def build_user_prompt(document: Document) -> str:
    return f"Extract facts from the following document.\n\n---\n{document.raw_text}\n---"


# Keys we accept from the model. Anything else is sloppiness, not content, so we
# drop it and log rather than failing the whole step.
_KNOWN = {"people", "organizations", "dates", "amounts", "key_terms", "confidence", "reasoning"}
_LISTS = ("people", "organizations", "dates", "amounts", "key_terms")


def _clamp_confidence(value: object, default: int = 3) -> int:
    try:
        return max(1, min(5, int(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _coerce_entity(raw: object) -> dict | None:
    """Repair the *shape* of an entity. Never repairs its *content*."""
    if not isinstance(raw, dict):
        return None
    out = {
        "value": str(raw.get("value", "")).strip(),
        # A missing quote becomes an empty one, which fails the grounding check.
        # That is deliberate: an unsupported claim should look unsupported.
        "source_quote": str(raw.get("source_quote") or "").strip(),
        "confidence": _clamp_confidence(raw.get("confidence")),
    }
    return out if out["value"] else None


def _coerce_date(raw: object) -> dict | None:
    entity = _coerce_entity(raw)
    if entity is None:
        return None
    iso = raw.get("iso_date") if isinstance(raw, dict) else None
    entity["iso_date"] = iso if isinstance(iso, str) and iso.strip() else None
    return entity


def _coerce_money(raw: object) -> dict | None:
    entity = _coerce_entity(raw)
    if entity is None or not isinstance(raw, dict):
        return None

    try:
        amount, ambiguous = parse_amount(str(raw.get("amount", entity["value"])))
    except AmbiguousAmount:
        logger.warning("extraction: unparseable amount %r, dropping", raw.get("amount"))
        return None

    currency = str(raw.get("currency", "")).strip()
    currency = SYMBOL_TO_CODE.get(currency, currency).upper()
    if len(currency) != 3:
        logger.warning("extraction: bad currency %r, defaulting to USD", raw.get("currency"))
        currency = "USD"

    entity["amount"] = str(amount)
    entity["currency"] = currency
    if ambiguous:
        entity["confidence"] = min(entity["confidence"], 2)
    return entity


def _coerce(payload: dict) -> dict:
    unexpected = set(payload) - _KNOWN
    if unexpected:
        # Cosmetic: the model added commentary fields. Worth knowing about, not
        # worth failing over. Phase 2 will record this on the span.
        logger.warning("extraction: dropping unexpected keys %s", sorted(unexpected))

    coercers = {"dates": _coerce_date, "amounts": _coerce_money}
    out: dict = {}
    for key in _LISTS:
        items = payload.get(key) or []
        if not isinstance(items, list):
            items = []
        coerce = coercers.get(key, _coerce_entity)
        out[key] = tuple(e for e in (coerce(i) for i in items) if e is not None)

    out["confidence"] = _clamp_confidence(payload.get("confidence"))
    out["reasoning"] = str(payload.get("reasoning", "")).strip()
    return out


def extract(document: Document, client: LLMClient) -> ExtractionResult:
    request = LLMRequest(
        tag="extraction",
        system=SYSTEM,
        user=build_user_prompt(document),
        model=getattr(client, "model", "mock"),
    )
    response = client.complete(request)

    try:
        payload = extract_json(response.text)
    except MalformedResponse as exc:
        raise StepError("extraction", str(exc), response.text, request.cache_key()) from exc

    try:
        return ExtractionResult(**_coerce(payload))
    except ValidationError as exc:
        raise StepError(
            "extraction", f"response did not match the schema: {exc}",
            response.text, request.cache_key(),
        ) from exc

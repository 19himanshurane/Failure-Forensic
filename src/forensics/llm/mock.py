"""A deterministic stand-in for a real model.

Two jobs, and it is important to keep them apart:

1. Default mode does crude rule-based work (regex, keywords) so the pipeline is
   runnable and testable with no API key, no cost and no flakiness. Its answers
   are mediocre but *honest*: every entity it returns is genuinely quoted from
   the source, so it never fabricates evidence.

2. Chaos mode deliberately injects specific, named failure modes, to test the
   Phase 3 detectors. You cannot verify a hallucination detector without a
   known hallucination. It is a test fixture, NOT a source of results:
   the Phase 6 demo numbers must come from real model failures in live mode.
"""

from __future__ import annotations

import json
import re

from ..money import find_amounts
from .base import LLMRequest, LLMResponse

ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
LONG_DATE = re.compile(r"\b(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b", re.I)
RELATIVE = re.compile(r"\b(within\s+\d+\s+days?|upon\s+\w+|net\s+\d+)\b", re.I)
ORG = re.compile(r"\b([A-Z][A-Za-z&.\-]*(?:\s+[A-Z][A-Za-z&.\-]*)*\s+(?:Corp|Corporation|Ltd|Limited|Inc|LLC|GmbH|PLC|AG|SA))\b")
PERSON = re.compile(r"\b([A-Z][a-z]{2,}\s+[A-Z][a-z]{2,})\b")


TYPE_KEYWORDS = {
    "contract": ("agreement", "hereby", "party", "parties", "shall", "terminate", "clause", "nda", "confidential"),
    "invoice": ("invoice", "amount due", "bill to", "payment terms", "subtotal", "vat", "tax", "remit"),
    "report": ("summary", "findings", "quarter", "results", "analysis", "conclusion", "revenue", "metrics"),
    "correspondence": ("dear", "regards", "sincerely", "hi ", "hello", "thanks", "best wishes", "following up"),
}


def _document_body(user_prompt: str) -> str:
    """Pull the document out of the surrounding prompt.

    Every step wraps the document in --- fences. A real model reads the document
    out of the prompt and quotes from it; the mock has to do the same, or it
    quotes instruction text and fence markers that are not in the source. Those
    quotes then fail the grounding check and look like hallucinations, a false
    positive that in a debugging tool is worse than no detector at all.
    """
    parts = user_prompt.split("---")
    if len(parts) >= 3:
        return "---".join(parts[1:-1]).strip()
    return user_prompt


def _quote_around(text: str, match: re.Match, pad: int = 25) -> str:
    """Return a verbatim window of the source around a match.

    The source_quote contract from models.py requires that quotes really appear
    in the document, so we slice the actual text rather than reconstructing it.
    """
    start = max(0, match.start() - pad)
    end = min(len(text), match.end() + pad)
    return text[start:end].strip()


class MockLLM:
    """Rule-based, deterministic. Same input always gives the same output."""

    def __init__(self, chaos: set[str] | None = None) -> None:
        self.model = "mock"
        # e.g. {"hallucinate", "misclassify", "drop_context", "bad_json"}
        self.chaos = chaos or set()

    def complete(self, request: LLMRequest) -> LLMResponse:
        if "bad_json" in self.chaos:
            payload = "Sure! Here is the result:\n\nI could not find anything useful."
        else:
            handler = {
                "extraction": self._extract,
                "classification": self._classify,
                "summarization": self._summarize,
            }.get(request.tag, self._unknown)
            payload = json.dumps(handler(_document_body(request.user)), ensure_ascii=False)

        return LLMResponse(
            text=payload,
            model="mock",
            prompt_tokens=len(request.system.split()) + len(request.user.split()),
            completion_tokens=len(payload.split()),
            latency_ms=0.0,
            source="mock",
        )

    # -- per-step handlers -------------------------------------------------

    def _extract(self, text: str) -> dict:
        people, orgs = [], []
        seen: set[str] = set()

        for m in ORG.finditer(text):
            name = m.group(1).strip()
            if name not in seen:
                seen.add(name)
                orgs.append({"value": name, "source_quote": _quote_around(text, m), "confidence": 4})

        for m in PERSON.finditer(text):
            name = m.group(1).strip()
            # Crude: skips names already claimed by an organisation match.
            if name in seen or any(name in o["value"] for o in orgs):
                continue
            seen.add(name)
            people.append({"value": name, "source_quote": _quote_around(text, m), "confidence": 3})

        amounts = []
        for m, value, currency, ambiguous in find_amounts(text):
            amounts.append({
                "value": m.group(0).strip(),
                "source_quote": _quote_around(text, m),
                # An amount whose separators are locale-ambiguous is reported at
                # low confidence rather than silently resolved. Phase 3 treats
                # low-confidence spans as suspects, which is exactly right here.
                "confidence": 2 if ambiguous else 4,
                "amount": str(value),
                "currency": currency,
            })

        dates = []
        for pattern, resolved in ((ISO_DATE, True), (LONG_DATE, False), (RELATIVE, False)):
            for m in pattern.finditer(text):
                dates.append({
                    "value": m.group(1), "source_quote": _quote_around(text, m),
                    "confidence": 4 if resolved else 2,
                    "iso_date": m.group(1) if resolved else None,
                })

        if "hallucinate" in self.chaos:
            # A plausible-looking entity with a quote that is NOT in the source.
            people.append({
                "value": "John Smith",
                "source_quote": "duly signed by John Smith on behalf of the parties",
                "confidence": 5,
            })

        return {
            "people": people, "organizations": orgs, "dates": dates, "amounts": amounts,
            "key_terms": [], "confidence": 3 if (people or orgs or amounts) else 1,
            "reasoning": "rule-based mock extraction",
        }

    def _classify(self, text: str) -> dict:
        low = text.lower()
        hits = {t: sum(low.count(k) for k in kws) for t, kws in TYPE_KEYWORDS.items()}
        peak = max(hits.values()) or 1
        # Normalise keyword hits onto the 0-100 scale the real step expects. The
        # mock must speak exactly the same contract as a live model, or testing
        # against it proves nothing.
        scores = {t: round(100 * v / peak) for t, v in hits.items()}

        if "misclassify" in self.chaos:
            ranked = sorted(scores, key=lambda k: scores[k], reverse=True)
            top, second = ranked[0], ranked[1]
            hi = max(scores[top], 60)
            scores[second], scores[top] = hi, hi - 2   # runner-up wins by a hair

        ordered = sorted(scores.values(), reverse=True)
        clear = ordered[0] - ordered[1] > 40
        return {
            "scores": scores,
            "confidence": 4 if clear else 2,
            "reasoning": f"keyword hits: {hits}",
        }

    def _summarize(self, text: str) -> dict:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        headline = lines[0] if lines else "Untitled document"
        bullets = lines[1:5]
        if "drop_context" in self.chaos:
            bullets = bullets[:1]
        return {
            "headline": headline[:120],
            "bullets": bullets or ["No detail extracted."],
            "confidence": 3,
            "reasoning": "rule-based mock summary: first lines of the document",
        }

    def _unknown(self, text: str) -> dict:
        return {"confidence": 1, "reasoning": f"mock has no handler for this tag"}

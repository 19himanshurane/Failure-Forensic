"""Step 1: raw text -> Document.

The only step with no LLM in it. Its job is to make every later step's life
predictable: one canonical text encoding, a stable id, and an early, loud
rejection of input that cannot be processed at all.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from ..errors import StepError
from ..models import Document
from ..tracing import traced_step

# Bumped whenever the behaviour of this step changes. It travels into the trace
# so you can tell "this failed on intake v1" apart from "this failed on v2".
STEP_VERSION = "intake/v1"

MIN_CHARS = 20


@traced_step("intake")
def intake(raw_text: str, source_name: str) -> Document:
    if raw_text is None or not raw_text.strip():
        raise StepError("intake", f"{source_name}: document is empty")

    # Canonicalise before anything else sees the text. Grounding checks compare
    # model quotes against Document.raw_text, so the text stored here must be
    # exactly the text the model was shown; normalising later would break that.
    text = raw_text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()

    if len(text) < MIN_CHARS:
        raise StepError("intake", f"{source_name}: only {len(text)} chars, too short to process")

    # Content-addressed id: the same document always gets the same id, across
    # runs and machines. That is what lets Phase 5 say "this is the same failing
    # case as last week" without maintaining a separate registry.
    doc_id = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]

    return Document(
        doc_id=doc_id,
        source_name=source_name,
        raw_text=text,
        ingested_at=datetime.now(timezone.utc),
    )

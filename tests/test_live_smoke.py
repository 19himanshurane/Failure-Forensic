"""A real call to the live provider, exercising the full pipeline end to end.

Skipped by default: it needs network access and a real key, and it costs
whatever the provider charges. Not part of the ordinary test run -- put your
key in .env and run it explicitly:

    pytest tests/test_live_smoke.py -v
"""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv

load_dotenv()

from forensics.llm import build_live_client
from forensics.models import DocumentType
from forensics.pipeline import run_traced_pipeline

INVOICE = """INVOICE 2201
From: Acme Corp
Bill to: Globex Ltd

Consulting services, Q1 2026.
Amount due: $4,500.00
Payment terms: net 30. Due 2026-04-01.

Regards,
Priya Sharma
"""

_KEY_VAR = {"groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
_PROVIDER = os.getenv("FF_LLM_PROVIDER", "groq")

# Opt-in on purpose, and independent of FF_LLM_MODE: a .env configured for
# live use (for scripts/check_provider.py, say) must not make `pytest` with no
# arguments start spending money and needing network on every run.
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_SMOKE") != "1",
    reason="set RUN_LIVE_SMOKE=1 to run this against a real provider (costs money, needs network)",
)


def test_live_pipeline_produces_a_well_formed_result():
    """Not a check on a real model's wording, which is not deterministic -- this
    only proves the contract holds: a live response actually parses into the
    same typed models every other test relies on, all the way through the
    four steps, instead of the mock's rule-based stand-in."""
    key_var = _KEY_VAR.get(_PROVIDER, "")
    if not os.getenv(key_var):
        pytest.skip(f"{key_var} not set")

    client = build_live_client(_PROVIDER)
    trace = run_traced_pipeline(INVOICE, "live-smoke.txt", client=client)

    assert trace.ok, (trace.spans[-1].error_message, trace.spans[-1].raw_response)
    result = trace.result

    assert result.classification.doc_type is DocumentType.INVOICE
    assert result.summary.headline
    assert result.extraction.all_entities()

    # every LLM-backed span actually reached the network, not a cassette or the mock
    llm_spans = [s for s in trace.spans if s.step != "intake"]
    assert llm_spans and all(s.source == "live" for s in llm_spans)
    assert all(s.latency_ms > 0 for s in llm_spans)

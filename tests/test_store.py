"""Phase 2: a trace saved to disk must come back exactly as it went in --
including which concrete type each span's polymorphic `output` field holds."""

from forensics.llm import MockLLM
from forensics.models import Classification, Document, ExtractionResult, Summary
from forensics.pipeline.tracing import run_traced_pipeline
from forensics.store import TraceStore

INVOICE = """INVOICE 2201
From: Acme Corp
Bill to: Globex Ltd

Consulting services, Q1 2026.
Amount due: $4,500.00
Payment terms: net 30. Due 2026-04-01.

Regards,
Priya Sharma
"""


def test_a_saved_trace_round_trips_through_json(tmp_path):
    trace = run_traced_pipeline(INVOICE, "i.txt", client=MockLLM())
    store = TraceStore(tmp_path / "traces")

    store.save(trace)
    reloaded = store.load(trace.trace_id)

    assert reloaded == trace


def test_each_spans_output_deserializes_to_its_own_concrete_type(tmp_path):
    """The Union type on Span.output must not collapse into the wrong model
    when Pydantic has to guess from a bare dict instead of a live object."""
    trace = run_traced_pipeline(INVOICE, "i.txt", client=MockLLM())
    store = TraceStore(tmp_path / "traces")
    store.save(trace)
    reloaded = store.load(trace.trace_id)

    by_step = {s.step: s.output for s in reloaded.spans}
    assert isinstance(by_step["intake"], Document)
    assert isinstance(by_step["extraction"], ExtractionResult)
    assert isinstance(by_step["classification"], Classification)
    assert isinstance(by_step["summarization"], Summary)


def test_a_failed_trace_also_round_trips(tmp_path):
    trace = run_traced_pipeline(INVOICE, "i.txt", client=MockLLM(chaos={"bad_json"}))
    store = TraceStore(tmp_path / "traces")
    store.save(trace)

    assert store.load(trace.trace_id) == trace


def test_sqlite_index_is_queryable_by_doc_id():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        store = TraceStore(d)
        a = run_traced_pipeline(INVOICE, "run-1.txt", client=MockLLM())
        b = run_traced_pipeline(INVOICE, "run-2.txt", client=MockLLM())
        store.save(a)
        store.save(b)

        rows = store.history_for(a.doc_id)
        assert [r["trace_id"] for r in rows] == [a.trace_id, b.trace_id]
        assert rows[0]["status"] == a.status.value
        store.close()

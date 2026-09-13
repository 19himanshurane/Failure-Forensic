"""OpenTelemetry spans should mirror what actually happened during a traced
run: one span per step nested under a parent pipeline span, attributed with
the same facts the custom Span model carries, and marked as an error exactly
when the step raised, verified independently of forensics' own models."""

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from forensics.llm import MockLLM
from forensics.pipeline.tracing import run_traced_pipeline

INVOICE = """INVOICE 2201
From: Acme Corp
Bill to: Globex Ltd

Consulting services, Q1 2026.
Amount due: $4,500.00
Payment terms: net 30. Due 2026-04-01.

Regards,
Priya Sharma
"""

# OpenTelemetry's global TracerProvider can only be set once per process, so
# it's configured here, at module scope, rather than per test.
_exporter = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(_exporter))
trace.set_tracer_provider(_provider)


def test_a_traced_run_emits_one_otel_span_per_step_plus_a_parent():
    _exporter.clear()
    run_traced_pipeline(INVOICE, "i.txt", client=MockLLM())

    names = sorted(s.name for s in _exporter.get_finished_spans())
    assert names == sorted([
        "forensics.pipeline.run", "forensics.step.intake", "forensics.step.extraction",
        "forensics.step.classification", "forensics.step.summarization",
    ])


def test_step_spans_nest_under_the_pipeline_span():
    _exporter.clear()
    run_traced_pipeline(INVOICE, "i.txt", client=MockLLM())

    spans = {s.name: s for s in _exporter.get_finished_spans()}
    pipeline_span = spans["forensics.pipeline.run"]
    assert pipeline_span.parent is None
    for step in ("intake", "extraction", "classification", "summarization"):
        child = spans[f"forensics.step.{step}"]
        assert child.parent.span_id == pipeline_span.context.span_id


def test_span_attributes_carry_step_metadata():
    _exporter.clear()
    run_traced_pipeline(INVOICE, "i.txt", client=MockLLM())

    spans = {s.name: s for s in _exporter.get_finished_spans()}
    pipeline_span = spans["forensics.pipeline.run"]
    assert pipeline_span.attributes["forensics.source_name"] == "i.txt"

    extraction_span = spans["forensics.step.extraction"]
    assert extraction_span.attributes["forensics.step"] == "extraction"
    assert extraction_span.attributes["forensics.model"] == "mock"
    assert "forensics.cache_key" in extraction_span.attributes
    assert extraction_span.status.status_code.name == "UNSET"  # healthy: no explicit status


def test_a_failing_step_marks_its_otel_span_as_an_error():
    _exporter.clear()
    run_traced_pipeline(INVOICE, "i.txt", client=MockLLM(chaos={"bad_json"}))

    spans = {s.name: s for s in _exporter.get_finished_spans()}
    extraction_span = spans["forensics.step.extraction"]
    assert extraction_span.status.status_code.name == "ERROR"
    assert extraction_span.events  # record_exception() added an event

    # A step that's never reached gets no span at all: it never ran.
    assert "forensics.step.classification" not in spans

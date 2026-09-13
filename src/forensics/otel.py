"""OpenTelemetry spans alongside the project's own Span/Trace models.

The custom models (models.Span/Trace) stay the source of truth: Diagnosis,
TraceStore and the eval loop all read them and don't need OpenTelemetry to
exist. This module is the other half of "OpenTelemetry + custom spans" --
every traced step also opens a real OTel span, so the same pipeline run shows
up in Jaeger/Honeycomb/whatever an org already runs, without either
representation depending on the other.

Configuration is opt-in and safe by default. With FF_OTEL_EXPORTER unset,
OpenTelemetry's own API hands back a no-op tracer -- every call below compiles
away to nothing, at effectively zero cost. Set it to "console" to print spans
to stdout locally, or "otlp" to ship them to a real collector (address from
the standard OTEL_EXPORTER_OTLP_ENDPOINT env var).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry import trace
from opentelemetry.trace import Span as OtelSpan
from opentelemetry.trace import Status, StatusCode

_configured = False


def _configure() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    mode = os.getenv("FF_OTEL_EXPORTER", "").lower()
    if not mode or mode == "none":
        return  # leave the default no-op provider in place

    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": "failure-forensics"}))

    if mode == "console":
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    elif mode == "otlp":
        # Optional dependency (pip install failure-forensics[otlp]): pulled in
        # lazily so nobody pays for grpc/protobuf just to import this module.
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    else:
        raise ValueError(f"unknown FF_OTEL_EXPORTER {mode!r}; expected console, otlp or none")

    trace.set_tracer_provider(provider)


def get_tracer() -> trace.Tracer:
    _configure()
    return trace.get_tracer("forensics")


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[OtelSpan]:
    """Open a span named `name`. Nests under whatever span is already active,
    so wrapping a whole pipeline run's spans in one outer span here gives a
    proper trace tree without any extra bookkeeping."""
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as otel_span:
        for key, value in attributes.items():
            if value is not None:
                otel_span.set_attribute(key, value)
        yield otel_span


def mark_error(otel_span: OtelSpan, message: str, exc: BaseException | None = None) -> None:
    otel_span.set_status(Status(StatusCode.ERROR, message))
    if exc is not None:
        otel_span.record_exception(exc)

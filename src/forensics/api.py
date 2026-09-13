"""The feedback loop as an HTTP service.

Everything here is a thin wrapper over functions that already exist and are
already tested (run_traced_pipeline, diagnose, flag_case, failure_analytics).
This module's only job is the wrapping: turn a Trace into a response, turn a
POST body into the arguments those functions want, and return the right HTTP
status when a trace_id doesn't exist or a flag request doesn't make sense.

create_app() takes explicit paths rather than reading FF_TRACE_DIR/FF_EVAL_FILE
directly, so tests can point a fresh instance at a tmp_path without mutating
process-wide environment variables.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .analysis import diagnose
from .eval import EvalDataset, failure_analytics, flag_case
from .llm import MockLLM, build_client
from .models import Diagnosis, EvalCase, FailureCategory, Trace
from .pipeline.tracing import run_traced_pipeline
from .store import TraceStore


class RunRequest(BaseModel):
    raw_text: str
    source_name: str
    mode: str | None = None  # mock | replay | live; None uses FF_LLM_MODE
    # Comma-separated chaos flags for mock mode only (hallucinate,
    # misclassify, drop_context, bad_json), so a UI can demonstrate each
    # detector on demand instead of only via the FF_MOCK_CHAOS env var.
    chaos: str | None = None


class FlagRequest(BaseModel):
    category: FailureCategory | None = None
    corrected_output: dict | None = None


def _get_trace(store: TraceStore, trace_id: str) -> Trace:
    try:
        return store.load(trace_id)
    except FileNotFoundError:
        raise HTTPException(404, f"no trace {trace_id!r}") from None


def create_app(trace_dir: str | None = None, eval_path: str | None = None) -> FastAPI:
    app = FastAPI(
        title="Failure Forensics API",
        description="Run the pipeline, inspect traces, and grow the eval set from confirmed failures.",
        version="0.1.0",
    )

    origins = os.getenv("FF_API_CORS_ORIGINS", "http://localhost:5173,http://localhost:3000")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    store = TraceStore(trace_dir or os.getenv("FF_TRACE_DIR", "traces"))
    eval_dataset = EvalDataset(eval_path or os.getenv("FF_EVAL_FILE", "eval_cases.jsonl"))
    app.state.store = store
    app.state.eval_dataset = eval_dataset

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/runs", response_model=Trace)
    def create_run(req: RunRequest) -> Trace:
        if (req.mode or "mock") == "mock" and req.chaos is not None:
            # Explicit per-request chaos overrides FF_MOCK_CHAOS entirely,
            # including to force *no* chaos via an empty string, so a UI
            # toggle behaves predictably regardless of server-side env config.
            chaos = {c.strip() for c in req.chaos.split(",") if c.strip()}
            client = MockLLM(chaos=chaos)
        else:
            client = build_client(req.mode)
        trace = run_traced_pipeline(req.raw_text, req.source_name, client=client)
        store.save(trace)
        return trace

    @app.get("/runs")
    def list_runs() -> list[dict]:
        return [dict(row) for row in store.all_traces()]

    @app.get("/runs/{trace_id}", response_model=Trace)
    def get_run(trace_id: str) -> Trace:
        return _get_trace(store, trace_id)

    @app.get("/runs/{trace_id}/diagnosis", response_model=Diagnosis | None)
    def get_diagnosis(trace_id: str) -> Diagnosis | None:
        return diagnose(_get_trace(store, trace_id))

    @app.post("/runs/{trace_id}/flag", response_model=EvalCase)
    def flag_run(trace_id: str, req: FlagRequest) -> EvalCase:
        trace = _get_trace(store, trace_id)
        raw_text = trace.result.document.raw_text if trace.result else ""
        try:
            case = flag_case(
                trace, raw_text,
                corrected_output=req.corrected_output, category=req.category,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        eval_dataset.add(case)
        return case

    @app.get("/eval-cases", response_model=list[EvalCase])
    def list_eval_cases() -> list[EvalCase]:
        return eval_dataset.all_cases()

    @app.get("/analytics")
    def analytics() -> dict:
        return failure_analytics(store)

    return app


app = create_app()

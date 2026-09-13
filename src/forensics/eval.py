"""Phase 5: the feedback-to-eval loop.

Three pieces, matching the spec exactly:

  flag_case()         a human flags a bad output and confirms the root cause
                       -> a permanent EvalCase, the moment a one-off failure
                       becomes a regression test.
  check_regression()  replay a case's original input through the *current*
                       pipeline and ask: still failing the same way, fixed, or
                       failing differently now?
  failure_analytics()  the numbers a product team would actually look at:
                       which category and which step account for most
                       failures, how the failure rate is trending, and how
                       long the system takes to reach a diagnosis.

There is no Phase 4 UI yet to click "bad output" from, so flag_case() is that
button, callable directly against any Trace a caller already has.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .analysis import diagnose
from .llm import LLMClient
from .models import EvalCase, FailureCategory, RegressionOutcome, RegressionResult, Trace
from .pipeline.tracing import run_traced_pipeline
from .store import TraceStore


def flag_case(trace: Trace, raw_text: str, corrected_output: dict[str, Any] | None = None,
              category: FailureCategory | None = None) -> EvalCase:
    """Turn a diagnosed trace into a permanent eval case.

    `raw_text` is the exact input that produced `trace`. Trace itself doesn't
    keep it (only the resulting Document, which is normalised text, not
    necessarily byte-identical to what intake was given), so the caller, who
    still has it, must supply it. `category` lets a human override the
    automatic diagnosis; omit it to accept diagnose()'s finding as-is.
    """
    diagnosis = diagnose(trace)
    if diagnosis is None and category is None:
        raise ValueError("cannot flag a healthy (SUCCESS) trace with no override category")

    step = diagnosis.step if diagnosis else "unknown"
    explanation = diagnosis.explanation if diagnosis else "human-flagged; no automatic diagnosis"
    resolved_category = category or diagnosis.category

    bad_span = next((s for s in reversed(trace.spans) if s.step == step), None)
    bad_output = (
        bad_span.output.model_dump(mode="json")
        if bad_span and bad_span.output is not None else None
    )

    return EvalCase(
        source_trace_id=trace.trace_id,
        raw_text=raw_text,
        source_name=trace.source_name,
        doc_id=trace.doc_id,
        category=resolved_category,
        failing_step=step,
        explanation=explanation,
        bad_output=bad_output,
        corrected_output=corrected_output,
    )


class EvalDataset:
    """An append-only JSONL file of confirmed failures.

    JSONL rather than one JSON array: a crash mid-write loses at most the
    partial last line, never the cases already appended, and growing the
    dataset never requires reading the whole thing back in to rewrite it.
    """

    def __init__(self, path: str | Path = "eval_cases.jsonl") -> None:
        self.path = Path(path)

    def add(self, case: EvalCase) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(case.model_dump_json() + "\n")

    def all_cases(self) -> list[EvalCase]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [EvalCase.model_validate_json(line) for line in lines if line.strip()]


def check_regression(case: EvalCase, client: LLMClient | None = None) -> RegressionResult:
    """Replay a case's original input through the current pipeline.

    The client is whatever the caller wants "current" to mean: the mock for a
    fast dev-loop check, a ReplayClient for a byte-exact re-check of a
    specific known model response, or a live client for a real regression
    sweep. None of those are this function's business; it only compares
    yesterday's diagnosis to today's.
    """
    new_trace = run_traced_pipeline(case.raw_text, case.source_name, client=client)
    new_diagnosis = diagnose(new_trace)

    if new_diagnosis is None:
        outcome = RegressionOutcome.FIXED
    elif new_diagnosis.category == case.category and new_diagnosis.step == case.failing_step:
        outcome = RegressionOutcome.STILL_FAILING
    else:
        outcome = RegressionOutcome.CHANGED

    return RegressionResult(
        case_id=case.case_id,
        outcome=outcome,
        new_trace_id=new_trace.trace_id,
        new_category=new_diagnosis.category if new_diagnosis else None,
    )


def run_regression(cases: list[EvalCase], client: LLMClient | None = None) -> list[RegressionResult]:
    """Re-run every accumulated case. What "periodically re-run the eval
    dataset" comes down to: one call to this, on whatever schedule you like."""
    return [check_regression(case, client=client) for case in cases]


def failure_analytics(store: TraceStore) -> dict[str, Any]:
    """The numbers that tell a product team where to invest: which failure
    category and which step show up most, how the failure rate is trending
    day over day, and how quickly the system reaches a diagnosis.

    "Time to root cause" is computed honestly for what this system actually
    is: the sum of a trace's own span latencies, i.e. wall-clock time from
    intake to a ready diagnosis. That number being small (milliseconds,
    against the hours a manual investigation takes) is the entire pitch --
    fabricating a human-review-time metric here would undercut it.
    """
    rows = store.all_traces()
    total = len(rows)
    failing_rows = [r for r in rows if r["category"] is not None]

    by_category = Counter(r["category"] for r in failing_rows)
    by_step = Counter(r["root_step"] for r in failing_rows)

    by_day: dict[str, list[int]] = {}
    for r in rows:
        day = r["created_at"][:10]
        bucket = by_day.setdefault(day, [0, 0])
        bucket[0] += 1
        if r["category"] is not None:
            bucket[1] += 1
    failure_rate_by_day = {
        day: (bad / seen if seen else 0.0) for day, (seen, bad) in sorted(by_day.items())
    }

    diagnosis_latencies_ms = [
        sum(s.latency_ms for s in store.load(r["trace_id"]).spans)
        for r in failing_rows
    ]
    avg_time_to_diagnosis_ms = (
        sum(diagnosis_latencies_ms) / len(diagnosis_latencies_ms)
        if diagnosis_latencies_ms else None
    )

    return {
        "total_traces": total,
        "failing_traces": len(failing_rows),
        "by_category": dict(by_category),
        "by_step": dict(by_step),
        "failure_rate_by_day": failure_rate_by_day,
        "avg_time_to_diagnosis_ms": avg_time_to_diagnosis_ms,
    }

"""Phase 5: flagging a failure should freeze it into something replayable,
and replaying it should correctly notice when the pipeline's behaviour
hasn't changed, has fixed it, or has broken it a different way."""

import pytest

from forensics.eval import (
    EvalDataset, check_regression, failure_analytics, flag_case, run_regression,
)
from forensics.llm import MockLLM
from forensics.models import FailureCategory, RegressionOutcome, TraceStatus
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

CLEAN_DOC = (
    "Hello team,\n"
    "Please note that Acme Corp will meet Globex Ltd on 2026-04-01.\n"
    "Thanks,\n"
    "Priya Sharma\n"
)


def test_flagging_a_healthy_trace_without_an_override_is_rejected():
    trace = run_traced_pipeline(CLEAN_DOC, "i.txt", client=MockLLM())
    with pytest.raises(ValueError):
        flag_case(trace, CLEAN_DOC)


def test_flag_case_captures_the_diagnosis_and_the_bad_output():
    trace = run_traced_pipeline(CLEAN_DOC, "i.txt", client=MockLLM(chaos={"hallucinate"}))
    case = flag_case(trace, CLEAN_DOC)

    assert case.category is FailureCategory.EXTRACTION_HALLUCINATION
    assert case.failing_step == "extraction"
    assert case.source_trace_id == trace.trace_id
    assert case.raw_text == CLEAN_DOC
    assert any(p["value"] == "John Smith" for p in case.bad_output["people"])


def test_eval_dataset_round_trips_through_jsonl(tmp_path):
    trace = run_traced_pipeline(CLEAN_DOC, "i.txt", client=MockLLM(chaos={"misclassify"}))
    case = flag_case(trace, CLEAN_DOC)

    dataset = EvalDataset(tmp_path / "cases.jsonl")
    dataset.add(case)
    dataset.add(case)  # a second flag from a second run, same shape

    cases = dataset.all_cases()
    assert len(cases) == 2
    assert cases[0] == case


def test_regression_is_fixed_once_the_chaos_is_gone():
    """The eval case is built from a chaotic run; replaying the identical
    input through a clean client is the whole reason a case stores raw_text
    instead of just a doc_id: it must be independently replayable."""
    trace = run_traced_pipeline(CLEAN_DOC, "i.txt", client=MockLLM(chaos={"hallucinate"}))
    case = flag_case(trace, CLEAN_DOC)

    result = check_regression(case, client=MockLLM())  # no chaos this time

    assert result.outcome is RegressionOutcome.FIXED
    assert result.new_category is None


def test_regression_is_still_failing_when_the_same_chaos_recurs():
    trace = run_traced_pipeline(CLEAN_DOC, "i.txt", client=MockLLM(chaos={"hallucinate"}))
    case = flag_case(trace, CLEAN_DOC)

    result = check_regression(case, client=MockLLM(chaos={"hallucinate"}))

    assert result.outcome is RegressionOutcome.STILL_FAILING
    assert result.new_category is FailureCategory.EXTRACTION_HALLUCINATION


def test_regression_is_changed_when_a_different_category_shows_up():
    trace = run_traced_pipeline(CLEAN_DOC, "i.txt", client=MockLLM(chaos={"hallucinate"}))
    case = flag_case(trace, CLEAN_DOC)

    result = check_regression(case, client=MockLLM(chaos={"misclassify"}))

    assert result.outcome is RegressionOutcome.CHANGED
    assert result.new_category is FailureCategory.MISCLASSIFICATION


def test_run_regression_checks_every_case():
    trace = run_traced_pipeline(CLEAN_DOC, "i.txt", client=MockLLM(chaos={"drop_context"}))
    case = flag_case(trace, CLEAN_DOC)

    results = run_regression([case, case], client=MockLLM())
    assert len(results) == 2
    assert all(r.outcome is RegressionOutcome.FIXED for r in results)


def test_failure_analytics_summarizes_a_stores_traces(tmp_path):
    store = TraceStore(tmp_path / "traces")
    store.save(run_traced_pipeline(CLEAN_DOC, "a.txt", client=MockLLM()))  # SUCCESS
    store.save(run_traced_pipeline(CLEAN_DOC, "b.txt", client=MockLLM(chaos={"hallucinate"})))
    store.save(run_traced_pipeline(CLEAN_DOC, "c.txt", client=MockLLM(chaos={"hallucinate"})))
    store.save(run_traced_pipeline(INVOICE, "d.txt", client=MockLLM(chaos={"bad_json"})))

    stats = failure_analytics(store)

    assert stats["total_traces"] == 4
    assert stats["failing_traces"] == 3
    assert stats["by_category"]["extraction_hallucination"] == 2
    assert stats["by_category"]["prompt_failure"] == 1
    assert stats["by_step"]["extraction"] == 3
    assert stats["avg_time_to_diagnosis_ms"] >= 0
    assert sum(stats["failure_rate_by_day"].values()) > 0

"""The REST API is a thin wrapper. These tests check the wrapping (status
codes, request/response shapes, wiring to the store/eval dataset), not the
pipeline logic itself, which is already covered elsewhere."""

import pytest
from fastapi.testclient import TestClient

from forensics.api import create_app

CLEAN_DOC = (
    "Hello team,\n"
    "Please note that Acme Corp will meet Globex Ltd on 2026-04-01.\n"
    "Thanks,\n"
    "Priya Sharma\n"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FF_LLM_MODE", "mock")
    monkeypatch.delenv("FF_MOCK_CHAOS", raising=False)
    app = create_app(
        trace_dir=str(tmp_path / "traces"),
        eval_path=str(tmp_path / "evals.jsonl"),
    )
    return TestClient(app)


def test_health():
    app = create_app()
    with TestClient(app) as c:
        assert c.get("/health").json() == {"status": "ok"}


def test_create_run_saves_and_returns_a_trace(client):
    resp = client.post("/runs", json={"raw_text": CLEAN_DOC, "source_name": "i.txt"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["source_name"] == "i.txt"
    assert [s["step"] for s in body["spans"]] == [
        "intake", "extraction", "classification", "summarization",
    ]


def test_list_and_get_run_round_trip(client):
    created = client.post("/runs", json={"raw_text": CLEAN_DOC, "source_name": "i.txt"}).json()

    listed = client.get("/runs").json()
    assert any(r["trace_id"] == created["trace_id"] for r in listed)

    fetched = client.get(f"/runs/{created['trace_id']}").json()
    assert fetched["trace_id"] == created["trace_id"]


def test_unknown_trace_id_is_404(client):
    resp = client.get("/runs/does-not-exist")
    assert resp.status_code == 404


def test_diagnosis_reflects_injected_chaos(client, monkeypatch):
    monkeypatch.setenv("FF_MOCK_CHAOS", "hallucinate")
    created = client.post("/runs", json={"raw_text": CLEAN_DOC, "source_name": "i.txt"}).json()

    diagnosis = client.get(f"/runs/{created['trace_id']}/diagnosis").json()
    assert diagnosis["category"] == "extraction_hallucination"
    assert diagnosis["step"] == "extraction"


def test_chaos_can_be_requested_per_call_without_the_env_var(client):
    """A UI toggling chaos per-request shouldn't need to touch process env."""
    created = client.post(
        "/runs",
        json={"raw_text": CLEAN_DOC, "source_name": "i.txt", "mode": "mock", "chaos": "misclassify"},
    ).json()

    diagnosis = client.get(f"/runs/{created['trace_id']}/diagnosis").json()
    assert diagnosis["category"] == "misclassification"


def test_diagnosis_is_null_for_a_healthy_run(client):
    created = client.post("/runs", json={"raw_text": CLEAN_DOC, "source_name": "i.txt"}).json()
    assert client.get(f"/runs/{created['trace_id']}/diagnosis").json() is None


def test_flagging_a_healthy_run_without_override_is_rejected(client):
    created = client.post("/runs", json={"raw_text": CLEAN_DOC, "source_name": "i.txt"}).json()
    resp = client.post(f"/runs/{created['trace_id']}/flag", json={})
    assert resp.status_code == 400


def test_flag_then_list_eval_cases(client, monkeypatch):
    monkeypatch.setenv("FF_MOCK_CHAOS", "misclassify")
    created = client.post("/runs", json={"raw_text": CLEAN_DOC, "source_name": "i.txt"}).json()

    flagged = client.post(
        f"/runs/{created['trace_id']}/flag",
        json={"corrected_output": {"doc_type": "correspondence"}},
    ).json()
    assert flagged["category"] == "misclassification"
    assert flagged["source_trace_id"] == created["trace_id"]

    cases = client.get("/eval-cases").json()
    assert any(c["case_id"] == flagged["case_id"] for c in cases)


def test_analytics_counts_runs(client, monkeypatch):
    client.post("/runs", json={"raw_text": CLEAN_DOC, "source_name": "a.txt"})
    monkeypatch.setenv("FF_MOCK_CHAOS", "hallucinate")
    client.post("/runs", json={"raw_text": CLEAN_DOC, "source_name": "b.txt"})

    stats = client.get("/analytics").json()
    assert stats["total_traces"] == 2
    assert stats["by_category"]["extraction_hallucination"] == 1

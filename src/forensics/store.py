"""Phase 2: persistent storage for traces.

Every trace is written twice, for two different readers: a JSON file under
<dir>/<trace_id>.json for a human (or the eventual trace explorer) to open and
read end to end, and a row in a SQLite index for anything that needs to query
across many traces without loading every file -- the failure analytics and
regression tracking in Phase 5, or "has this doc_id run before" for a
repeat-case check.

Deliberately not wired into run_traced_pipeline itself: saving is a decision
the caller makes (and every test that builds a Trace without wanting it to
touch disk relies on that).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .analysis import diagnose
from .models import Trace

_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    trace_id    TEXT PRIMARY KEY,
    doc_id      TEXT,
    source_name TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    status      TEXT NOT NULL,
    final_score INTEGER,
    category    TEXT,
    root_step   TEXT
)
"""


class TraceStore:
    def __init__(self, directory: str | Path = "traces") -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: a web server (FastAPI included) runs sync
        # route handlers in a thread pool, so "the thread that opened this
        # connection" is not a fact any caller can rely on. The lock below is
        # what actually keeps that safe -- sqlite3 connections are not
        # implicitly thread-safe for concurrent use, only single-threaded-at-
        # a-time use from multiple threads.
        self._db = sqlite3.connect(self.dir / "index.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute(_SCHEMA)
            self._db.commit()

    def save(self, trace: Trace) -> Path:
        path = self.dir / f"{trace.trace_id}.json"
        path.write_text(trace.model_dump_json(indent=2), encoding="utf-8")
        # Diagnosed once, here, and cached in the index -- so failure_analytics()
        # can group thousands of traces by category without reloading and
        # re-diagnosing every JSON file on every query.
        diagnosis = diagnose(trace)
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO traces "
                "(trace_id, doc_id, source_name, created_at, status, final_score, category, root_step) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    trace.trace_id, trace.doc_id, trace.source_name,
                    trace.created_at.isoformat(), trace.status.value, trace.final_score,
                    diagnosis.category.value if diagnosis else None,
                    diagnosis.step if diagnosis else None,
                ),
            )
            self._db.commit()
        return path

    def load(self, trace_id: str) -> Trace:
        path = self.dir / f"{trace_id}.json"
        return Trace.model_validate_json(path.read_text(encoding="utf-8"))

    def history_for(self, doc_id: str) -> list[sqlite3.Row]:
        """Every past trace for this document, oldest first -- "is this the
        same failing case as last time" reads from exactly this."""
        with self._lock:
            cur = self._db.execute(
                "SELECT * FROM traces WHERE doc_id = ? ORDER BY created_at", (doc_id,),
            )
            return cur.fetchall()

    def all_traces(self) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute("SELECT * FROM traces ORDER BY created_at").fetchall()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "TraceStore":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

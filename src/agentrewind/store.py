"""SQLite-backed trace store. Zero-config: defaults to ~/.agentrewind/traces.db."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

from .models import Span, Status, Trace

DEFAULT_DB = Path(os.environ.get("AGENTREWIND_DB", "~/.agentrewind/traces.db")).expanduser()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    trace_id   TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at   REAL,
    status     TEXT NOT NULL,
    metadata   TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS spans (
    span_id    TEXT PRIMARY KEY,
    trace_id   TEXT NOT NULL REFERENCES traces(trace_id),
    parent_id  TEXT,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at   REAL,
    status     TEXT NOT NULL,
    error      TEXT,
    input      TEXT,
    output     TEXT,
    attributes TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id);
CREATE TABLE IF NOT EXISTS llm_cache (
    fingerprint TEXT PRIMARY KEY,
    request     TEXT NOT NULL,
    response    TEXT NOT NULL,
    created_at  REAL NOT NULL
);
"""


class TraceStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path).expanduser() if path else DEFAULT_DB
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._conn().executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path))
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    # -- traces ------------------------------------------------------------

    def save_trace(self, trace: Trace) -> None:
        conn = self._conn()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO traces VALUES (?,?,?,?,?,?)",
                (
                    trace.trace_id,
                    trace.name,
                    trace.started_at,
                    trace.ended_at,
                    trace.status.value,
                    json.dumps(trace.metadata, default=str),
                ),
            )
            conn.execute("DELETE FROM spans WHERE trace_id = ?", (trace.trace_id,))
            conn.executemany(
                "INSERT INTO spans VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [s.to_row() for s in trace.spans],
            )

    def get_trace(self, trace_id: str) -> Trace | None:
        conn = self._conn()
        row = conn.execute(
            "SELECT trace_id, name, started_at, ended_at, status, metadata "
            "FROM traces WHERE trace_id = ? OR trace_id LIKE ?",
            (trace_id, trace_id + "%"),
        ).fetchone()
        if row is None:
            return None
        trace = Trace(
            trace_id=row[0],
            name=row[1],
            started_at=row[2],
            ended_at=row[3],
            status=Status(row[4]),
            metadata=json.loads(row[5]),
        )
        span_rows = conn.execute(
            "SELECT * FROM spans WHERE trace_id = ? ORDER BY started_at", (trace.trace_id,)
        ).fetchall()
        trace.spans = [Span.from_row(r) for r in span_rows]
        return trace

    def list_traces(self, limit: int = 50) -> list[Trace]:
        rows = self._conn().execute(
            "SELECT trace_id, name, started_at, ended_at, status, metadata "
            "FROM traces ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            Trace(
                trace_id=r[0],
                name=r[1],
                started_at=r[2],
                ended_at=r[3],
                status=Status(r[4]),
                metadata=json.loads(r[5]),
            )
            for r in rows
        ]

    # -- llm replay cache ----------------------------------------------------

    def cache_put(self, fingerprint: str, request: dict, response: dict) -> None:
        import time

        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?)",
                (
                    fingerprint,
                    json.dumps(request, default=str),
                    json.dumps(response, default=str),
                    time.time(),
                ),
            )

    def cache_get(self, fingerprint: str) -> dict | None:
        row = self._conn().execute(
            "SELECT response FROM llm_cache WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        return json.loads(row[0]) if row else None

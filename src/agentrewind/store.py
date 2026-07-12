"""SQLite-backed trace store. Zero-config: defaults to ~/.agentrewind/traces.db."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

from .models import Span, SpanKind, Status, Trace
from .redaction import RedactionPolicy

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
    def __init__(
        self, path: str | Path | None = None, *, redaction: RedactionPolicy | None = None
    ):
        self.path = Path(path).expanduser() if path else DEFAULT_DB
        self.redaction = redaction
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
                    json.dumps(self._redact(trace.metadata), default=str),
                ),
            )
            conn.execute("DELETE FROM spans WHERE trace_id = ?", (trace.trace_id,))
            conn.executemany(
                "INSERT INTO spans VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [self._redacted_span(s).to_row() for s in trace.spans],
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

    def export_trace(self, trace_id: str) -> dict[str, Any] | None:
        """Return a portable, versioned JSON-safe representation of a trace."""
        trace = self.get_trace(trace_id)
        if trace is None:
            return None
        return {
            "format": "agentrewind.trace.v1",
            "trace": {
                "trace_id": trace.trace_id,
                "name": trace.name,
                "started_at": trace.started_at,
                "ended_at": trace.ended_at,
                "status": trace.status.value,
                "metadata": trace.metadata,
                "spans": [
                    {
                        "span_id": span.span_id,
                        "trace_id": span.trace_id,
                        "parent_id": span.parent_id,
                        "name": span.name,
                        "kind": span.kind.value,
                        "started_at": span.started_at,
                        "ended_at": span.ended_at,
                        "status": span.status.value,
                        "error": span.error,
                        "input": span.input,
                        "output": span.output,
                        "attributes": span.attributes,
                    }
                    for span in trace.spans
                ],
            },
        }

    def import_trace(self, payload: dict[str, Any], *, overwrite: bool = False) -> Trace:
        """Validate and save an artifact produced by :meth:`export_trace`."""
        if payload.get("format") != "agentrewind.trace.v1" or not isinstance(
            payload.get("trace"), dict
        ):
            raise ValueError("not an AgentRewind trace v1 export")
        data = payload["trace"]
        required = {"trace_id", "name", "started_at", "status", "metadata", "spans"}
        if not required <= data.keys() or not isinstance(data["spans"], list):
            raise ValueError("trace export is missing required fields")
        if not overwrite and self.get_trace(data["trace_id"]) is not None:
            raise ValueError(
                f"trace {data['trace_id']} already exists (pass overwrite=True to replace it)"
            )
        try:
            trace = Trace(
                trace_id=data["trace_id"],
                name=data["name"],
                started_at=data["started_at"],
                ended_at=data.get("ended_at"),
                status=Status(data["status"]),
                metadata=data["metadata"],
                spans=[
                    Span(
                        span_id=span["span_id"],
                        trace_id=span["trace_id"],
                        parent_id=span.get("parent_id"),
                        name=span["name"],
                        kind=SpanKind(span["kind"]),
                        started_at=span["started_at"],
                        ended_at=span.get("ended_at"),
                        status=Status(span["status"]),
                        error=span.get("error"),
                        input=span.get("input"),
                        output=span.get("output"),
                        attributes=span.get("attributes", {}),
                    )
                    for span in data["spans"]
                ],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid trace export: {exc}") from exc
        self.save_trace(trace)
        return trace

    # -- llm replay cache ----------------------------------------------------

    def cache_put(self, fingerprint: str, request: dict, response: dict) -> None:
        import time

        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?)",
                (
                    fingerprint,
                    json.dumps(self._redact(request), default=str),
                    json.dumps(self._redact(response), default=str),
                    time.time(),
                ),
            )

    def cache_get(self, fingerprint: str) -> dict | None:
        row = self._conn().execute(
            "SELECT response FROM llm_cache WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def _redact(self, value: Any) -> Any:
        return self.redaction.redact(value) if self.redaction else value

    def _redacted_span(self, span: Span) -> Span:
        if not self.redaction:
            return span
        return replace(
            span,
            input=self._redact(span.input),
            output=self._redact(span.output),
            attributes=self._redact(span.attributes),
            error=self._redact(span.error),
        )

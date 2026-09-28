"""Trace storage: a backend-neutral interface plus the zero-config SQLite default.

SQLite (``~/.agentrewind/traces.db``) needs no setup. Set ``AGENTREWIND_DB_URL`` to a
``postgresql://`` URL, or pass one to :func:`open_store`, to use the optional PostgreSQL
backend in :mod:`agentrewind.postgres` instead. Both backends share one schema and the
behaviour defined here; they differ only in SQL dialect and connection handling.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path
from typing import Any

from .models import Span, SpanKind, Status, Trace
from .redaction import RedactionPolicy

DEFAULT_DB = Path(os.environ.get("AGENTREWIND_DB", "~/.agentrewind/traces.db")).expanduser()
DB_URL_ENV = "AGENTREWIND_DB_URL"
POSTGRES_SCHEMES = ("postgres://", "postgresql://")

# Column lists are spelled out rather than relying on positional INSERT/SELECT *, so the
# spans.seq column added in 0.3.0 does not shift anything.
TRACE_COLUMNS = "trace_id, name, started_at, ended_at, status, metadata"
SPAN_COLUMNS = (
    "span_id, trace_id, parent_id, name, kind, started_at, ended_at, status, error, "
    "input, output, attributes"
)
# Execution order. seq is the span's index in Trace.spans; rows written before 0.3.0 have
# no seq, so they fall back to (started_at, span_id). "seq IS NULL" sorts NULLs last on
# both backends (SQLite and PostgreSQL disagree on the default NULL position).
SPAN_ORDER = "started_at, seq IS NULL, seq, span_id"

_SQLITE_SCHEMA = """
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
    attributes TEXT NOT NULL DEFAULT '{}',
    seq        INTEGER
);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id);
CREATE TABLE IF NOT EXISTS llm_cache (
    fingerprint TEXT PRIMARY KEY,
    request     TEXT NOT NULL,
    response    TEXT NOT NULL,
    created_at  REAL NOT NULL
);
"""


def open_store(
    target: str | Path | None = None, *, redaction: RedactionPolicy | None = None
) -> BaseStore:
    """Open the store named by ``target``: a ``postgresql://`` URL or a SQLite path.

    With no target, ``AGENTREWIND_DB_URL`` is consulted, then the SQLite default.
    """
    if target is None:
        target = os.environ.get(DB_URL_ENV) or None
    if isinstance(target, str) and target.startswith(POSTGRES_SCHEMES):
        from .postgres import PostgresStore

        return PostgresStore(target, redaction=redaction)
    return SQLiteStore(target, redaction=redaction)


class BaseStore(ABC):
    """Everything backend-independent: serialisation, redaction, export/import.

    Subclasses implement the ``_``-prefixed primitives against their database.
    """

    redaction: RedactionPolicy | None

    # -- backend primitives ----------------------------------------------------

    @abstractmethod
    def _write_trace(self, trace_row: tuple, span_rows: list[tuple]) -> None:
        """Atomically replace the trace row and all of its spans."""

    @abstractmethod
    def _find_trace_row(self, trace_id: str) -> tuple | None:
        """Exact id match, else the first id (in id order) with this case-insensitive prefix."""

    @abstractmethod
    def _span_rows(self, trace_id: str) -> list[tuple]:
        """Span rows in SPAN_ORDER."""

    @abstractmethod
    def _trace_rows(self, limit: int) -> list[tuple]:
        """Most recent traces first; ties broken by trace_id."""

    @abstractmethod
    def _cache_write(self, row: tuple) -> None: ...

    @abstractmethod
    def _cache_read(self, fingerprint: str) -> str | None: ...

    @abstractmethod
    def close(self) -> None:
        """Close every connection this store opened."""

    # -- traces ------------------------------------------------------------

    def save_trace(self, trace: Trace) -> None:
        trace_row = (
            trace.trace_id,
            trace.name,
            trace.started_at,
            trace.ended_at,
            trace.status.value,
            json.dumps(self._redact(trace.metadata), default=str),
        )
        span_rows = [
            (*self._redacted_span(s).to_row(), seq) for seq, s in enumerate(trace.spans)
        ]
        self._write_trace(trace_row, span_rows)

    def get_trace(self, trace_id: str) -> Trace | None:
        row = self._find_trace_row(trace_id)
        if row is None:
            return None
        trace = _trace_from_row(row)
        trace.spans = [Span.from_row(r) for r in self._span_rows(trace.trace_id)]
        return trace

    def list_traces(self, limit: int = 50) -> list[Trace]:
        return [_trace_from_row(r) for r in self._trace_rows(limit)]

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
        self._cache_write(
            (
                fingerprint,
                json.dumps(self._redact(request), default=str),
                json.dumps(self._redact(response), default=str),
                time.time(),
            )
        )

    def cache_get(self, fingerprint: str) -> dict | None:
        raw = self._cache_read(fingerprint)
        return json.loads(raw) if raw is not None else None

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


def _trace_from_row(row: tuple) -> Trace:
    return Trace(
        trace_id=row[0],
        name=row[1],
        started_at=row[2],
        ended_at=row[3],
        status=Status(row[4]),
        metadata=json.loads(row[5]),
    )


class SQLiteStore(BaseStore):
    """Zero-config default backend: one SQLite file in WAL mode, one connection per thread.

    ``":memory:"`` is the exception: every connection to it would be a separate, empty
    database, so an in-memory store keeps a single connection that all threads share, and
    serialises access to it with a lock. Its data lives only in that connection, so
    :meth:`close` discards it and the store cannot be used afterwards.
    """

    # Seconds a writer waits on another process's lock before raising "database is locked".
    BUSY_TIMEOUT = 30.0

    def __init__(
        self, path: str | Path | None = None, *, redaction: RedactionPolicy | None = None
    ):
        self.path = Path(path).expanduser() if path else DEFAULT_DB
        self.redaction = redaction
        self._memory = str(self.path) == ":memory:"
        if not self._memory:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._all_conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        self._memory_conn: sqlite3.Connection | None = None
        # File databases need no lock: each thread has its own connection and SQLite
        # arbitrates between them. The shared in-memory connection must be serialised.
        self._use_lock = threading.RLock() if self._memory else nullcontext()
        with self._use() as conn:
            conn.executescript(_SQLITE_SCHEMA)
            self._migrate(conn)

    def __repr__(self) -> str:
        return f"SQLiteStore({str(self.path)!r})"

    @contextmanager
    def _use(self) -> Iterator[sqlite3.Connection]:
        """Yield this thread's connection, holding the lock for an in-memory store."""
        with self._use_lock:
            yield self._conn()

    def _conn(self) -> sqlite3.Connection:
        if self._memory:
            if self._memory_conn is None:
                self._memory_conn = self._open()
            return self._memory_conn
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._local.conn = self._open()
        return conn

    def _open(self) -> sqlite3.Connection:
        # check_same_thread=False lets close() release every thread's connection from the
        # calling thread, and lets threads share the in-memory connection under _use_lock.
        conn = sqlite3.connect(str(self.path), timeout=self.BUSY_TIMEOUT, check_same_thread=False)
        self._enable_wal(conn)
        with self._conns_lock:
            self._all_conns.append(conn)
        return conn

    def _enable_wal(self, conn: sqlite3.Connection) -> None:
        """Switch to WAL, retrying while another process holds the file.

        Converting a rollback-journal file (a brand-new db, or one copied without its WAL)
        needs an exclusive lock, and SQLite reports SQLITE_BUSY for it immediately instead
        of waiting out the busy timeout, so concurrent first opens must retry by hand.
        """
        deadline = time.monotonic() + self.BUSY_TIMEOUT
        delay = 0.005
        while True:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) or time.monotonic() >= deadline:
                    raise
            time.sleep(delay)
            delay = min(delay * 2, 0.1)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Bring a database created by an earlier release up to the current schema.

        Idempotent and safe when several processes open the same old file at once: the
        column check is repeated under an exclusive write lock before altering.
        """

        def has_seq() -> bool:
            return any(col[1] == "seq" for col in conn.execute("PRAGMA table_info(spans)"))

        if has_seq():
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            if not has_seq():
                # 0.2.x rows keep seq NULL and sort by (started_at, span_id).
                conn.execute("ALTER TABLE spans ADD COLUMN seq INTEGER")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        with self._conns_lock:
            conns, self._all_conns = self._all_conns, []
        for conn in conns:
            conn.close()
        self._local = threading.local()
        self._memory_conn = None

    def _write_trace(self, trace_row: tuple, span_rows: list[tuple]) -> None:
        with self._use() as conn, conn:
            conn.execute(
                f"INSERT OR REPLACE INTO traces ({TRACE_COLUMNS}) VALUES (?,?,?,?,?,?)",
                trace_row,
            )
            conn.execute("DELETE FROM spans WHERE trace_id = ?", (trace_row[0],))
            conn.executemany(
                f"INSERT INTO spans ({SPAN_COLUMNS}, seq) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                span_rows,
            )

    def _find_trace_row(self, trace_id: str) -> tuple | None:
        with self._use() as conn:
            row = conn.execute(
                f"SELECT {TRACE_COLUMNS} FROM traces WHERE trace_id = ?", (trace_id,)
            ).fetchone()
            if row is not None:
                return row
            return conn.execute(
                f"SELECT {TRACE_COLUMNS} FROM traces "
                "WHERE lower(substr(trace_id, 1, length(?))) = lower(?) "
                "ORDER BY trace_id LIMIT 1",
                (trace_id, trace_id),
            ).fetchone()

    def _span_rows(self, trace_id: str) -> list[tuple]:
        with self._use() as conn:
            return conn.execute(
                f"SELECT {SPAN_COLUMNS} FROM spans WHERE trace_id = ? ORDER BY {SPAN_ORDER}",
                (trace_id,),
            ).fetchall()

    def _trace_rows(self, limit: int) -> list[tuple]:
        with self._use() as conn:
            return conn.execute(
                f"SELECT {TRACE_COLUMNS} FROM traces "
                "ORDER BY started_at DESC, trace_id LIMIT ?",
                (limit,),
            ).fetchall()

    def _cache_write(self, row: tuple) -> None:
        with self._use() as conn, conn:
            conn.execute("INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?)", row)

    def _cache_read(self, fingerprint: str) -> str | None:
        with self._use() as conn:
            row = conn.execute(
                "SELECT response FROM llm_cache WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
        return row[0] if row else None


# Backwards-compatible name: TraceStore(path) has always meant the SQLite store.
TraceStore = SQLiteStore

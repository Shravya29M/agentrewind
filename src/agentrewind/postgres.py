"""Optional PostgreSQL backend. Install with: pip install 'llm-run-recorder[postgres]'.

Selected by passing a ``postgresql://`` URL to :func:`agentrewind.store.open_store` or by
setting ``AGENTREWIND_DB_URL``. Tables live in the connection's ``search_path`` (usually
``public``); append ``?options=-csearch_path%3Dmyschema`` to the URL to use another schema.

Concurrency model, for many writer processes sharing one database:

- One connection per (process, thread). A connection inherited across ``fork()`` is never
  reused; the child opens its own.
- ``save_trace`` upserts the trace row first. That row lock serialises writers of the
  *same* trace (so the delete-then-insert of its spans cannot interleave), while writers of
  different traces proceed in parallel.
- Schema creation is guarded by a transaction-scoped advisory lock, because concurrent
  ``CREATE TABLE IF NOT EXISTS`` can still fail on PostgreSQL's catalog constraints.
"""

from __future__ import annotations

import os
import re
import threading
from typing import Any

from .redaction import RedactionPolicy
from .store import SPAN_COLUMNS, TRACE_COLUMNS, BaseStore

try:
    import psycopg
except ImportError:  # pragma: no cover - exercised via the stub in tests
    psycopg = None  # type: ignore[assignment]

INSTALL_HINT = "PostgreSQL support requires psycopg 3: pip install 'llm-run-recorder[postgres]'"

# Arbitrary constant key for pg_advisory_xact_lock around DDL ("agentrwd" in hex-ish).
_SCHEMA_LOCK_KEY = 0x6167656E74727764

# Same tables and columns as SQLite. REAL becomes DOUBLE PRECISION so timestamps round-trip
# bit-for-bit; JSON stays TEXT (not JSONB) so payloads come back byte-identical, with key
# order preserved for export and diff. spans.trace_id carries no enforced foreign key,
# matching SQLite, where foreign-key enforcement is off by default.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    trace_id   TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    started_at DOUBLE PRECISION NOT NULL,
    ended_at   DOUBLE PRECISION,
    status     TEXT NOT NULL,
    metadata   TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS spans (
    span_id    TEXT PRIMARY KEY,
    trace_id   TEXT NOT NULL,
    parent_id  TEXT,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    started_at DOUBLE PRECISION NOT NULL,
    ended_at   DOUBLE PRECISION,
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
    created_at  DOUBLE PRECISION NOT NULL
);
"""

# store.SPAN_ORDER with byte-wise ("C") collation on the text tiebreakers, so ties sort
# exactly as SQLite's default BINARY collation does, whatever the database locale.
_SPAN_ORDER = 'started_at, seq IS NULL, seq, span_id COLLATE "C"'
_TRACE_ORDER = 'started_at DESC, trace_id COLLATE "C"'

_URL_PASSWORD = re.compile(r"(://[^:/@]*:)[^@]*@")
_KW_PASSWORD = re.compile(r"(password\s*=\s*)(\S+)", re.IGNORECASE)


def redact_dsn(dsn: str) -> str:
    """Hide the password in a connection URL or key=value conninfo string."""
    return _KW_PASSWORD.sub(r"\1***", _URL_PASSWORD.sub(r"\1***@", dsn))


class PostgresStore(BaseStore):
    def __init__(self, dsn: str, *, redaction: RedactionPolicy | None = None):
        if psycopg is None:
            raise ImportError(INSTALL_HINT)
        self.dsn = dsn
        self.redaction = redaction
        self._local = threading.local()
        self._all_conns: list[Any] = []
        self._conns_lock = threading.Lock()
        conn = self._conn()
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK_KEY,))
            conn.execute(_SCHEMA)

    def __repr__(self) -> str:
        return f"PostgresStore({redact_dsn(self.dsn)!r})"

    def _conn(self) -> Any:
        conn = getattr(self._local, "conn", None)
        pid = os.getpid()
        if conn is None or getattr(self._local, "pid", None) != pid:
            try:
                conn = psycopg.connect(self.dsn, autocommit=True)
            except psycopg.OperationalError as exc:
                # libpq messages can echo connection parameters; never surface the password.
                raise psycopg.OperationalError(
                    f"could not connect to {redact_dsn(self.dsn)}: {redact_dsn(str(exc))}"
                ) from None
            self._local.conn, self._local.pid = conn, pid
            with self._conns_lock:
                self._all_conns.append((pid, conn))
        return conn

    def close(self) -> None:
        with self._conns_lock:
            conns, self._all_conns = self._all_conns, []
        pid = os.getpid()
        for owner, conn in conns:
            # Closing a connection inherited from a parent would tear down its session.
            if owner == pid:
                conn.close()
        self._local = threading.local()

    def _write_trace(self, trace_row: tuple, span_rows: list[tuple]) -> None:
        conn = self._conn()
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO traces ({TRACE_COLUMNS}) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (trace_id) DO UPDATE SET name = EXCLUDED.name, "
                "started_at = EXCLUDED.started_at, ended_at = EXCLUDED.ended_at, "
                "status = EXCLUDED.status, metadata = EXCLUDED.metadata",
                trace_row,
            )
            cur.execute("DELETE FROM spans WHERE trace_id = %s", (trace_row[0],))
            cur.executemany(
                f"INSERT INTO spans ({SPAN_COLUMNS}, seq) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                span_rows,
            )

    def _find_trace_row(self, trace_id: str) -> tuple | None:
        conn = self._conn()
        row = conn.execute(
            f"SELECT {TRACE_COLUMNS} FROM traces WHERE trace_id = %s", (trace_id,)
        ).fetchone()
        if row is not None:
            return row
        return conn.execute(
            f"SELECT {TRACE_COLUMNS} FROM traces "
            "WHERE lower(substr(trace_id, 1, length(%s::text))) = lower(%s::text) "
            'ORDER BY trace_id COLLATE "C" LIMIT 1',
            (trace_id, trace_id),
        ).fetchone()

    def _span_rows(self, trace_id: str) -> list[tuple]:
        return self._conn().execute(
            f"SELECT {SPAN_COLUMNS} FROM spans WHERE trace_id = %s ORDER BY {_SPAN_ORDER}",
            (trace_id,),
        ).fetchall()

    def _trace_rows(self, limit: int) -> list[tuple]:
        return self._conn().execute(
            f"SELECT {TRACE_COLUMNS} FROM traces ORDER BY {_TRACE_ORDER} LIMIT %s",
            (limit,),
        ).fetchall()

    def _cache_write(self, row: tuple) -> None:
        self._conn().execute(
            "INSERT INTO llm_cache (fingerprint, request, response, created_at) "
            "VALUES (%s,%s,%s,%s) ON CONFLICT (fingerprint) DO UPDATE SET "
            "request = EXCLUDED.request, response = EXCLUDED.response, "
            "created_at = EXCLUDED.created_at",
            row,
        )

    def _cache_read(self, fingerprint: str) -> str | None:
        row = self._conn().execute(
            "SELECT response FROM llm_cache WHERE fingerprint = %s", (fingerprint,)
        ).fetchone()
        return row[0] if row else None

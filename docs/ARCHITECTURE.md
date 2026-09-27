# Architecture

AgentRewind is local-first: the core uses only the Python standard library and stores data in a
SQLite database. Two optional extras add to it: FastAPI/Uvicorn for the browser viewer, and
psycopg 3 for a PostgreSQL storage backend shared by many writer processes.

```text
agent code → trace/span SDK → in-memory span tree → trace store → CLI / web viewer / diff
                │                                      │
                └→ Recorder → request fingerprint → replay cache (same store)
                                                       │
                                                       └→ offline deterministic replay

trace store = BaseStore ─┬─ SQLiteStore    (default, ~/.agentrewind/traces.db)
                         └─ PostgresStore  (AGENTREWIND_DB_URL=postgresql://…)
```

`Recorder` is provider-agnostic. The OpenAI and Anthropic adapters translate SDK-shaped calls
into plain request/response dictionaries, allowing the same fingerprinting, caching, tracing,
streaming capture, and diff logic to apply to either provider.

The trace diff walks span trees in execution order. It reports structural changes first, then
input, output, and status changes at each aligned span, making the earliest divergence the
natural debugging starting point.

For sensitive environments, `RedactionPolicy` is applied at persistence time. It leaves the
application's in-memory objects untouched while removing common credentials from stored traces
and cached responses, on either backend.

## Storage

`store.BaseStore` owns everything that is not SQL: turning traces into rows, redaction,
export/import validation, and cache (de)serialisation. A backend implements six primitives
(write a trace atomically, find a trace row, read its spans, list traces, cache write, cache
read) plus `close()`. `open_store(target)` picks the backend: a `postgres://` or
`postgresql://` URL opens `PostgresStore` (in `postgres.py`, which is the only module that
imports psycopg), anything else is a SQLite path. The SDK resolves the global store as:
explicit `configure(store=…)` → `configure(db_path=…)` / CLI `--db` → `AGENTREWIND_DB_URL` →
`AGENTREWIND_DB` → `~/.agentrewind/traces.db`. `TraceStore` remains an alias for
`SQLiteStore`.

### One schema, one behaviour

Both backends create the same three tables (`traces`, `spans`, `llm_cache`) with the same
columns. The types map one-to-one: SQLite `REAL` is PostgreSQL `DOUBLE PRECISION`, so
timestamps round-trip exactly, and JSON payloads are `TEXT` on both. They are not stored as
`JSONB`, which would reorder keys and change what export and diff produce.

Where the two databases' defaults differ, the queries pin the behaviour explicitly:

| Concern | Rule on both backends |
|---|---|
| Span order | `started_at`, then `seq` (the span's index in `Trace.spans`), then `span_id`. Spans with identical timestamps come back in execution order, so diffs do not flap. |
| Rows without `seq` (written by 0.2.x) | Sort after rows with one at the same timestamp (`seq IS NULL` is an explicit sort key, because SQLite and PostgreSQL place NULLs differently), then by `span_id`. |
| Trace list order | `started_at DESC`, then `trace_id`. |
| Id lookup | Exact id first; otherwise the lowest id with that case-insensitive prefix. Implemented with `substr`, not `LIKE`, so `%` and `_` in user input are literal. |
| Text tiebreak collation | PostgreSQL sorts ids with `COLLATE "C"` to match SQLite's byte-wise `BINARY` collation regardless of the database locale. |
| `spans.trace_id` foreign key | Declared on SQLite, but SQLite does not enforce it by default, so PostgreSQL does not enforce one either. |

### Schema migration

0.3.0 added `spans.seq`. `SQLiteStore` migrates an older file when it opens it: if the column
is missing, it takes a `BEGIN IMMEDIATE` write lock, checks again, and runs
`ALTER TABLE spans ADD COLUMN seq INTEGER`. This is idempotent and safe when several processes
open the same old file at once. Existing rows keep `seq = NULL` and use the fallback order
above until they are next saved. The migration is one-way: 0.2.x writes spans with a
positional 12-column `INSERT`, which fails against the 13-column table.

### Concurrency

- **SQLite**: WAL mode, one connection per thread, and a 30 s busy timeout. Writers queue on
  SQLite's single database-wide write lock, so concurrent writer processes are safe but
  serialised.
- **PostgreSQL**: one autocommit connection per (process, thread). A connection inherited
  across `fork()` is detected by PID and replaced, never shared. `save_trace` runs in one
  transaction and starts with `INSERT … ON CONFLICT (trace_id) DO UPDATE` on `traces`. The
  row lock that statement takes serialises writers of the *same* trace, so one writer's
  delete-then-reinsert of spans cannot interleave with another's, while writers of
  different traces commit in parallel. Schema creation takes `pg_advisory_xact_lock`,
  because concurrent `CREATE TABLE IF NOT EXISTS` can still collide in PostgreSQL's catalog.
  Error messages and `repr()` never include the DSN password.

The test suite runs every test against both backends (`tests/conftest.py`), and
`tests/test_storage_backends.py` spawns separate writer processes to check that concurrent
writes lose nothing and never mix span sets. See [EVALUATION.md](EVALUATION.md) for measured
multi-process write throughput.

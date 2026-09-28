# Changelog

## Unreleased
- Fix: a `SQLiteStore(":memory:")` store now works from every thread, including the web
  viewer's worker threads. Previously each thread saw its own empty database and failed with
  `no such table: traces` (KI-5). In-memory stores now share one connection guarded by a
  lock; file-backed stores are unchanged.

## 0.3.0 — 2026-09-27
- Optional PostgreSQL storage backend: `pip install 'llm-run-recorder[postgres]'` (psycopg 3),
  selected with `AGENTREWIND_DB_URL=postgresql://…` or a URL passed to `--db`,
  `configure(db_path=…)` or `open_store(…)`. The core remains stdlib-only and SQLite remains
  the zero-setup default.
- Storage interface: `BaseStore`, `SQLiteStore` and `open_store()` are exported.
  `TraceStore` is still accepted and is an alias for `SQLiteStore`.
- Safe for many concurrent writer processes on PostgreSQL: a row lock serialises writes to the
  same trace, schema creation takes an advisory lock, and connections are never reused
  across `fork()`. SQLite writers now wait up to 30 s for the write lock (previously 5 s), and
  processes opening the same new or rollback-journal file at once retry the switch to WAL mode
  instead of failing with `database is locked`.
- Deterministic ordering on both backends: new `spans.seq` column so spans with identical
  timestamps return in execution order; trace-list ties broken by id.
- `get_trace` prefix lookup: an exact id match now always wins, an ambiguous prefix resolves
  to the lowest id, and `%` / `_` in the lookup are matched literally rather than as `LIKE`
  wildcards.
- **Migration:** opening a 0.2.x `traces.db` adds `spans.seq` automatically and in place.
  Existing traces are kept and ordered by (timestamp, span id). The change is one-way:
  0.2.x cannot write to a migrated file.
- `benchmarks/storage_benchmark.py`: concurrent-writer-process benchmark for both backends.
- `agentrewind serve` without the server extra now suggests the correct package name,
  `pip install 'llm-run-recorder[server]'`.
- Tests run against both backends; `docker-compose.yml` provides local PostgreSQL. The 97%
  coverage floor is now enforced in CI only.

## 0.2.2 — 2026-07-12
- Privacy controls: opt-in `RedactionPolicy` recursively removes common credential fields and
  token formats before trace payloads, metadata, and replay-cache entries reach SQLite.
- Package metadata/docs: PyPI-compatible absolute demo image URL and version 0.2.1 release.

## 0.2.0 — 2026-07-12
- Streaming capture: `Recorder.call_stream` / `acall_stream` record chunks while passing
  them through live; replay re-streams identical chunks offline. Usage picked up from the
  final chunk (OpenAI `stream_options` / Anthropic `message_delta` convention).
- Auto-instrumentation: `agentrewind.instrument(client)` patches an OpenAI- or
  Anthropic-shaped client in place — no code changes at call sites.
- Web viewer: select two runs to open a side-by-side divergence view; new
  `/api/diff/{left}/{right}` endpoint.
- Demo GIF and vhs tape (`docs/demo.tape`).

## 0.1.0 — 2026-07-12
- Trace SDK: `trace()`, `span()`, `@traced` (sync + async), `record_llm_call`.
- Zero-config SQLite trace store (`~/.agentrewind/traces.db`).
- Record/replay engine: `Recorder` with `record` / `replay` / `auto` modes, canonical
  request fingerprinting, `canonicalize` hook for volatile fields, async `acall`.
- Provider wrappers: `OpenAIChat`, `AnthropicMessages` (duck-typed, no SDK dependency).
- Structural run diff: `agentrewind diff` with execution-ordered divergences; exit code 2
  on divergence for use as a CI regression gate.
- CLI (`list` / `show` / `diff` / `serve`) and built-in web trace viewer.
- Offline example agent with a seeded regression (`examples/research_agent.py`).

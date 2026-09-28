# AgentRewind — working context

Flight recorder for LLM agents: record a run, replay it offline and deterministically, diff two
runs to find where behavior diverged. Stdlib-only core; the FastAPI viewer (`[server]`) and the
PostgreSQL storage backend (`[postgres]`, psycopg 3) are optional extras.

Published on PyPI as **`llm-run-recorder`** (the import name is `agentrewind`).

## Layout

```
src/agentrewind/
  sdk.py         trace() / span() context managers, global store config
  replay.py      Recorder: record | replay modes, sync + async
  store.py       BaseStore interface, SQLiteStore (default), open_store(), export/import
  postgres.py    PostgresStore (optional; the only module that imports psycopg)
  diff.py        structural diff between two traces
  models.py      Span, Trace, SpanKind, Status
  providers.py   OpenAI / Anthropic wrappers
  redaction.py   opt-in credential scrubbing before writes
  cli.py         list | show | diff | export | import | serve
  server.py      single-file HTML trace viewer
benchmarks/replay_benchmark.py   offline throughput + zero-provider-call proof
benchmarks/storage_benchmark.py  1–32 concurrent writer processes, SQLite vs PostgreSQL
benchmarks/live_replay_benchmark.py  real OpenAI agent (50 calls): live vs replay wall-clock
docs/EVALUATION.md               benchmark protocol and reference results
tests/fixtures/traces_v0_2_2.db  written by the published 0.2.2 wheel; migration tests
```

## Commands

```bash
pip install -e '.[dev]'
pytest                                          # SQLite half; PostgreSQL cases skip
docker compose up -d                            # PostgreSQL 17 on localhost:55432
export AGENTREWIND_DB_URL=postgresql://agentrewind:agentrewind@localhost:55432/agentrewind
pytest --cov-fail-under=97                      # full suite, both backends (what CI runs)
ruff check .
python benchmarks/replay_benchmark.py --calls 1000
python benchmarks/storage_benchmark.py          # needs AGENTREWIND_DB_URL for the PG half
```

## Verified metrics

All values below were measured on 2026-09-27 unless noted. Re-derive with the command shown;
do not estimate these.

| Metric | Value | How to re-derive |
|---|---|---|
| PyPI package | `llm-run-recorder` | `curl -s https://pypi.org/pypi/llm-run-recorder/json` |
| Latest published version | 0.3.1 (uploaded 2026-09-28 UTC, tag `v0.3.1`) | same; `info.version` |
| Published releases | 0.2.0, 0.2.1, 0.2.2, 0.3.0, 0.3.1 (wheel + sdist each) | same; `releases` |
| Test count | 296 (148 per backend); 146 pass + 150 skip without `AGENTREWIND_DB_URL` | `pytest --collect-only -q` with `AGENTREWIND_DB_URL` set |
| Line coverage | 99% (770 statements, 1 missed) | `pytest --cov=src/agentrewind --cov-report=term -o addopts=""` with PG |
| Branch + line coverage | 99.57% with PG | `pytest` (branch mode is the default in pyproject) |
| Coverage floor | 97%, **CI only** | `.github/workflows/ci.yml:47` (`--cov-fail-under=97`) |
| CI Python matrix | 3.10, 3.12, 3.13 (**not** 3.11) | `.github/workflows/ci.yml:17` |
| Concurrency level in record→replay test | 12 concurrent runs, 2 LLM calls each | `tests/test_concurrency.py:15` (`N_RUNS = 12`), test at `:249` |

### Replay economics

At the benchmark's default `--calls 1000`:

| | Provider API calls |
|---|---:|
| Recording the run | 1000 |
| Replaying the same run | **0** |
| Saved by replay | 1000 (100%) |

This figure scales with `--calls`; it is not a fixed property of the library. The invariant that
*is* fixed: replay never reaches the provider. `replay_fidelity` is `true` on every run.

Throughput, Apple M4 / macOS 15.7.4 / Python 3.13.5 / `--calls 1000`, 3-run median
(`docs/EVALUATION.md:28-33`, measured 2026-07-15): record **8027.01 req/s**, replay
**62345.43 req/s** — replay ~7.8x faster. A 2026-09-21 spot check gave 8324.57 / 61629.64.
Do not compare throughput across machines.

### Live run vs replay (real OpenAI API)

`benchmarks/live_replay_benchmark.py`, 2026-09-27, `gpt-4.1-mini-2025-04-14`, llm-run-recorder
0.3.1 from PyPI, Apple M4 / Python 3.13.5, 3 runs: **50** LLM calls per live run, **0** on
replay. Median live **52.7756 s**, median replay **0.0055 s** (about 9,600×). Replayed
answers were identical in all 3 runs. Raw JSON: `docs/benchmarks/live-replay-2026-09-27-apple-m4.json`.
Live time is provider latency and varies; each run costs about 50 real API calls.

### Concurrent writers (storage benchmark)

Apple M4, Python 3.13.5, PostgreSQL 17.11 in the compose container (Docker Desktop), 1000
traces per writer, 3-run median, measured 2026-09-27 (`docs/EVALUATION.md`, raw JSON in
`docs/benchmarks/storage-2026-09-27-apple-m4.json`). At 32 writer processes: SQLite
**6968.78** traces/s (p95 1.547 ms), PostgreSQL **3129.11** traces/s (p95 17.064 ms) —
PostgreSQL/SQLite throughput ratio **0.45**. 0 write errors in all 30 runs. On this setup
SQLite was faster at every level; the PG server was behind Docker Desktop's VM network, so
this is not a general backend ranking.

### Per-module coverage (2026-09-27, with PostgreSQL)

`cli.py`, `diff.py`, `models.py`, `postgres.py`, `providers.py`, `redaction.py`, `server.py`
at 100%; `store.py` 99%, `sdk.py` 99%, `replay.py` 98%.

## What CI enforces

`.github/workflows/ci.yml` on push to main and every PR: `ruff check`, `pytest
--cov-fail-under=97` against SQLite **and** a `postgres:17` service container
(`AGENTREWIND_REQUIRE_POSTGRES=1` turns a missing database into a failure rather than a skip),
`python -m build` plus `twine check`, across the three-version matrix. Coverage
XML is uploaded as an artifact from the 3.13 leg.

## Known issues

`docs/KNOWN_ISSUES.md` tracks open bugs, deliberate limitations and resolved issues with IDs
(`KI-n`). Read it before starting work. When you find a bug, add it under Open with repro
steps, even if you fix it in the same change. When you fix one, move it to Resolved with the
commit and release.

## Gotchas

- **Editable installs can silently fail to put `src/` on `sys.path`** on some local setups (the
  hatchling `.pth` is not honored, and `import agentrewind` raises ModuleNotFoundError even
  though `pip install -e` reported success). Workaround: `PYTHONPATH=src pytest`. A
  non-editable `pip install .` works fine, and CI on Ubuntu is unaffected.
- `SpanKind` has exactly three values: `span`, `llm`, `tool`. Passing anything else raises.
- `Span.error` is formatted as `"<ExceptionType>: <message>"`, not the bare message.
- `cmd_diff` returns exit code **2** when runs differ, 1 when a trace is missing, 0 when
  identical. The 2 is deliberate — it is the regression-gate signal.
- `AGENTREWIND_DB_URL` selects the backend for the app **and** enables the PostgreSQL half of
  the test suite. Compose publishes on host port **55432**, not 5432 (a Homebrew PostgreSQL
  already listens on 5432 on this machine).
- Opening a 0.2.x `traces.db` adds `spans.seq` in place. One-way: 0.2.x cannot write to the
  migrated file (positional 12-column INSERT).
- `*.db` is gitignored; `tests/fixtures/*.db` is explicitly un-ignored. Regenerate the fixture
  only with the published 0.2.2 wheel (`tests/fixtures/make_v0_2_2_db.py`).
- The remaining test warnings are third-party deprecations (starlette/anyio testclient), not ours.
  `pyproject.toml` errors on `DeprecationWarning` originating in `agentrewind.*` only.

## Dependencies

Renovate (`renovate.json`): weekly Monday, GitHub Actions bumps grouped and automerged once CI
is green, majors get a standalone PR plus a 7-day soak. Requires the Renovate GitHub App to be
installed on the repo.

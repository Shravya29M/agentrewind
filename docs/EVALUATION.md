# Evaluation protocol

AgentRewind's key promise is deterministic, offline replay. The benchmark is deliberately
offline and dependency-free so anyone can reproduce its measurements:

```bash
python benchmarks/replay_benchmark.py --calls 1000
```

It reports JSON with four decision-relevant metrics:

| Metric | What it establishes |
|---|---|
| `replay_fidelity` | Replayed responses exactly match their recorded responses. |
| `provider_calls_while_replaying` | Must be `0`; replay never reaches the provider. |
| `record_requests_per_second` | Local trace/cache write throughput on the current machine. |
| `replay_requests_per_second` | Cache-read throughput on the current machine. |

Do not compare raw throughput across machines. For a portfolio case study, report the machine,
Python version, call count, and complete JSON output. Repeat the run three times and report the
median; the replay-fidelity and zero-provider-call checks must hold on every run.

## Reference results

Measured 2026-07-15 following the protocol above (three runs, median reported).
Machine: Apple M4, macOS 15.7.4, Python 3.13.5, `--calls 1000`.

| Metric | Run 1 | Run 2 | Run 3 | Median |
|---|---|---|---|---|
| `record_requests_per_second` | 9510.31 | 8027.01 | 7009.11 | **8027.01** |
| `replay_requests_per_second` | 60035.57 | 62712.80 | 62345.43 | **62345.43** |
| `replay_fidelity` | true | true | true | — |
| `provider_calls_while_replaying` | 0 | 0 | 0 | — |

Replay served cached responses ~7.8x faster than the initial recorded run and made zero
provider calls, so replaying a traced agent run costs nothing in API usage.

## Live run vs replay with a real provider

The offline benchmark above uses a stub provider. `benchmarks/live_replay_benchmark.py`
measures replay against a **real** LLM API instead. A small research agent answers 10
questions, and each question takes exactly 5 real LLM calls:

1. plan the approach
2. choose a tool through OpenAI function calling (a calculator or a local fact lookup, which
   then runs locally)
3. draft an answer
4. critique the draft
5. write the final answer

That makes 50 LLM calls per run. Each run records the live agent run into a fresh SQLite
file, reopens the store from disk, and replays the same agent. During replay the provider
function raises if it is called, so zero provider calls is enforced rather than assumed. Both
timings are wall-clock for the whole agent run, including tool calls and saving the trace.

```bash
pip install openai && export OPENAI_API_KEY=...
python benchmarks/live_replay_benchmark.py --runs 3 --output live.json   # ~150 real API calls
```

### Reference results

Measured 2026-09-27. Apple M4, macOS 15.7.4, Python 3.13.5, llm-run-recorder 0.3.1 (the
PyPI release installed in a clean venv), openai 3.19.2, model `gpt-4.1-mini-2025-04-14`,
`temperature=0`. Raw JSON:
[`benchmarks/live-replay-2026-09-27-apple-m4.json`](benchmarks/live-replay-2026-09-27-apple-m4.json).

| | Run 1 | Run 2 | Run 3 | Median |
|---|---:|---:|---:|---:|
| LLM calls, live run | 50 | 50 | 50 | — |
| LLM calls, replay | 0 | 0 | 0 | — |
| Live run, seconds | 56.0335 | 52.7756 | 50.9681 | **52.7756** |
| Replay, seconds | 0.0056 | 0.0055 | 0.0042 | **0.0055** |
| Replayed answers identical to live | true | true | true | — |

On these medians, replaying the 50-call run took 0.0055 s instead of 52.78 s, about 9,600×
faster, with no API usage.

Almost all of the live time is network and model latency: about 1.06 s per call in the
median run. It varies with provider load, the model, prompt size and output length, so the
ratio is a property of this workload and this day, not a constant of AgentRewind. Replay time
is local store reads plus writing the replay's trace, about 0.1 ms per call here.

## Concurrent writers: SQLite vs PostgreSQL

The PostgreSQL backend exists to give many machines one **shared** trace store that any
teammate can query across runs. It is not meant to be faster. This benchmark checks that the
shared store stays correct when many processes write to it at once, and records what that
costs compared with the single-machine SQLite default.

`benchmarks/storage_benchmark.py` measures what happens when many separate **processes**
write traces into one store at once. Each configuration starts W writer processes (spawned,
not forked). Each process opens its own store, waits at a shared barrier, then saves 1000
distinct traces as fast as it can. Every trace has 4 spans: a root span, two LLM calls and
one tool call, with realistic message and tool payloads. Every `save_trace` call is timed.

- **traces/sec**: successful saves divided by the time from the first writer starting to the
  last writer finishing.
- **p95 write latency**: nearest-rank 95th percentile over every save in the run.
- Every (backend, W) pair runs 3 times, each time against a **fresh** database (a new SQLite
  file, or a new PostgreSQL schema). The table reports the median of the 3 runs.
- Failed saves are counted and never retried. After each run, the number of stored traces
  is checked against the number of successful saves.

```bash
docker compose up -d
export AGENTREWIND_DB_URL=postgresql://agentrewind:agentrewind@localhost:55432/agentrewind
python benchmarks/storage_benchmark.py --output results.json
```

### Reference results

Measured 2026-09-27. The raw JSON for all 30 runs is in
[`benchmarks/storage-2026-09-27-apple-m4.json`](benchmarks/storage-2026-09-27-apple-m4.json).

| Setting | Value |
|---|---|
| Machine | Apple M4 (10 cores), macOS 15.7.4, Python 3.13.5 |
| SQLite | 3.53.4 on the local SSD, `journal_mode=wal`, `synchronous=FULL` (2), `fullfsync` off (the SQLite default) |
| PostgreSQL | 17.11 in the `docker-compose.yml` container (Docker Desktop linuxkit VM, Docker Engine 28.3.2, 10 CPUs and 8 GB allocated), stock settings: `fsync=on`, `synchronous_commit=on`, `shared_buffers=128MB`. Reached over TCP through Docker's port forward to `localhost:55432` |
| psycopg | 3.3.6 (binary) |

| Writer processes | SQLite traces/s | SQLite p95 ms | PostgreSQL traces/s | PostgreSQL p95 ms | PostgreSQL ÷ SQLite throughput |
|---:|---:|---:|---:|---:|---:|
| 1  | 10707.02 | 0.112 | 874.59  | 2.770  | 0.08 |
| 4  | 9130.56  | 0.123 | 2795.96 | 2.562  | 0.31 |
| 8  | 7816.63  | 0.254 | 3438.51 | 3.192  | 0.44 |
| 16 | 7484.12  | 1.181 | 2917.55 | 9.260  | 0.39 |
| 32 | 6968.78  | 1.547 | 3129.11 | 17.064 | 0.45 |

Across all 30 runs there were 0 write errors on either backend, and every trace was stored.

### Reading the results

- **On this setup SQLite had the higher throughput at every concurrency level.** At 32
  writers PostgreSQL reached 0.45× SQLite's throughput (3129.11 vs 6968.78 traces/s).
- **SQLite's throughput fell as writers were added** (10707 → 6969 traces/s), and its p95
  latency rose 14× (0.112 → 1.547 ms). That fits SQLite's single database-wide write lock,
  where writers queue rather than run in parallel.
- **PostgreSQL's throughput rose 3.9× from 1 to 8 writers, then levelled off at
  medians of about 2900–3400 traces/s.** Its p95 latency rose from 2.8 ms to 17.1 ms. A single PostgreSQL
  writer paid about 2.8 ms per save, against 0.11 ms for SQLite.
- **The two setups are not equivalent**, so read the ratio as a result for this
  configuration, not as a general ranking of the backends:
  - PostgreSQL ran inside the Docker Desktop VM and was reached over TCP, so every
    statement of a save crossed the VM network boundary.
  - SQLite wrote directly to the host SSD. With `fullfsync` off, SQLite on macOS commits
    with `fsync()`, which does not force the drive's write cache the way `F_FULLFSYNC` does.
  - We have not measured how much each factor contributes, or where PostgreSQL's plateau
    comes from. A PostgreSQL server on native Linux, or on separate hardware, may produce a
    different curve.
- **What the benchmark does establish:** under 32 concurrent writer processes, both backends
  completed every write with no errors and no lost or partial traces.
  `tests/test_storage_backends.py` also checks that concurrent writers of the *same* trace
  never leave a mixture of two writers' spans.

Choose PostgreSQL when traces must be shared: writers on different machines, containers or
CI runners recording into one store, and teammates listing, diffing and querying runs with SQL
wherever those runs were recorded. A SQLite file cannot provide that across machines. Do not
choose it for speed: for writers on one host, the SQLite default was faster in these
measurements.

## Regression-gate workflow

1. Run a representative agent with a known-good prompt/tool configuration.
2. Save the run as a portable fixture: `agentrewind export <trace-id> -o baseline.json`.
3. In CI, run the candidate agent against deterministic mocks, import the baseline, and compare
   the resulting trace with `agentrewind diff`.
4. Treat exit code `2` as a behavior change requiring review, rather than a silent regression.

This turns trace data into a reviewable, versioned behavioral contract without requiring a SaaS
service.

"""Concurrent-writer benchmark for the SQLite and PostgreSQL storage backends.

Each configuration starts W writer *processes* (spawn), which open their own store, wait
at a shared barrier, then each call ``save_trace`` on N distinct traces as fast as they
can. Every save is timed individually.

    traces_per_second = successful saves / (last writer finish - first writer start)
    p95 write latency = 95th percentile (nearest rank) over every save in the run

Each (backend, W) pair runs --runs times against a fresh database (new SQLite file / new
PostgreSQL schema) and the median is reported. Failed saves are counted, never retried,
and the row count is verified against the number of successful saves after every run.

    docker compose up -d
    export AGENTREWIND_DB_URL=postgresql://agentrewind:agentrewind@localhost:55432/agentrewind
    python benchmarks/storage_benchmark.py --output results.json

Throughput depends on the machine, disk and database configuration. Do not compare
numbers across machines.
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import os
import platform
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import uuid

from agentrewind.models import Span, SpanKind, Status, Trace
from agentrewind.store import DB_URL_ENV, open_store

DEFAULT_WRITERS = (1, 4, 8, 16, 32)


def make_trace(writer: int, i: int, run_tag: str) -> Trace:
    """A representative agent run: one root span, two LLM calls, one tool call."""
    trace_id = f"{run_tag}-{writer:02d}-{i:05d}"
    now = time.time()
    trace = Trace(
        name="bench-agent",
        trace_id=trace_id,
        started_at=now,
        ended_at=now + 1.5,
        status=Status.OK,
        metadata={"writer": writer, "i": i, "suite": "storage-benchmark"},
    )
    root = Span(trace_id=trace_id, name="agent", started_at=now, ended_at=now + 1.5,
                status=Status.OK, input={"task": "summarise the quarterly report " * 4})
    messages = [
        {"role": "system", "content": "You are a careful research assistant. " * 6},
        {"role": "user", "content": f"Question {i} from writer {writer}: " + "context " * 40},
    ]
    llm1 = Span(trace_id=trace_id, parent_id=root.span_id, name="plan", kind=SpanKind.LLM,
                started_at=now + 0.1, ended_at=now + 0.6, status=Status.OK,
                input={"model": "bench-model", "messages": messages, "temperature": 0},
                output={"content": "Plan: search, then answer. " * 8},
                attributes={"model": "bench-model", "prompt_tokens": 312,
                            "completion_tokens": 64})
    tool = Span(trace_id=trace_id, parent_id=root.span_id, name="search", kind=SpanKind.TOOL,
                started_at=now + 0.6, ended_at=now + 0.9, status=Status.OK,
                input={"query": f"quarterly report {i}"},
                output={"results": [{"title": f"doc {k}", "snippet": "lorem ipsum " * 10}
                                    for k in range(3)]})
    llm2 = Span(trace_id=trace_id, parent_id=root.span_id, name="answer", kind=SpanKind.LLM,
                started_at=now + 0.9, ended_at=now + 1.4, status=Status.OK,
                input={"model": "bench-model", "messages": messages[-1:], "temperature": 0},
                output={"content": "The report shows steady growth. " * 10},
                attributes={"model": "bench-model", "prompt_tokens": 540,
                            "completion_tokens": 120})
    trace.spans = [root, llm1, tool, llm2]
    return trace


def writer_proc(target, writer, n_traces, run_tag, barrier, results):
    store = open_store(target)
    traces = [make_trace(writer, i, run_tag) for i in range(n_traces)]
    latencies: list[float] = []
    errors: list[str] = []
    barrier.wait()
    start = time.monotonic()
    for trace in traces:
        t0 = time.perf_counter()
        try:
            store.save_trace(trace)
        except Exception as exc:  # counted and reported, never retried
            errors.append(f"{type(exc).__name__}: {exc}")
            continue
        latencies.append(time.perf_counter() - t0)
    end = time.monotonic()
    store.close()
    results.put({"writer": writer, "start": start, "end": end,
                 "latencies": latencies, "errors": errors})


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


class Target:
    """A fresh, empty database for one run."""

    def __init__(self, backend: str, pg_url: str | None):
        self.backend, self.pg_url = backend, pg_url

    def __enter__(self) -> str:
        if self.backend == "sqlite":
            self._dir = tempfile.mkdtemp(prefix="agentrewind-bench-")
            return os.path.join(self._dir, "traces.db")
        import psycopg

        self._schema = f"ar_bench_{uuid.uuid4().hex[:12]}"
        with psycopg.connect(self.pg_url, autocommit=True) as conn:
            conn.execute(f"CREATE SCHEMA {self._schema}")
        sep = "&" if "?" in self.pg_url else "?"
        return f"{self.pg_url}{sep}options=-csearch_path%3D{self._schema}"

    def __exit__(self, *exc) -> None:
        if self.backend == "sqlite":
            shutil.rmtree(self._dir, ignore_errors=True)
            return
        import psycopg

        with psycopg.connect(self.pg_url, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA {self._schema} CASCADE")


def run_once(backend: str, pg_url: str | None, writers: int, n_traces: int) -> dict:
    ctx = multiprocessing.get_context("spawn")
    with Target(backend, pg_url) as target:
        open_store(target).close()  # create the schema outside the timed region
        barrier, results = ctx.Barrier(writers), ctx.Queue()
        run_tag = uuid.uuid4().hex[:8]
        procs = [
            ctx.Process(target=writer_proc,
                        args=(target, w, n_traces, run_tag, barrier, results))
            for w in range(writers)
        ]
        for p in procs:
            p.start()
        reports = [results.get(timeout=600) for _ in procs]
        for p in procs:
            p.join()
        if any(p.exitcode != 0 for p in procs):
            raise RuntimeError(f"writer exit codes: {[p.exitcode for p in procs]}")
        check = open_store(target)
        stored = len(check.list_traces(limit=writers * n_traces + 1))
        check.close()

    latencies = [x for r in reports for x in r["latencies"]]
    errors = [e for r in reports for e in r["errors"]]
    elapsed = max(r["end"] for r in reports) - min(r["start"] for r in reports)
    if stored != len(latencies):
        raise RuntimeError(f"{stored} traces stored but {len(latencies)} saves succeeded")
    return {
        "writers": writers,
        "traces_attempted": writers * n_traces,
        "traces_written": len(latencies),
        "write_errors": len(errors),
        "error_samples": sorted(set(errors))[:3],
        "elapsed_seconds": round(elapsed, 6),
        "traces_per_second": round(len(latencies) / elapsed, 2),
        "p50_write_ms": round(statistics.median(latencies) * 1000, 3),
        "p95_write_ms": round(p95(latencies) * 1000, 3),
    }


def environment(backends: list[str], pg_url: str | None) -> dict:
    env = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
    }
    if sys.platform == "darwin":
        env["cpu"] = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                    capture_output=True, text=True).stdout.strip()
    with tempfile.TemporaryDirectory() as d:
        probe = open_store(os.path.join(d, "probe.db"))
        conn = probe._conn()
        env["sqlite_journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
        env["sqlite_synchronous"] = conn.execute("PRAGMA synchronous").fetchone()[0]
        probe.close()
    if "postgres" in backends:
        import psycopg

        with psycopg.connect(pg_url) as conn:
            env["psycopg"] = psycopg.__version__
            env["postgres"] = conn.execute("SHOW server_version").fetchone()[0]
            for setting in ("fsync", "synchronous_commit", "max_connections", "shared_buffers"):
                env[f"postgres_{setting}"] = conn.execute(f"SHOW {setting}").fetchone()[0]
    return env


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark concurrent trace writers")
    parser.add_argument("--backends", default="sqlite,postgres")
    parser.add_argument("--writers", default=",".join(map(str, DEFAULT_WRITERS)))
    parser.add_argument("--traces-per-writer", type=int, default=1000)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--pg-url", default=os.environ.get(DB_URL_ENV))
    parser.add_argument("--output", help="also write the full JSON report here")
    args = parser.parse_args()

    backends = args.backends.split(",")
    writer_counts = [int(w) for w in args.writers.split(",")]
    if "postgres" in backends and not args.pg_url:
        parser.error(f"PostgreSQL needs --pg-url or {DB_URL_ENV} (see docker-compose.yml)")

    report = {
        "environment": environment(backends, args.pg_url),
        "traces_per_writer": args.traces_per_writer,
        "spans_per_trace": 4,
        "runs": args.runs,
        "results": {},
    }
    for backend in backends:
        for writers in writer_counts:
            runs = []
            for _ in range(args.runs):
                runs.append(run_once(backend, args.pg_url, writers, args.traces_per_writer))
                print(f"{backend:8} W={writers:<3} {runs[-1]['traces_per_second']:>10} tr/s  "
                      f"p95 {runs[-1]['p95_write_ms']:>8} ms  "
                      f"errors {runs[-1]['write_errors']}", file=sys.stderr)
            report["results"].setdefault(backend, {})[str(writers)] = {
                "median_traces_per_second": statistics.median(
                    r["traces_per_second"] for r in runs),
                "median_p95_write_ms": statistics.median(r["p95_write_ms"] for r in runs),
                "total_write_errors": sum(r["write_errors"] for r in runs),
                "runs": runs,
            }

    text = json.dumps(report, indent=2)
    if args.output:
        with open(args.output, "w") as fh:
            fh.write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()

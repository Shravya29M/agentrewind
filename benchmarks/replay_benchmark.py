"""Reproducible, offline replay benchmark for AgentRewind.

Run from an editable install:

    python benchmarks/replay_benchmark.py --calls 1000
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time

from agentrewind.replay import Recorder
from agentrewind.store import TraceStore


def measure(calls: int) -> dict[str, float | int | bool]:
    provider_calls = 0

    def provider(request: dict) -> dict:
        nonlocal provider_calls
        provider_calls += 1
        return {"content": f"response:{request['id']}", "usage": {"prompt_tokens": 1}}

    requests = [
        {"id": i, "model": "benchmark", "messages": [{"content": str(i)}]}
        for i in range(calls)
    ]
    with tempfile.TemporaryDirectory() as directory:
        store = TraceStore(f"{directory}/traces.db")
        recorder = Recorder(provider, mode="record", store=store)
        started = time.perf_counter()
        recorded = [recorder.call(request) for request in requests]
        record_seconds = time.perf_counter() - started

        replay = Recorder(provider, mode="replay", store=store)
        calls_before_replay = provider_calls
        started = time.perf_counter()
        replayed = [replay.call(request) for request in requests]
        replay_seconds = time.perf_counter() - started

    return {
        "calls": calls,
        "record_seconds": round(record_seconds, 6),
        "replay_seconds": round(replay_seconds, 6),
        "record_requests_per_second": round(calls / record_seconds, 2),
        "replay_requests_per_second": round(calls / replay_seconds, 2),
        "provider_calls_while_recording": calls_before_replay,
        "provider_calls_while_replaying": provider_calls - calls_before_replay,
        "replay_fidelity": recorded == replayed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark local AgentRewind replay")
    parser.add_argument("--calls", type=int, default=1_000)
    args = parser.parse_args()
    if args.calls < 1:
        parser.error("--calls must be positive")
    print(json.dumps(measure(args.calls), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

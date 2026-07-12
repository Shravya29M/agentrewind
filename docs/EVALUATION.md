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

## Regression-gate workflow

1. Run a representative agent with a known-good prompt/tool configuration.
2. Save the run as a portable fixture: `agentrewind export <trace-id> -o baseline.json`.
3. In CI, run the candidate agent against deterministic mocks, import the baseline, and compare
   the resulting trace with `agentrewind diff`.
4. Treat exit code `2` as a behavior change requiring review, rather than a silent regression.

This turns trace data into a reviewable, versioned behavioral contract without requiring a SaaS
service.

# AgentLens

**Flight recorder for LLM agents.** Trace every LLM and tool call an agent makes, replay a
run deterministically (no API calls, no cost, no nondeterminism), and diff two runs to find
exactly where their behavior diverged.

*"It worked yesterday — why is it different today?"* is the defining debugging problem of
agent development. Tracing tools show you what happened; AgentLens also lets you **re-execute**
what happened and **compare** runs structurally.

## Install

```bash
pip install agentlens            # core (stdlib-only)
pip install 'agentlens[server]'  # + web trace viewer
```

## Trace

```python
import agentlens as al

@al.traced(kind="tool")
def search(query: str) -> str:
    ...

with al.trace("research-agent"):
    plan = call_llm(...)          # record with al.record_llm_call(...) or a Recorder
    evidence = search(plan.query)
```

Traces (span tree, inputs/outputs, latency, token counts, errors) are stored in a local
SQLite db at `~/.agentlens/traces.db` — no account, no server required.

## Replay

Wrap your provider call in a `Recorder`. Responses are cached by a canonical request
fingerprint; in replay mode the whole agent run re-executes deterministically and offline:

```python
from agentlens.replay import Recorder

llm = Recorder(call_openai, mode="record")   # live run, responses cached
llm = Recorder(call_openai, mode="replay")   # deterministic re-run, zero API calls
llm = Recorder(call_openai, mode="auto")     # replay on hit, record on miss
```

## Diff

```
$ agentlens diff 3f2a91 8c17d0
2 divergence(s); first divergence is where the runs split:

1. [input] /llm:mock-4o — inputs differ
     left : {"messages":[{"content":"decide next step",...
     right: {"messages":[{"content":"decide the next step",...
2. [output] /tool:search — outputs differ
```

The diff walks both span trees in parallel and reports structure, input, and output
divergences in execution order — entry #1 is where the runs first split.

## CLI & viewer

```bash
agentlens list                 # recent runs
agentlens show <trace-id>      # span tree with latencies
agentlens diff <run1> <run2>   # structural diff (exit code 2 if runs differ)
agentlens serve                # web viewer at http://127.0.0.1:4317
```

## Try the demo (offline, no API key)

```bash
python examples/research_agent.py   # records two runs with a seeded regression
agentlens diff <run1> <run2>        # pinpoints the prompt change that caused it
```

## Development

```bash
pip install -e '.[dev]'
pytest
ruff check .
```

Note for macOS: if this repo lives in an iCloud-synced folder (e.g. `~/Documents`), create
your virtualenv *outside* it (e.g. `~/.venvs/agentlens`) — the file provider marks files in
dot-directories as hidden, and Python ≥3.13 skips hidden `.pth` files, which breaks
editable installs.

MIT licensed.

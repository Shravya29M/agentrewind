# AgentRewind — working context

Flight recorder for LLM agents: record a run, replay it offline and deterministically, diff two
runs to find where behavior diverged. Stdlib-only core; FastAPI viewer is an optional extra.

Published on PyPI as **`llm-run-recorder`** (the import name is `agentrewind`).

## Layout

```
src/agentrewind/
  sdk.py         trace() / span() context managers, global store config
  replay.py      Recorder: record | replay modes, sync + async
  store.py       SQLite persistence, export/import artifacts
  diff.py        structural diff between two traces
  models.py      Span, Trace, SpanKind, Status
  providers.py   OpenAI / Anthropic wrappers
  redaction.py   opt-in credential scrubbing before writes
  cli.py         list | show | diff | export | import | serve
  server.py      single-file HTML trace viewer
benchmarks/replay_benchmark.py   offline throughput + zero-provider-call proof
docs/EVALUATION.md               benchmark protocol and reference results
```

## Commands

```bash
pip install -e '.[dev]'
pytest                                          # coverage on by default, fails under 97%
ruff check .
python benchmarks/replay_benchmark.py --calls 1000
```

## Verified metrics

All values below were measured on 2026-09-21 unless noted. Re-derive with the command shown;
do not estimate these.

| Metric | Value | How to re-derive |
|---|---|---|
| PyPI package | `llm-run-recorder` | `curl -s https://pypi.org/pypi/llm-run-recorder/json` |
| Latest published version | 0.2.2 (uploaded 2026-07-12) | same; `info.version` |
| Published releases | 0.2.0, 0.2.1, 0.2.2 (wheel + sdist each) | same; `releases` |
| Test count | 108 | `pytest --collect-only -q` |
| Line coverage | 99% (593 statements, 2 missed) | `pytest --cov=src/agentrewind --cov-report=term -o addopts=""` |
| Branch + line coverage | 99.31% | `pytest` (branch mode is the default in pyproject) |
| Coverage floor enforced in CI | 97% | `pyproject.toml`, `[tool.pytest.ini_options] addopts` |
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

### Per-module coverage (2026-09-21)

`diff.py`, `models.py`, `providers.py`, `redaction.py`, `server.py`, `store.py` at 100%;
`cli.py` 98%, `replay.py` 98%, `sdk.py` 97%.

## What CI enforces

`.github/workflows/ci.yml` on push to main and every PR: `ruff check`, `pytest` with the 97%
coverage floor, `python -m build` plus `twine check`, across the three-version matrix. Coverage
XML is uploaded as an artifact from the 3.13 leg.

## Gotchas

- **Editable installs can silently fail to put `src/` on `sys.path`** on some local setups (the
  hatchling `.pth` is not honored, and `import agentrewind` raises ModuleNotFoundError even
  though `pip install -e` reported success). Workaround: `PYTHONPATH=src pytest`. A
  non-editable `pip install .` works fine, and CI on Ubuntu is unaffected.
- `SpanKind` has exactly three values: `span`, `llm`, `tool`. Passing anything else raises.
- `Span.error` is formatted as `"<ExceptionType>: <message>"`, not the bare message.
- `cmd_diff` returns exit code **2** when runs differ, 1 when a trace is missing, 0 when
  identical. The 2 is deliberate — it is the regression-gate signal.
- The 99 test warnings are third-party deprecations (starlette/anyio testclient), not ours.
  `pyproject.toml` errors on `DeprecationWarning` originating in `agentrewind.*` only.

## Dependencies

Renovate (`renovate.json`): weekly Monday, GitHub Actions bumps grouped and automerged once CI
is green, majors get a standalone PR plus a 7-day soak. Requires the Renovate GitHub App to be
installed on the repo.

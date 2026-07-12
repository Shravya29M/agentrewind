# PRD: AgentRewind v1

**Author:** Shravya Munugala · **Date:** July 12, 2026 · **Status:** v1 shipped; v1.1 in development

## Problem
Developers building LLM agents cannot reliably answer "why did this run behave differently
from the last one?" Agent runs are nondeterministic (sampling, provider drift, tool state),
expensive to re-execute, and existing observability tools (LangSmith, Langfuse, Braintrust)
show traces but do not let you *re-execute a run deterministically* or *structurally compare
two runs*. Debugging is manual log-reading.

## Users
- **Primary:** individual developers / small teams building agents (indie hackers, students,
  startup engineers) who want local-first tooling without a SaaS account.
- **Secondary:** teams writing regression tests for agents in CI.

## Alternatives & why they lose
| Alternative | Gap |
|---|---|
| LangSmith / Langfuse | Hosted-first, trace viewing only — no deterministic replay, no run diff |
| VCR.py / pytest-recording | HTTP-level cassettes; no agent semantics, no span tree, no diff |
| print debugging | The status quo; doesn't survive nondeterminism |

## v1 scope (shipped)
1. Trace SDK: `trace()`, `span()`, `@traced`, `record_llm_call` — stdlib-only core.
2. Local SQLite store, zero-config.
3. Record/replay of LLM calls via request fingerprinting (`Recorder`, 3 modes).
4. Structural run diff with execution-ordered divergences (`agentrewind diff`, exit code 2 on
   divergence → usable as a CI regression gate).
5. CLI + local web viewer.
6. Async tracing and replay, streaming-token capture, and OpenAI/Anthropic SDK instrumentation.

## Out of scope for v1 (explicitly cut)
OpenTelemetry export, hosted dashboard, team access controls, encrypted storage, and multi-run
statistical comparison.

## v1.1 priorities
1. Safe local persistence: opt-in recursive redaction for credentials and sensitive payloads.
2. Evidence of reliability: a reproducible benchmark reporting replay-hit rate, replay fidelity,
   storage overhead, and tracing latency on multi-step and streaming agents.
3. CI workflow example: use `agentrewind diff` as a regression gate against a saved baseline.

## Success metrics (v1, first 60 days after launch)
- North star: **10 weekly active external users** (proxied by PyPI downloads minus CI + issues/PRs from strangers).
- 100 GitHub stars; ≥3 issues filed by non-friends (signal of real usage).
- A demo GIF a hiring manager understands in 60 seconds.

## Launch plan
Show HN + r/LocalLLaMA + LinkedIn post, anchored on the demo: *seed a regression in an
example agent, catch it with `agentrewind diff` in one command.*

## Risks
- Crowded observability space → differentiate hard on replay+diff, local-first.
- Fingerprinting brittle if requests contain timestamps → document canonicalization hooks (v1.1).

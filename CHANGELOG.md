# Changelog

## Unreleased
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

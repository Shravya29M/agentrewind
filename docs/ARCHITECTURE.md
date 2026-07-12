# Architecture

AgentRewind is local-first: the core uses only the Python standard library and stores data in a
SQLite database. Optional FastAPI/Uvicorn dependencies power the browser viewer.

```text
agent code → trace/span SDK → in-memory span tree → SQLite trace store → CLI / web viewer / diff
                │
                └→ Recorder → canonical request fingerprint → SQLite replay cache
                                                       │
                                                       └→ offline deterministic replay
```

`Recorder` is provider-agnostic. The OpenAI and Anthropic adapters translate SDK-shaped calls
into plain request/response dictionaries, allowing the same fingerprinting, caching, tracing,
streaming capture, and diff logic to apply to either provider.

The trace diff walks span trees in execution order. It reports structural changes first, then
input, output, and status changes at each aligned span, making the earliest divergence the
natural debugging starting point.

For sensitive environments, `RedactionPolicy` is applied at persistence time. It leaves the
application's in-memory objects untouched while removing common credentials from SQLite traces
and cached responses.

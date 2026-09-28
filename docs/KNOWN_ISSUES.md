# Known issues

What is broken, what is a deliberate limitation, and what has been fixed. Only list issues
that have been reproduced or confirmed in the code, and give the steps to reproduce.

**Updating:** add new issues under **Open** with the next free ID. When an issue is fixed,
move it to **Resolved** with the commit and release that fixed it. Never renumber IDs.

Last reviewed: 2026-09-27 (v0.3.0).

## Open

| ID | Severity | Since | Summary |
|---|---|---|---|
| KI-6 | Low | 0.3.0 | A case-insensitive ID lookup with non-ASCII characters can match on PostgreSQL but not on SQLite |
| KI-7 | Low | — | An editable install can fail to put `src/` on `sys.path` |
| KI-8 | Low | — | Test runs show third-party deprecation warnings |

### KI-6: a case-insensitive ID lookup with non-ASCII characters can match on PostgreSQL but not on SQLite
- **Status:** open, known difference between the backends
- **Cause:** prefix lookup compares with `lower()`. SQLite's `lower()` folds ASCII only
  (`lower('ÀBC')` returns `Àbc`), while PostgreSQL's folds all of Unicode.
- **Impact:** generated trace IDs are lowercase hex, so this only affects hand-assigned IDs
  that contain non-ASCII letters.
- **Fix options:** do case folding in Python before querying, or make lookups
  case-sensitive on both backends. Either changes behaviour, so it belongs in a minor release.

### KI-7: an editable install can fail to put `src/` on `sys.path`
- **Status:** open, environment issue (not a code bug)
- **Symptom:** after `pip install -e .`, `import agentrewind` raises `ModuleNotFoundError`.
- **Cause:** when the repo is in an iCloud-synced folder, macOS marks dot-directories hidden,
  and Python 3.13+ skips hidden `.pth` files.
- **Workaround:** `PYTHONPATH=src pytest`, or keep the virtualenv outside the synced folder.
  CI is not affected.

### KI-8: test runs show third-party deprecation warnings
- **Status:** open, waiting on upstream
- **Symptom:** 1–2 warnings per run from starlette's `TestClient` (the httpx and anyio
  `BlockingPortal` deprecations).
- **Impact:** none. `pyproject.toml` turns `DeprecationWarning`s raised from `agentrewind.*`
  into errors, so our own deprecations still fail the build.

## Deliberate limitations

These are working as intended, and they are documented for users.

| Limitation | Where documented |
|---|---|
| The 0.2.x → 0.3.0 SQLite migration is one-way: 0.2.x cannot write to a migrated file | CHANGELOG, README, v0.3.0 release notes |
| On one machine, PostgreSQL wrote slower than SQLite in the benchmark (0.45× at 32 writers); the backend is for sharing traces, not speed | `docs/EVALUATION.md` |
| `configure()` does not close the store it replaces, because a `Recorder` may still hold it | `sdk.configure` |
| `spans.trace_id` has no enforced foreign key on either backend, to match SQLite's default | `docs/ARCHITECTURE.md` |

## Resolved

| ID | Summary | Fixed in | Release |
|---|---|---|---|
| KI-5 | An in-memory SQLite store (`SQLiteStore(":memory:")`) only worked on the thread that created it; other threads, including the web viewer's, got `no such table: traces`. Present since 0.1.0. In-memory stores now share one locked connection | `6dc0657` | unreleased (after 0.3.0) |
| KI-1 | Several processes opening the same new or 0.2.x SQLite file at once could fail with `database is locked`. Switching to WAL mode ignores the busy timeout. CI caught it; it reproduced locally in 29 of 240 simultaneous opens | `c0b2577` | 0.3.0 |
| KI-2 | `agentrewind serve` without the server extra suggested `pip install 'agentrewind[server]'`, the wrong package name | `f90292f` | 0.3.0 |
| KI-3 | The migration fixture `tests/fixtures/traces_v0_2_2.db` matched the `*.db` gitignore rule and would never have been committed | `f90292f` | 0.3.0 |
| KI-4 | A CLI test leaked a SQLite connection by calling `--db` twice in one process (affected tests only) | `f90292f` | 0.3.0 |

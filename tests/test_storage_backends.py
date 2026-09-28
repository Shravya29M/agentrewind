"""Storage-backend contract: identical ordering, lookup and round-trip behaviour on SQLite
and PostgreSQL, the 0.2.x → 0.3 SQLite migration, and multi-process write safety."""

import multiprocessing
import shutil
import sqlite3
import threading
from pathlib import Path

import pytest

import agentrewind as al
from agentrewind import postgres, sdk, store
from agentrewind.diff import diff_traces
from agentrewind.models import Span, SpanKind, Status, Trace
from agentrewind.store import DB_URL_ENV, SQLiteStore, open_store

FIXTURE_V022 = Path(__file__).parent / "fixtures" / "traces_v0_2_2.db"


def tied_trace(trace_id="tied0000", n=6, t=1000.0):
    """n spans sharing one started_at, with span_ids in *reverse* lexical order so neither
    timestamp nor id order can masquerade as execution order."""
    trace = Trace(name="tied", trace_id=trace_id, started_at=t, ended_at=t + 1, status=Status.OK)
    trace.spans = [
        Span(
            trace_id=trace_id,
            span_id=f"{trace_id}-{chr(ord('z') - i)}",
            name=f"step{i}",
            kind=SpanKind.TOOL,
            started_at=t,
            ended_at=t,
            status=Status.OK,
            input={"i": i},
        )
        for i in range(n)
    ]
    return trace


# --------------------------------------------------------------------------
# ordering
# --------------------------------------------------------------------------


def test_spans_with_identical_timestamps_keep_execution_order(fresh_store):
    trace = tied_trace()
    fresh_store.save_trace(trace)
    loaded = fresh_store.get_trace(trace.trace_id)
    assert [s.name for s in loaded.spans] == [f"step{i}" for i in range(6)]


def test_tied_spans_diff_clean_against_themselves_after_a_round_trip(fresh_store):
    fresh_store.save_trace(tied_trace("left0000"))
    fresh_store.save_trace(tied_trace("right000"))
    left, right = fresh_store.get_trace("left0000"), fresh_store.get_trace("right000")
    assert diff_traces(left, right) == []


def test_a_resave_reassigns_seq_from_the_new_span_list(fresh_store):
    trace = tied_trace(n=3)
    fresh_store.save_trace(trace)
    trace.spans.reverse()
    fresh_store.save_trace(trace)
    loaded = fresh_store.get_trace(trace.trace_id)
    assert [s.name for s in loaded.spans] == ["step2", "step1", "step0"]


def test_timestamp_still_orders_before_seq(fresh_store):
    trace = tied_trace(n=2)
    trace.spans[0].started_at = 2000.0  # appended first but started later
    fresh_store.save_trace(trace)
    assert [s.name for s in fresh_store.get_trace(trace.trace_id).spans] == ["step1", "step0"]


def test_list_traces_breaks_start_time_ties_by_id(fresh_store):
    for tid in ["cccc", "aaaa", "bbbb"]:
        fresh_store.save_trace(Trace(name=tid, trace_id=tid, started_at=5.0, status=Status.OK))
    fresh_store.save_trace(Trace(name="new", trace_id="zzzz", started_at=9.0, status=Status.OK))
    assert [t.trace_id for t in fresh_store.list_traces(10)] == ["zzzz", "aaaa", "bbbb", "cccc"]


# --------------------------------------------------------------------------
# lookup and round trip
# --------------------------------------------------------------------------


def _save_ids(store_, *ids):
    for tid in ids:
        store_.save_trace(Trace(name=tid, trace_id=tid, started_at=1.0, status=Status.OK))


def test_exact_id_beats_a_longer_id_sharing_the_prefix(fresh_store):
    _save_ids(fresh_store, "abc123", "abc")
    assert fresh_store.get_trace("abc").trace_id == "abc"


def test_ambiguous_prefix_resolves_to_the_lowest_id(fresh_store):
    _save_ids(fresh_store, "ab9", "ab1", "ab5")
    assert fresh_store.get_trace("ab").trace_id == "ab1"


def test_prefix_lookup_ignores_case(fresh_store):
    _save_ids(fresh_store, "deadbeef00")
    assert fresh_store.get_trace("DEADbeef").trace_id == "deadbeef00"


def test_like_wildcards_in_a_lookup_are_literal(fresh_store):
    _save_ids(fresh_store, "abcdef")
    assert fresh_store.get_trace("%") is None
    assert fresh_store.get_trace("a_c") is None


def test_payloads_round_trip_byte_identically(fresh_store):
    trace = Trace(
        name="rt",
        trace_id="rt000000",
        started_at=1727000000.123456789,
        ended_at=0.1 + 0.2,
        status=Status.OK,
        metadata={"z": 1, "a": {"y": [3, 2, 1], "b": None}, "ünï": "✓"},
    )
    trace.spans = [
        Span(trace_id="rt000000", name="s", started_at=1e-9, input={"b": 1, "a": 2}, output="x")
    ]
    fresh_store.save_trace(trace)
    loaded = fresh_store.get_trace("rt000000")
    assert loaded.started_at == trace.started_at and loaded.ended_at == trace.ended_at
    assert list(loaded.metadata) == ["z", "a", "ünï"]
    assert list(loaded.metadata["a"]) == ["y", "b"]
    assert list(loaded.spans[0].input) == ["b", "a"]
    assert loaded.spans[0].started_at == 1e-9
    assert fresh_store.export_trace("rt000000")["trace"]["metadata"] == trace.metadata


def test_cache_put_overwrites_an_existing_fingerprint(fresh_store):
    fresh_store.cache_put("fp", {"q": 1}, {"a": "old"})
    fresh_store.cache_put("fp", {"q": 1}, {"a": "new"})
    assert fresh_store.cache_get("fp") == {"a": "new"}
    assert fresh_store.cache_get("missing") is None


# --------------------------------------------------------------------------
# SQLite migration from 0.2.2
# --------------------------------------------------------------------------


@pytest.fixture
def legacy_db(tmp_path):
    """A copy of a traces.db written by the published llm-run-recorder 0.2.2 wheel."""
    dest = tmp_path / "legacy.db"
    shutil.copy(FIXTURE_V022, dest)
    assert "seq" not in _span_columns(dest), "fixture must predate the seq column"
    return dest


def _span_columns(path):
    conn = sqlite3.connect(path)
    try:
        return [c[1] for c in conn.execute("PRAGMA table_info(spans)")]
    finally:
        conn.close()


def test_opening_a_0_2_2_database_adds_seq_and_keeps_every_trace(legacy_db):
    s = SQLiteStore(legacy_db)
    assert _span_columns(legacy_db)[-1] == "seq"
    [listed] = s.list_traces()
    assert listed.trace_id == "legacy0a1b2c3d4e5" and listed.metadata == {"release": "0.2.2"}
    trace = s.get_trace("legacy0a")
    # NULL seq falls back to (started_at, span_id): the tie resolves aaa-tie before zzz-tie.
    assert [sp.span_id for sp in trace.spans] == ["root000000000000", "aaa-tie", "zzz-tie"]
    assert trace.spans[1].input == {"messages": [{"role": "user", "content": "hi"}]}
    assert trace.spans[2].output == ["r1"]
    assert s.cache_get("fp-legacy") == {"content": "cached"}
    s.close()


def test_migration_is_idempotent(legacy_db):
    SQLiteStore(legacy_db).close()
    s = SQLiteStore(legacy_db)
    assert _span_columns(legacy_db).count("seq") == 1
    assert len(s.get_trace("legacy0a1b2c3d4e5").spans) == 3
    s.close()


def test_migrated_database_accepts_new_traces_and_resaves(legacy_db):
    s = SQLiteStore(legacy_db)
    s.save_trace(tied_trace())
    assert [sp.name for sp in s.get_trace("tied0000").spans] == [f"step{i}" for i in range(6)]
    legacy = s.get_trace("legacy0a1b2c3d4e5")
    s.save_trace(legacy)  # rewrite assigns seq in the order it was read back
    assert [sp.span_id for sp in s.get_trace("legacy0a1b2c3d4e5").spans] == [
        "root000000000000",
        "aaa-tie",
        "zzz-tie",
    ]
    assert diff_traces(legacy, s.get_trace("legacy0a1b2c3d4e5")) == []
    s.close()


def test_a_failed_migration_rolls_back(legacy_db, monkeypatch):
    conn = sqlite3.connect(legacy_db)
    real_execute = conn.execute

    calls = []

    def execute(sql, *args):
        calls.append(sql)
        if sql.startswith("ALTER TABLE"):
            raise sqlite3.OperationalError("disk I/O error")
        return real_execute(sql, *args)

    class Proxy:  # sqlite3.Connection.execute is read-only, so wrap the connection
        def __getattr__(self, name):
            return getattr(conn, name)

    proxy = Proxy()
    proxy.execute = execute
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        SQLiteStore._migrate(proxy)
    assert calls[-1] == "ROLLBACK"
    assert not conn.in_transaction
    conn.close()
    assert "seq" not in _span_columns(legacy_db)


def _open_and_count(path, barrier, n_traces_seen):
    barrier.wait()
    s = SQLiteStore(path)
    n_traces_seen.put(len(s.list_traces()))
    s.close()


@pytest.mark.parametrize("trial", range(3))
def test_concurrent_processes_can_migrate_the_same_file(legacy_db, trial):
    # The fixture is in rollback-journal mode, so every opener races to switch it to WAL
    # and to add seq. A barrier lines the processes up to maximise the contention.
    n = 12
    ctx = multiprocessing.get_context("spawn")
    barrier, seen = ctx.Barrier(n), ctx.Queue()
    procs = [
        ctx.Process(target=_open_and_count, args=(legacy_db, barrier, seen)) for _ in range(n)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    assert [p.exitcode for p in procs] == [0] * n
    assert sorted(seen.get(timeout=5) for _ in procs) == [1] * n
    assert _span_columns(legacy_db).count("seq") == 1


class _FlakyWal:
    """Connection stand-in whose WAL switch fails with the given errors, then succeeds."""

    def __init__(self, *errors):
        self.errors, self.attempts = list(errors), 0

    def execute(self, sql):
        assert sql == "PRAGMA journal_mode=WAL"
        self.attempts += 1
        if self.errors:
            raise self.errors.pop(0)


def test_wal_switch_retries_while_the_file_is_locked(tmp_path):
    s = SQLiteStore(tmp_path / "wal.db")
    locked = sqlite3.OperationalError("database is locked")
    conn = _FlakyWal(locked, locked)
    s._enable_wal(conn)
    assert conn.attempts == 3
    s.close()


def test_wal_switch_does_not_retry_other_errors(tmp_path):
    s = SQLiteStore(tmp_path / "wal.db")
    conn = _FlakyWal(sqlite3.OperationalError("disk I/O error"))
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        s._enable_wal(conn)
    assert conn.attempts == 1
    s.close()


def test_wal_switch_gives_up_after_the_busy_timeout(tmp_path, monkeypatch):
    s = SQLiteStore(tmp_path / "wal.db")
    monkeypatch.setattr(s, "BUSY_TIMEOUT", 0.0)
    conn = _FlakyWal(*[sqlite3.OperationalError("database is locked")] * 5)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        s._enable_wal(conn)
    assert conn.attempts == 1
    s.close()


# --------------------------------------------------------------------------
# multi-process writers (both backends)
# --------------------------------------------------------------------------

N_PROCS = 8
TRACES_PER_PROC = 25


def _write_traces(target, worker, count):
    s = open_store(target)
    for i in range(count):
        with_spans = tied_trace(trace_id=f"w{worker:02d}-{i:04d}", n=4, t=float(i))
        with_spans.metadata = {"worker": worker, "i": i}
        s.save_trace(with_spans)
    s.close()


def _run(ctx, target, jobs):
    procs = [ctx.Process(target=target, args=args) for args in jobs]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
    return [p.exitcode for p in procs]


def test_many_writer_processes_lose_and_corrupt_nothing(make_target):
    target = make_target("shared")
    open_store(target).close()
    ctx = multiprocessing.get_context("spawn")
    codes = _run(ctx, _write_traces, [(target, w, TRACES_PER_PROC) for w in range(N_PROCS)])
    assert codes == [0] * N_PROCS

    s = open_store(target)
    traces = s.list_traces(limit=10_000)
    assert len(traces) == N_PROCS * TRACES_PER_PROC
    for t in traces:
        full = s.get_trace(t.trace_id)
        worker, i = full.metadata["worker"], full.metadata["i"]
        assert full.trace_id == f"w{worker:02d}-{i:04d}"
        assert [sp.name for sp in full.spans] == ["step0", "step1", "step2", "step3"]
        assert {sp.trace_id for sp in full.spans} == {full.trace_id}
    s.close()


def _overwrite_same_trace(target, worker, rounds):
    s = open_store(target)
    for r in range(rounds):
        trace = tied_trace(trace_id="contended", n=5)
        for sp in trace.spans:
            sp.output = {"worker": worker, "round": r}
        trace.metadata = {"worker": worker, "round": r}
        s.save_trace(trace)
    s.close()


def test_concurrent_saves_of_one_trace_leave_one_consistent_version(make_target):
    target = make_target("contended")
    open_store(target).close()
    ctx = multiprocessing.get_context("spawn")
    codes = _run(ctx, _overwrite_same_trace, [(target, w, 20) for w in range(N_PROCS)])
    assert codes == [0] * N_PROCS

    s = open_store(target)
    trace = s.get_trace("contended")
    assert len(trace.spans) == 5  # never a mix of two writers' span sets
    assert {tuple(sp.output.items()) for sp in trace.spans} == {tuple(trace.metadata.items())}
    s.close()


def _open_only(target):
    open_store(target).close()


def test_concurrent_first_opens_create_the_schema_once(make_target):
    target = make_target("fresh")
    ctx = multiprocessing.get_context("spawn")
    assert _run(ctx, _open_only, [(target,)] * N_PROCS) == [0] * N_PROCS
    s = open_store(target)
    assert s.list_traces() == []
    s.close()


# --------------------------------------------------------------------------
# backend selection
# --------------------------------------------------------------------------


def test_open_store_defaults_to_sqlite(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DEFAULT_DB", tmp_path / "default.db")
    s = open_store()
    assert isinstance(s, SQLiteStore) and s.path == tmp_path / "default.db"
    s.close()


def test_env_url_selects_the_store(monkeypatch, make_target, backend):
    target = make_target("from-env")
    monkeypatch.setenv(DB_URL_ENV, target)
    sdk._store = None
    s = al.get_store()
    assert type(s).__name__ == {"sqlite": "SQLiteStore", "postgres": "PostgresStore"}[backend]
    s.save_trace(tied_trace(n=1))
    reopened = open_store(target)
    assert reopened.get_trace("tied0000") is not None
    reopened.close()


def test_explicit_db_path_beats_the_env_url(monkeypatch, tmp_path):
    monkeypatch.setenv(DB_URL_ENV, "postgresql://nobody@127.0.0.1:1/never")
    s = al.configure(db_path=str(tmp_path / "explicit.db"))
    assert isinstance(s, SQLiteStore)


def test_trace_store_name_still_means_sqlite():
    assert al.TraceStore is SQLiteStore


def test_sqlite_repr_shows_the_path(tmp_path):
    s = SQLiteStore(tmp_path / "r.db")
    assert repr(s) == f"SQLiteStore({str(tmp_path / 'r.db')!r})"
    s.close()


# --------------------------------------------------------------------------
# PostgreSQL specifics that need no server
# --------------------------------------------------------------------------


def test_missing_psycopg_names_the_extra(monkeypatch):
    monkeypatch.setattr(postgres, "psycopg", None)
    with pytest.raises(ImportError, match=r"llm-run-recorder\[postgres\]"):
        open_store("postgresql://localhost/x")


@pytest.mark.parametrize(
    "dsn, expected",
    [
        ("postgresql://u:s3cret@h:5432/db", "postgresql://u:***@h:5432/db"),
        ("postgres://u:p%40ss@h/db?sslmode=require", "postgres://u:***@h/db?sslmode=require"),
        ("postgresql://u@h/db", "postgresql://u@h/db"),
        ("host=h user=u password=s3cret dbname=d", "host=h user=u password=*** dbname=d"),
    ],
)
def test_dsn_passwords_are_redacted(dsn, expected):
    assert postgres.redact_dsn(dsn) == expected


def test_connection_errors_do_not_leak_the_password():
    with pytest.raises(postgres.psycopg.OperationalError) as exc:
        postgres.PostgresStore("postgresql://u:hunter2@127.0.0.1:1/db?connect_timeout=2")
    assert "hunter2" not in str(exc.value)
    assert "u:***@127.0.0.1:1" in str(exc.value)


# --------------------------------------------------------------------------
# PostgreSQL connection lifecycle (needs a server)
# --------------------------------------------------------------------------


@pytest.fixture
def pg_store(backend, fresh_store):
    if backend != "postgres":
        pytest.skip("PostgreSQL-only")
    return fresh_store


def test_postgres_repr_hides_the_password(pg_store):
    assert "***" in repr(pg_store) or "@" not in pg_store.dsn
    assert repr(pg_store).startswith("PostgresStore(")


def test_a_forked_child_opens_its_own_connection(pg_store):
    parent_conn = pg_store._conn()
    pg_store._local.pid = -1  # what a child process sees after fork()
    child_conn = pg_store._conn()
    assert child_conn is not parent_conn and not parent_conn.closed

    # close() must not touch connections owned by another process.
    pg_store._all_conns[0] = (-1, parent_conn)
    pg_store.close()
    assert child_conn.closed and not parent_conn.closed
    parent_conn.close()


# --------------------------------------------------------------------------
# in-memory SQLite across threads (KI-5)
# --------------------------------------------------------------------------


def test_an_in_memory_store_is_visible_from_other_threads():
    s = SQLiteStore(":memory:")
    s.save_trace(tied_trace(n=2))
    seen = {}

    def reader():
        seen["ids"] = [t.trace_id for t in s.list_traces()]
        seen["spans"] = len(s.get_trace("tied0000").spans)

    t = threading.Thread(target=reader)
    t.start()
    t.join()
    assert seen == {"ids": ["tied0000"], "spans": 2}
    s.close()


def test_threads_share_one_in_memory_store_safely():
    s = SQLiteStore(":memory:")
    errors = []

    def worker(w):
        try:
            for i in range(25):
                s.save_trace(tied_trace(trace_id=f"m{w}-{i:02d}", n=3, t=float(i)))
                s.cache_put(f"fp{w}-{i}", {"w": w}, {"i": i})
                assert len(s.get_trace(f"m{w}-{i:02d}").spans) == 3
                assert s.cache_get(f"fp{w}-{i}") == {"i": i}
        except Exception as exc:  # surfaced below; a thread's exception is otherwise lost
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    traces = s.list_traces(limit=1000)
    assert len(traces) == 200
    for t in traces:
        assert [sp.name for sp in s.get_trace(t.trace_id).spans] == ["step0", "step1", "step2"]
    s.close()


def test_the_viewer_serves_an_in_memory_store():
    from fastapi.testclient import TestClient

    from agentrewind.server import create_app

    s = SQLiteStore(":memory:")
    al.configure(store=s)
    s.save_trace(tied_trace(n=2))
    with TestClient(create_app()) as client:  # handlers run on a worker thread
        assert [t["trace_id"] for t in client.get("/api/traces").json()] == ["tied0000"]
        assert client.get("/api/traces/tied0000").status_code == 200
    s.close()

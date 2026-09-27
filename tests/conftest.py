"""Every test runs once per storage backend: SQLite always, PostgreSQL when configured.

PostgreSQL tests run when AGENTREWIND_DB_URL points at a server (``docker compose up -d``
provides one) and are skipped otherwise, so a plain ``pytest`` needs no Docker. CI sets
AGENTREWIND_REQUIRE_POSTGRES=1, which turns that skip into a failure.

Each PostgreSQL test gets a throwaway schema, selected through the URL's search_path.
"""

import os
import uuid

import pytest

import agentrewind
from agentrewind import sdk
from agentrewind.store import DB_URL_ENV, open_store

PG_URL = os.environ.get(DB_URL_ENV) or None
REQUIRE_PG = os.environ.get("AGENTREWIND_REQUIRE_POSTGRES") == "1"


def pg_url_for_schema(schema: str) -> str:
    sep = "&" if "?" in PG_URL else "?"
    return f"{PG_URL}{sep}options=-csearch_path%3D{schema}"


@pytest.fixture(scope="session")
def pg_admin():
    import psycopg

    conn = psycopg.connect(PG_URL, autocommit=True)
    conn.execute("SET lock_timeout = '10s'")
    yield conn
    conn.close()


@pytest.fixture(params=["sqlite", "postgres"])
def backend(request):
    if request.param == "postgres" and PG_URL is None:
        if REQUIRE_PG:
            pytest.fail(f"AGENTREWIND_REQUIRE_POSTGRES=1 but {DB_URL_ENV} is not set")
        pytest.skip(f"set {DB_URL_ENV} (see docker-compose.yml) to run PostgreSQL tests")
    return request.param


@pytest.fixture
def make_target(backend, tmp_path, request):
    """Return a fresh, empty database target (SQLite path or PostgreSQL URL) per call."""
    schemas: list[str] = []

    def factory(name: str = "traces") -> str:
        if backend == "sqlite":
            return str(tmp_path / f"{name}.db")
        schema = f"ar_test_{uuid.uuid4().hex[:12]}"
        request.getfixturevalue("pg_admin").execute(f"CREATE SCHEMA {schema}")
        schemas.append(schema)
        return pg_url_for_schema(schema)

    yield factory
    if schemas:
        admin = request.getfixturevalue("pg_admin")
        for schema in schemas:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def make_store(make_target):
    """Open a store on a fresh target of the current backend."""
    stores = []

    def factory(name: str = "traces", **kwargs):
        store = open_store(make_target(name), **kwargs)
        stores.append(store)
        return store

    yield factory
    for store in stores:
        store.close()


@pytest.fixture(autouse=True)
def fresh_store(make_store, monkeypatch):
    """Point the global store at a per-test empty database of the current backend."""
    # Tests decide for themselves whether the ambient URL applies.
    monkeypatch.delenv(DB_URL_ENV, raising=False)
    store = make_store()
    agentrewind.configure(store=store)
    yield store
    # CLI --db and configure(db_path=...) open stores the factory does not track.
    if sdk._store is not None and sdk._store is not store:
        sdk._store.close()
    sdk._store = None

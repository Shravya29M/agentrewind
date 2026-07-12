import pytest

import agentlens
from agentlens.store import TraceStore


@pytest.fixture(autouse=True)
def fresh_store(tmp_path):
    """Point the global store at a per-test temp db."""
    store = TraceStore(tmp_path / "traces.db")
    agentlens.configure(store=store)
    yield store

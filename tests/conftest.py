import pytest

import agentrewind
from agentrewind.store import TraceStore


@pytest.fixture(autouse=True)
def fresh_store(tmp_path):
    """Point the global store at a per-test temp db."""
    store = TraceStore(tmp_path / "traces.db")
    agentrewind.configure(store=store)
    yield store

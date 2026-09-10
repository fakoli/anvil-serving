import sqlite3

import pytest

from anvil_serving.observability.dashboard.contracts import ObservatoryError
from anvil_serving.workbench_app.store import PrivateStore


def test_retention_preserves_claims_and_request_tombstones(tmp_path):
    path = tmp_path / "state.sqlite"
    store = PrivateStore(path)
    for kind, key, body in [("task-binding", "claim", {"lease_id": "live"}), ("chat-request", "retry", {"id": "old"}), ("conversation", "finished", {"status": "completed"}), ("conversation", "running", {"status": "running"})]:
        store.put(kind, "operator", key, body)
    store.db.execute("UPDATE records SET updated=0")
    store.db.commit()
    store.close()
    store = PrivateStore(path)
    assert store.get("task-binding", "operator", "claim")["lease_id"] == "live"
    assert store.get("chat-request", "operator", "retry")["id"] == "old"
    assert store.get("conversation", "operator", "running")["status"] == "running"
    with pytest.raises(ObservatoryError):
        store.get("conversation", "operator", "finished")
    assert store.db.execute("PRAGMA max_page_count").fetchone()[0] * store.db.execute("PRAGMA page_size").fetchone()[0] <= 256 * 1024**2
    store.close()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM records").fetchone()[0] == 3

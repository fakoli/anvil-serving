"""Catalog changes preserve native file SecretRefs without resolving credentials."""
import json
from pathlib import Path

import pytest

from anvil_serving.client_catalog_sync import ClientCatalogError, sync_client_catalog_batch
from tests.test_client_catalog_sync import (
    _Opener, _catalog, _native_three_client_batch, _run, _write_inputs,
    isolated_home as isolated_home, tmp_path as tmp_path,
)


def _file_reference(path):
    document = json.loads(path.read_text())
    reference = {"source": "file", "provider": "native_router", "id": "value"}
    document["models"]["providers"]["anvil"]["apiKey"] = reference
    document["secrets"] = {"providers": {"native_router": {
        "source": "file", "path": str(path.parent / "protected-key"), "mode": "singleValue"}}}
    path.write_text(json.dumps(document))
    return reference, document["secrets"]


def test_file_secret_preview_apply_repeat_preserve_reference_and_skip_env(tmp_path, monkeypatch):
    path, _, _ = _write_inputs(tmp_path)
    reference, secrets = _file_reference(path)
    original = Path.read_bytes

    def read_without_credentials(candidate):
        assert candidate.name not in {"protected-key", ".env", "ai.openclaw.gateway.env"}
        return original(candidate)

    monkeypatch.setattr(Path, "read_bytes", read_without_credentials)
    restarts, refreshes = [], []
    options = dict(clients="openclaw", restart_on_change=True,
        restart=lambda: restarts.append(True) or 0, refresh=lambda: refreshes.append(True) or 0)
    assert _run(tmp_path, opener=_Opener(*_catalog()), **options)["dry_run"]
    assert _run(tmp_path, opener=_Opener(*_catalog()), confirm=True, dry_run=False, **options)["openclaw_restarted"]
    assert _run(tmp_path, opener=_Opener(*_catalog()), confirm=True, dry_run=False, **options)["changed"] == []
    observed = json.loads(path.read_text())
    assert observed["models"]["providers"]["anvil"]["apiKey"] == reference
    assert observed["secrets"] == secrets
    assert restarts == [True] and refreshes == []
    assert not (tmp_path / ".env").exists()


def test_fenced_batch_file_reference_keeps_credentials_out_of_effects(tmp_path, monkeypatch):
    options, targets, _, _ = _native_three_client_batch(tmp_path, monkeypatch)
    path = Path(targets[2].openclaw_config)
    reference, secrets = _file_reference(path)
    assert all(row["result"]["dry_run"] for row in sync_client_catalog_batch(**options))
    sync_client_catalog_batch(**options, dry_run=False)
    assert all(row["result"]["changed"] == [] for row in sync_client_catalog_batch(**options))
    observed = json.loads(path.read_text())
    assert observed["models"]["providers"]["anvil"]["apiKey"] == reference
    assert observed["secrets"] == secrets
    assert not (path.parent / ".env").exists()


def test_file_reference_requires_a_declared_file_provider(tmp_path):
    path, _, _ = _write_inputs(tmp_path)
    _file_reference(path)
    document = json.loads(path.read_text()); document["secrets"] = {"providers": {}}
    path.write_text(json.dumps(document))
    with pytest.raises(ClientCatalogError, match="declared file SecretRef"):
        _run(tmp_path, clients="openclaw", opener=_Opener(*_catalog()))

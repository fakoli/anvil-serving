"""Curated Hindsight import stays explicit, idempotent, and source-safe."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import uuid
import urllib.parse

import pytest

from anvil_serving import cli, memory_import


def _config(tmp_path: Path, source: Path, files: list[str] | None = None) -> Path:
    path = tmp_path / "memory-import.toml"
    paths = files or ["MEMORY.md"]
    rendered = ", ".join(json.dumps(item) for item in paths)
    path.write_text(
        "schema_version = 1\n"
        "base_url = \"http://127.0.0.1:8888\"\n"
        "bank = \"migration\"\n"
        "auth_file = %s\n"
        "[[sources]]\n"
        "id = \"codex-primary\"\n"
        "harness = \"codex\"\n"
        "root = %s\n"
        "files = [%s]\n" % (
            json.dumps(str(tmp_path / "protected-token")),
            json.dumps(str(source)),
            rendered,
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def source_root(tmp_path):
    if os.name != "nt":
        root = tmp_path / "source"
        root.mkdir()
        yield root
        return
    root = Path.cwd() / ("memory-import-source-" + uuid.uuid4().hex)
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _defense_transport(documents, calls):
    def transport(method, url, token, payload=None):
        calls.append((method, url, payload))
        if url.endswith("/config"):
            return 200, {"config": {"memory_defense": memory_import._DEFENSE}}
        if method == "GET":
            document_id = urllib.parse.unquote(url.rsplit("/", 1)[-1])
            return (200, documents[document_id]) if document_id in documents else (404, {})
        if method == "POST":
            item = payload["items"][0]
            documents[item["document_id"]] = {
                "document_metadata": item["metadata"],
                "content_hash": memory_import.hashlib.sha256(item["content"].encode()).hexdigest(),
            }
            return 200, {"success": True}
        raise AssertionError((method, url))
    return transport


def test_preview_is_offline_and_deterministic(tmp_path, source_root, monkeypatch):
    source = source_root
    (source / "MEMORY.md").write_text("alpha\n\nbeta\n", encoding="utf-8")
    config = _config(tmp_path, source)
    monkeypatch.setattr(memory_import, "read_private_auth_file", lambda *_args, **_kwargs: pytest.fail("credential read"))
    first = memory_import.import_memories(config, transport=lambda *_args: pytest.fail("network"))
    second = memory_import.import_memories(config, transport=lambda *_args: pytest.fail("network"))
    command = memory_import.dispatch(["--config", str(config)])
    assert first == second
    assert command.error is None and command.data == first
    assert first["apply"] is False and first["chunks"] == 1
    assert "alpha" not in json.dumps(first)


@pytest.mark.parametrize("character", ["\x01", "\x7f"])
def test_rejects_upstream_stripped_characters_before_any_write(tmp_path, source_root, character):
    source = source_root
    (source / "MEMORY.md").write_text("a" + character + "b", encoding="utf-8")
    config = _config(tmp_path, source)
    with pytest.raises(memory_import.MemoryImportError, match="control characters"):
        memory_import.import_memories(config, confirm=True,
                                      transport=lambda *_args: pytest.fail("network"))


def test_rejects_traversal_and_symlink_sources(tmp_path, source_root):
    source = source_root
    (source / "MEMORY.md").write_text("safe", encoding="utf-8")
    with pytest.raises(memory_import.MemoryImportError, match="unsafe"):
        memory_import.load_config(_config(tmp_path, source, ["../MEMORY.md"]))
    config = _config(tmp_path, source)
    unsafe_root = source / ".." / source.name
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            json.dumps(str(source)), json.dumps(str(unsafe_root))
        ),
        encoding="utf-8",
    )
    with pytest.raises(memory_import.MemoryImportError, match="identity"):
        memory_import.load_config(config)
    try:
        (source / "linked.md").symlink_to(source / "MEMORY.md")
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    config = _config(tmp_path, source, ["linked.md"])
    with pytest.raises(memory_import.MemoryImportError, match="unsafe"):
        memory_import.import_memories(config)


def test_rejects_bad_config_types_and_credential_newlines(tmp_path, source_root, monkeypatch):
    source = source_root
    (source / "MEMORY.md").write_text("safe", encoding="utf-8")
    config = _config(tmp_path, source)
    config.write_text(config.read_text(encoding="utf-8").replace("schema_version = 1", "schema_version = true"), encoding="utf-8")
    with pytest.raises(memory_import.MemoryImportError, match="schema version"):
        memory_import.load_config(config)
    config = _config(tmp_path, source)
    config.write_text(config.read_text(encoding="utf-8").replace('harness = "codex"', "harness = []"), encoding="utf-8")
    with pytest.raises(memory_import.MemoryImportError, match="identity"):
        memory_import.load_config(config)
    config = _config(tmp_path, source)
    monkeypatch.setattr(memory_import, "read_private_auth_file", lambda *_args, **_kwargs: b"secret\n")
    with pytest.raises(memory_import.MemoryImportError, match="credential"):
        memory_import.import_memories(config, confirm=True)


def test_content_addressed_retry_skips_and_changed_source_versions(tmp_path, source_root, monkeypatch):
    source = source_root
    path = source / "MEMORY.md"
    path.write_text("first", encoding="utf-8")
    config = _config(tmp_path, source)
    monkeypatch.setattr(memory_import, "read_private_auth_file", lambda *_args, **_kwargs: b"secret")
    documents, calls = {}, []
    transport = _defense_transport(documents, calls)
    first = memory_import.import_memories(config, confirm=True, transport=transport)
    second = memory_import.import_memories(config, confirm=True, transport=transport)
    first_id = next(iter(documents))
    path.write_text("second", encoding="utf-8")
    third = memory_import.import_memories(config, confirm=True, transport=transport)
    assert (first["imported"], second["skipped"], third["imported"]) == (1, 1, 1)
    assert len(documents) == 2 and first_id in documents
    posted = [payload for method, _, payload in calls if method == "POST"]
    assert posted and all(isinstance(value, str) for value in posted[0]["items"][0]["metadata"].values())


def test_defense_refusal_happens_before_any_write(tmp_path, source_root, monkeypatch):
    source = source_root
    (source / "MEMORY.md").write_text("safe", encoding="utf-8")
    monkeypatch.setattr(memory_import, "read_private_auth_file", lambda *_args, **_kwargs: b"secret")
    calls = []
    def transport(method, url, token, payload=None):
        calls.append(method)
        return 200, {"config": {"memory_defense": {"enabled": False}}}
    with pytest.raises(memory_import.MemoryImportError, match="block_sensitive_data"):
        memory_import.import_memories(_config(tmp_path, source), confirm=True, transport=transport)
    assert calls == ["GET"]


def test_upstream_rejection_is_safe_and_never_changes_source(tmp_path, source_root, monkeypatch):
    source = source_root
    original = "private source content"
    path = source / "MEMORY.md"
    path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(memory_import, "read_private_auth_file", lambda *_args, **_kwargs: b"secret")
    def transport(method, url, token, payload=None):
        if url.endswith("/config"):
            return 200, {"config": {"memory_defense": memory_import._DEFENSE}}
        if method == "GET":
            return 404, {}
        return 422, {"detail": "rejected"}
    report = memory_import.import_memories(_config(tmp_path, source), confirm=True, transport=transport)
    assert report["failed_chunks"] and original not in json.dumps(report)
    assert path.read_text(encoding="utf-8") == original


def test_transport_failure_stops_before_the_next_chunk(tmp_path, source_root, monkeypatch):
    source = source_root
    (source / "MEMORY.md").write_text("x" * (memory_import.MAX_CHUNK_CHARS + 1), encoding="utf-8")
    monkeypatch.setattr(memory_import, "read_private_auth_file", lambda *_args, **_kwargs: b"secret")
    post_ids = []

    def transport(method, url, token, payload=None):
        if url.endswith("/config"):
            return 200, {"config": {"memory_defense": memory_import._DEFENSE}}
        if method == "GET":
            return 404, {}
        post_ids.append(payload["items"][0]["document_id"])
        raise memory_import.MemoryImportError("Hindsight request failed")

    report = memory_import.import_memories(_config(tmp_path, source), confirm=True, transport=transport)
    assert len(post_ids) == 1
    assert report["imported"] == 0 and report["failed_chunks"] == post_ids


def test_partial_import_is_a_nonzero_cli_result(monkeypatch):
    monkeypatch.setattr(memory_import, "import_memories", lambda *_args, **_kwargs: {"failed_chunks": ["safe-id"]})
    result = memory_import.dispatch(["--config", "/safe/config.toml"])
    assert result.error is not None and result.exit_code != 0
    assert result.data == {"failed_chunks": ["safe-id"]}


def test_canonical_cli_dispatches_preview_and_partial_failure(tmp_path, source_root, monkeypatch, capsys):
    source = source_root
    (source / "MEMORY.md").write_text("safe", encoding="utf-8")
    config = _config(tmp_path, source)
    monkeypatch.setattr(
        memory_import,
        "read_private_auth_file",
        lambda *_args, **_kwargs: pytest.fail("preview read credential"),
    )

    assert cli.main(["harness", "memory-import", "--config", str(config)]) == 0
    assert '"apply": false' in capsys.readouterr().out

    monkeypatch.setattr(
        memory_import,
        "import_memories",
        lambda *_args, **_kwargs: {"failed_chunks": ["safe-id"]},
    )
    assert cli.main(["harness", "memory-import", "--config", str(config)]) != 0

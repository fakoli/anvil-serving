"""Curated Hindsight import stays explicit, idempotent, and source-safe."""
from __future__ import annotations

import json
import io
import os
from pathlib import Path
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
    from tests.bootstrap_windows_fixtures import windows_fixture_tree

    with windows_fixture_tree() as tree:
        yield tree.root


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
                "tags": item["tags"],
            }
            return 200, {"success": True}
        if method == "PATCH":
            document_id = urllib.parse.unquote(url.rsplit("/", 1)[-1])
            documents[document_id]["tags"] = payload["tags"]
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
    completed = [payload for method, _, payload in calls if method == "PATCH"]
    assert posted and all(isinstance(value, str) for value in posted[0]["items"][0]["metadata"].values())
    assert completed and completed[0]["tags"][-1] == memory_import.COMPLETION_TAG


def test_default_transport_uses_retain_and_read_deadlines(tmp_path, source_root, monkeypatch):
    source = source_root
    (source / "MEMORY.md").write_text("safe", encoding="utf-8")
    monkeypatch.setattr(memory_import, "read_private_auth_file", lambda *_args, **_kwargs: b"secret")
    observed = []

    class Response:
        status = 200

        def __init__(self, body):
            self._body = json.dumps(body).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return self._body

    class Opener:
        def open(self, request, *, timeout):
            observed.append((request.get_method(), timeout))
            if request.get_method() == "GET" and request.full_url.endswith("/config"):
                return Response({"config": {"memory_defense": memory_import._DEFENSE}})
            if request.get_method() == "GET":
                response = Response({})
                response.status = 404
                return response
            return Response({"success": True})

    monkeypatch.setattr(memory_import.urllib.request, "build_opener", lambda *_args: Opener())
    report = memory_import.import_memories(_config(tmp_path, source), confirm=True)

    assert report["imported"] == 1
    assert observed == [
        ("GET", memory_import.READ_REQUEST_TIMEOUT_SECONDS),
        ("GET", memory_import.READ_REQUEST_TIMEOUT_SECONDS),
        ("POST", memory_import.RETAIN_REQUEST_TIMEOUT_SECONDS),
        ("PATCH", memory_import.READ_REQUEST_TIMEOUT_SECONDS),
    ]


def test_unmarked_document_retries_after_timeout_then_completed_rerun_skips(tmp_path, source_root, monkeypatch):
    source = source_root
    (source / "MEMORY.md").write_text("safe", encoding="utf-8")
    config = _config(tmp_path, source)
    monkeypatch.setattr(memory_import, "read_private_auth_file", lambda *_args, **_kwargs: b"secret")
    chunk = memory_import.plan_import(memory_import.load_config(config))[0]
    documents = {chunk.document_id: {
        "document_metadata": memory_import._metadata(chunk),
        "content_hash": chunk.content_sha256,
        "tags": memory_import._tags(chunk, complete=False),
    }}
    attempts = {"post": 0, "patch": 0}
    post_document_ids = []

    def transport(method, url, token, payload=None):
        if url.endswith("/config"):
            return 200, {"config": {"memory_defense": memory_import._DEFENSE}}
        if method == "GET":
            document_id = urllib.parse.unquote(url.rsplit("/", 1)[-1])
            return (200, documents[document_id]) if document_id in documents else (404, {})
        if method == "POST":
            attempts["post"] += 1
            post_document_ids.append(payload["items"][0]["document_id"])
            if attempts["post"] == 1:
                raise memory_import.MemoryImportError("Hindsight request failed")
            item = payload["items"][0]
            documents[item["document_id"]] = {
                "document_metadata": item["metadata"],
                "content_hash": memory_import.hashlib.sha256(item["content"].encode()).hexdigest(),
                "tags": item["tags"],
            }
            return 200, {"success": True}
        if method == "PATCH":
            attempts["patch"] += 1
            document_id = urllib.parse.unquote(url.rsplit("/", 1)[-1])
            documents[document_id]["tags"] = payload["tags"]
            return 200, {"success": True}
        raise AssertionError((method, url))

    timed_out = memory_import.import_memories(config, confirm=True, transport=transport)
    assert attempts == {"post": 1, "patch": 0}
    recovered = memory_import.import_memories(config, confirm=True, transport=transport)
    rerun = memory_import.import_memories(config, confirm=True, transport=transport)

    assert timed_out["skipped"] == 0 and timed_out["failed_chunks"] == [chunk.document_id]
    assert timed_out["failed_chunk_metadata"] == [{"document_id": chunk.document_id, "http_status": None}]
    assert recovered["imported"] == 1 and recovered["failed_chunks"] == []
    assert rerun["skipped"] == 1 and rerun["imported"] == 0
    assert attempts == {"post": 2, "patch": 1}
    assert post_document_ids == [chunk.document_id, chunk.document_id]


def test_completion_patch_rejection_reports_retained_change_and_stops(tmp_path, source_root, monkeypatch):
    source = source_root
    (source / "MEMORY.md").write_text("x" * (memory_import.MAX_CHUNK_CHARS + 1), encoding="utf-8")
    config = _config(tmp_path, source)
    monkeypatch.setattr(memory_import, "read_private_auth_file", lambda *_args, **_kwargs: b"secret")
    private_payload = "private completion error"
    posted = []

    class Opener:
        def open(self, request, *_args, **_kwargs):
            assert request.get_method() == "PATCH"
            raise memory_import.urllib.error.HTTPError(
                request.full_url, 503, "rejected", {}, io.BytesIO(private_payload.encode())
            )

    monkeypatch.setattr(memory_import.urllib.request, "build_opener", lambda *_args: Opener())

    def transport(method, url, token, payload=None):
        if url.endswith("/config"):
            return 200, {"config": {"memory_defense": memory_import._DEFENSE}}
        if method == "GET":
            return 404, {}
        if method == "POST":
            posted.append(payload["items"][0]["document_id"])
            return 200, {"success": True}
        return memory_import._http(method, url, token, payload)

    report = memory_import.import_memories(config, confirm=True, transport=transport)

    assert report["changed"] is True and report["imported"] == 0
    assert report["failed_chunks"] == posted and len(posted) == 1
    assert report["failed_chunk_metadata"] == [{"document_id": posted[0], "http_status": 503}]
    assert private_payload not in json.dumps(report)


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


def test_http_rejection_reports_status_without_upstream_payload(tmp_path, source_root, monkeypatch):
    source = source_root
    private_payload = "private upstream error body"
    (source / "MEMORY.md").write_text("safe", encoding="utf-8")
    monkeypatch.setattr(memory_import, "read_private_auth_file", lambda *_args, **_kwargs: b"secret")

    class Opener:
        def open(self, *_args, **_kwargs):
            raise memory_import.urllib.error.HTTPError(
                "http://127.0.0.1:8888/retain", 422, "rejected", {}, io.BytesIO(private_payload.encode())
            )

    monkeypatch.setattr(memory_import.urllib.request, "build_opener", lambda *_args: Opener())

    def transport(method, url, token, payload=None):
        if url.endswith("/config"):
            return 200, {"config": {"memory_defense": memory_import._DEFENSE}}
        if method == "GET":
            return 404, {}
        return memory_import._http(method, url, token, payload)

    report = memory_import.import_memories(_config(tmp_path, source), confirm=True, transport=transport)
    assert report["failed_chunk_metadata"] == [{"document_id": report["failed_chunks"][0], "http_status": 422}]
    assert private_payload not in json.dumps(report)


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
    assert report["failed_chunk_metadata"] == [{"document_id": post_ids[0], "http_status": None}]


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

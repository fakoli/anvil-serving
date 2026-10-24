"""Trace custody rejects links, overflow, and overwrite; copies exact bytes."""
import json
import tempfile
from pathlib import Path

import pytest

from anvil_serving.recipe_trace import collect


def test_trace_collection():
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        root, out = base / "trace", base / "out"
        root.mkdir(); out.mkdir()
        (root / "run.sqlite").write_bytes(b"fixture bytes")
        assert collect(root)["total_bytes"] == 13
        result = collect(root, out)
        assert (out / "run.sqlite").read_bytes() == b"fixture bytes"
        assert len(result["files"][0]["sha256"]) == 64
        with pytest.raises(FileExistsError):
            collect(root, out)
        with pytest.raises(ValueError, match="byte bound"):
            collect(root, max_bytes=12)
        (root / "private.sqlite").symlink_to(base / "private")
        with pytest.raises(ValueError, match="non-regular"):
            collect(root)


@pytest.mark.parametrize("fault", [None, "remote", "mount", "driver", "running", "drift", "restart", "json", "shared"])
def test_trace_owner_contract(tmp_path, monkeypatch, fault):
    from types import SimpleNamespace
    import anvil_serving.recipe_trace as mod

    volume = tmp_path / "volume"; volume.mkdir()
    (volume / "run.sqlite").write_bytes(b"fixture")
    recipe = {"model": "example/model", "serve": {"named_volumes": ["trace:/diagnostics"]}}
    identity = {"container_id": "a" * 64, "image_id": "sha256:" + "b" * 64, "state": "exited", "running": False}
    if fault == "running":
        identity.update(state="running", running=True)
    monkeypatch.setattr(mod.serve_recipes.recipe_memory, "_docker_endpoint", lambda run: "ssh://remote" if fault == "remote" else "unix:///var/run/docker.sock")
    monkeypatch.setattr(mod.serve_recipes, "load_registry", lambda path: {"recipe": [recipe]})
    count = 0

    def owned(*args, **kwargs):
        nonlocal count
        assert kwargs["require_recipe_digest"] is True
        count += 1
        return identity | ({"container_id": "c" * 64} if fault == "drift" and count > 1 else {})

    monkeypatch.setattr(mod, "_recipe_container_identity", owned)

    lifecycle_reads = 0

    def run(argv, **kwargs):
        nonlocal lifecycle_reads
        assert kwargs.get("timeout") == 10
        if argv[1] == "ps":
            return SimpleNamespace(stdout="a" * 64 + ("\n" + "d" * 64 if fault == "shared" else "") + "\n")
        if fault == "json":
            return SimpleNamespace(stdout="{")
        if argv[1] == "volume":
            data = [{"Driver": "remote" if fault == "driver" else "local", "Options": None, "Mountpoint": str(volume)}]
        elif argv[3] == mod._LIFECYCLE_FORMAT:
            lifecycle_reads += 1
            data = {
                "State": {
                    "StartedAt": "2026-01-01T00:00:00Z",
                    "FinishedAt": "2026-01-01T00:01:00Z",
                    "ExitCode": 0,
                },
                "RestartCount": 1 if fault == "restart" and lifecycle_reads > 1 else 0,
            }
        else:
            data = [{"Type": "volume", "Name": "wrong" if fault == "mount" else "trace", "Destination": "/diagnostics"}]
        return SimpleNamespace(stdout=json.dumps(data))

    monkeypatch.setattr(mod.subprocess, "run", run)
    if fault:
        with pytest.raises((ValueError, json.JSONDecodeError)):
            mod.trace_artifacts("example/model", "candidate", tmp_path / "registry", tmp_path / "export")
    else:
        result = mod.trace_artifacts("example/model", "candidate", tmp_path / "registry", tmp_path / "export")
        assert result["files"][0]["exported"] is True


def test_trace_lifecycle_projection_uses_docker_inspect_shape():
    import anvil_serving.recipe_trace as mod

    # Docker exposes RestartCount beside, rather than inside, the State object.
    observed = {
        "State": {
            "Status": "exited",
            "Running": False,
            "StartedAt": "2026-01-01T00:00:00.000000000Z",
            "FinishedAt": "2026-01-01T00:01:00.000000000Z",
            "ExitCode": 0,
        },
        "RestartCount": 2,
    }
    assert mod._lifecycle_snapshot(observed) == {
        "StartedAt": "2026-01-01T00:00:00.000000000Z",
        "FinishedAt": "2026-01-01T00:01:00.000000000Z",
        "RestartCount": 2,
        "ExitCode": 0,
    }
    assert '"RestartCount":{{json .RestartCount}}' in mod._LIFECYCLE_FORMAT


def test_trace_collection_rejects_source_addition_or_rewrite(tmp_path, monkeypatch):
    import anvil_serving.recipe_trace as mod

    root, output = tmp_path / "trace", tmp_path / "out"
    root.mkdir(); output.mkdir()
    (root / "first.sqlite").write_bytes(b"first")
    (root / "later.sqlite").write_bytes(b"later")
    real_fsync = mod.os.fsync

    def mutate(fd):
        real_fsync(fd)
        if not (root / "added.sqlite").exists():
            (root / "added.sqlite").write_bytes(b"added")

    monkeypatch.setattr(mod.os, "fsync", mutate)
    with pytest.raises(ValueError, match="changed during collection"):
        mod.collect(root, output)

    (root / "added.sqlite").unlink()
    monkeypatch.setattr(mod.os, "fsync", real_fsync)
    output = tmp_path / "out-rewrite"
    output.mkdir()
    changed = False

    def rewrite(fd):
        nonlocal changed
        real_fsync(fd)
        if not changed:
            changed = True
            (root / "later.sqlite").write_bytes(b"rewritten")

    monkeypatch.setattr(mod.os, "fsync", rewrite)
    with pytest.raises(ValueError, match="trace changed"):
        mod.collect(root, output)


def test_trace_collection_rejects_hardlinks_and_output_substitution(tmp_path, monkeypatch):
    import anvil_serving.recipe_trace as mod

    root, output, moved, replacement = (tmp_path / name for name in ("trace", "out", "moved", "replacement"))
    root.mkdir(); output.mkdir(); replacement.mkdir()
    source = root / "run.sqlite"
    source.write_bytes(b"trace")
    (root / "same.sqlite").hardlink_to(source)
    with pytest.raises(ValueError, match="multiply linked"):
        mod.collect(root)
    (root / "same.sqlite").unlink()
    real_fsync = mod.os.fsync
    swapped = False

    def substitute(fd):
        nonlocal swapped
        real_fsync(fd)
        if not swapped:
            swapped = True
            output.rename(moved)
            output.symlink_to(replacement, target_is_directory=True)

    monkeypatch.setattr(mod.os, "fsync", substitute)
    with pytest.raises(ValueError, match="output directory changed"):
        mod.collect(root, output)


@pytest.mark.parametrize("attached", [False, True])
def test_retained_trace_has_no_writers(tmp_path, monkeypatch, attached):
    import json
    from types import SimpleNamespace
    import anvil_serving.recipe_trace as mod

    volume = tmp_path / "volume"; volume.mkdir(); (volume / "run.nsys-rep").write_bytes(b"trace")
    recipe = {"model": "example/model", "serve": {"named_volumes": ["trace:/diagnostics"]}}
    monkeypatch.setattr(mod.serve_recipes.recipe_memory, "_docker_endpoint", lambda run: "unix:///var/run/docker.sock")
    monkeypatch.setattr(mod.serve_recipes, "load_registry", lambda path: {"recipe": [recipe]})
    monkeypatch.setattr(mod, "_recipe_container_identity", lambda *a, **k: pytest.fail("removed container cannot establish identity"))

    def run(argv, **kwargs):
        if argv[1] == "ps":
            return SimpleNamespace(stdout="a" * 64 if attached else "")
        assert argv == ["docker", "volume", "inspect", "trace"]
        return SimpleNamespace(stdout=json.dumps([{"Driver": "local", "Options": None, "Mountpoint": str(volume), "CreatedAt": "fixed"}]))

    monkeypatch.setattr(mod.subprocess, "run", run)
    if attached:
        with pytest.raises(ValueError, match="exclusively"):
            mod.trace_artifacts("example/model", None, tmp_path / "recipe", tmp_path / "out", retained=True)
    else:
        result = mod.trace_artifacts("example/model", None, tmp_path / "recipe", tmp_path / "out", retained=True)
        assert result["retained_volume"] and result["identity"] is None and result["files"][0]["exported"]

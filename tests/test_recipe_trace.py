"""Trace custody rejects links, overflow, and overwrite; copies exact bytes."""
import json

import pytest


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

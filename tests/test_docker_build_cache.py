import json
import subprocess

import pytest

from anvil_serving import docker_build_cache as cache


OLD = "a" * 25
RECENT = "b" * 25
UNKNOWN = "c" * 25
BUSY = "d" * 25
CUTOFF = "2020-01-01T00:00:00Z"


class Docker:
    def __init__(self, *, drift=False, post_error=False):
        self.calls = []
        self.reads = 0
        self.removed = False
        self.drift = drift
        self.post_error = post_error

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        assert argv[:3] == ["docker", "--context", "desktop-linux"]
        args = argv[3:]
        if args[:2] == ["buildx", "ls"]:
            text = json.dumps({"Name": "desktop-linux", "Driver": "docker",
                               "Nodes": [{"Endpoint": "desktop-linux", "Status": "running"}]})
        elif args[0] == "info":
            text = '"engine-one"'
        elif args[:2] == ["system", "df"]:
            self.reads += 1
            if self.removed and self.post_error:
                return subprocess.CompletedProcess(argv, 1, "", "unavailable")
            rows = [
                {"ID": OLD, "LastUsedAt": "2019-09-01 12:00:00.123456789 +0000 UTC", "InUse": False},
                {"ID": RECENT, "LastUsedAt": "2021-01-01T00:00:00Z", "InUse": False},
                {"ID": UNKNOWN, "LastUsedAt": "3 weeks ago", "InUse": False},
                {"ID": BUSY, "LastUsedAt": "2019-01-01T00:00:00Z", "InUse": True},
            ]
            for row in rows:
                row.update(Size="1GB", CacheType="regular")
            if self.drift and self.reads > 1:
                rows[0]["LastUsedAt"] = "2021-01-01T00:00:00Z"
            if self.removed:
                rows = rows[1:]
            text = json.dumps({"BuildCache": rows})
        elif args[:2] == ["buildx", "prune"]:
            assert args[2:6] == ["--builder", "desktop-linux", "--force", "--verbose"]
            filters = [args[i + 1] for i, item in enumerate(args) if item == "--filter"]
            assert "id~=^(" + OLD + ")$" in filters
            assert not any(item.startswith("inuse") for item in filters)
            assert "type=regular" in filters
            assert any(item.startswith("until=") and item.endswith("s") for item in filters)
            assert "--all" not in args
            self.removed = True
            text = "Total: 1GB\n"
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(argv, 0, text, "")


def test_old_cache_only_preview_confirm_and_server_side_guards():
    docker = Docker()
    args = ("desktop-linux", "desktop-linux", CUTOFF)
    preview = cache.prune(*args, confirm=True, dry_run=True, runner=docker)
    assert [row["id"] for row in preview["inspection"]["candidates"]] == [OLD]
    assert not docker.removed
    result = cache.prune(*args, runner=docker)
    assert not result["removal_attempted"]
    result = cache.prune(*args, confirm=True, runner=docker)
    assert result["outcome"] == "completed"
    assert result["removed_ids"] == [OLD]
    assert result["daemon_report"] == "Total: 1GB"
    assert result["reclaimed_bytes"] is None


def test_reuse_and_postinspection_failure_do_not_claim_success():
    args = ("desktop-linux", "desktop-linux", CUTOFF)
    docker = Docker(drift=True)
    result = cache.prune(*args, confirm=True, runner=docker)
    assert result["outcome"] == "inventory-drift"
    assert not docker.removed
    result = cache.prune(*args, confirm=True, runner=Docker(post_error=True))
    assert result["outcome"] == "failed" and result["removal_attempted"]
    assert "removed_ids" not in result


@pytest.mark.parametrize("timestamp", ["2020-01-01", "yesterday", "9999-01-01T00:00:00Z"])
def test_bad_cutoff_never_contacts_docker(timestamp):
    docker = Docker()
    with pytest.raises(cache.DockerImageCleanupError):
        cache.inventory("desktop-linux", "desktop-linux", timestamp, runner=docker)
    assert docker.calls == []


@pytest.mark.parametrize("problem", ["invalid-id", "duplicate-id", "driver", "endpoint", "conflicting-builder"])
def test_inventory_identity_guards(problem):
    docker = Docker()

    def runner(argv, **kwargs):
        result = docker(argv, **kwargs)
        if argv[3:5] == ["buildx", "ls"]:
            row = json.loads(result.stdout)
            if problem == "driver":
                row["Driver"] = "remote"
            if problem == "endpoint":
                row["Nodes"][0]["Endpoint"] = "another-engine"
            result.stdout = json.dumps(row)
            if problem == "conflicting-builder":
                row["Nodes"][0]["Endpoint"] = "another-engine"
                result.stdout += "\n" + json.dumps(row)
        if argv[3:5] == ["system", "df"]:
            data = json.loads(result.stdout)
            if problem == "invalid-id":
                data["BuildCache"][0]["ID"] = "short|.*"
            if problem == "duplicate-id":
                data["BuildCache"].append(data["BuildCache"][0])
            result.stdout = json.dumps(data)
        return result

    with pytest.raises(cache.DockerImageCleanupError):
        cache.prune("desktop-linux", "desktop-linux", CUTOFF, confirm=True, runner=runner)
    assert not docker.removed


def test_multiple_ids_are_anchored_and_post_prune_none_is_failure():
    import re

    docker = Docker()
    second_id = "e" * 25
    attempted = False

    def runner(argv, **kwargs):
        nonlocal attempted
        if argv[3:5] == ["buildx", "prune"]:
            selector = next(value for value in argv if value.startswith("id~="))[4:]
            assert re.fullmatch(selector, OLD)
            assert re.fullmatch(selector, second_id)
            assert not re.search(selector, "prefix" + OLD)
            assert not re.search(selector, OLD + "suffix")
            assert not re.fullmatch(selector, RECENT)
            attempted = True
            return None
        result = docker(argv, **kwargs)
        if argv[3:5] == ["system", "df"]:
            data = json.loads(result.stdout)
            data["BuildCache"].append({**data["BuildCache"][0], "ID": second_id})
            result.stdout = json.dumps(data)
        return result

    result = cache.prune("desktop-linux", "desktop-linux", CUTOFF, confirm=True, runner=runner)
    assert attempted and result["removal_attempted"]
    assert result["outcome"] == "failed"

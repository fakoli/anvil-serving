import json
import subprocess


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

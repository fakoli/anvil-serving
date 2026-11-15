"""Offline contracts for guarded Flare package preparation."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from anvil_serving import flare_package_prepare as prepare


PACKAGE_PATH = (
    "/home/flare/.cache/huggingface/hub/"
    + prepare.REPO_FOLDER
    + "/snapshots/"
    + prepare.REVISION
    + "/"
    + prepare.FILENAME
)
CID = "a" * 64
IMAGE_ID = "sha256:" + "b" * 64


def candidate_recipe():
    return {
        "model": prepare.MODEL,
        "download": {
            "repo": prepare.MODEL,
            "revision": prepare.REVISION,
            "volume": "qwen38-flash-next-flare-431b3021",
            "cache_mount": "/root/.cache/huggingface",
        },
        "serve": {
            "engine": "flare-2193455651794642",
            "image": prepare.IMAGE,
            "entrypoint": ["/usr/local/bin/flare", "run"],
            "named_volumes": [f"{prepare.HOME_VOLUME}:{prepare.HOME_TARGET}"],
            "flags": [
                f"--revision {prepare.REVISION}",
                f"--dangerously-approve-key {prepare.PUBLISHER_FINGERPRINT}",
                f"--dangerously-accept-publisher {prepare.PUBLISHER_NAMESPACE}",
            ],
        },
    }


def verified_inspect():
    return {
        "package": {
            "signatures_verified": True,
            "publisher": {
                "namespace": prepare.PUBLISHER_NAMESPACE,
                "fingerprint": prepare.PUBLISHER_FINGERPRINT,
            },
            "official_source": f"https://huggingface.co/{prepare.MODEL}",
            "bytes": 123,
            "companions": [],
        }
    }


class ManagedDockerFake:
    """Model create/start/inspect/logs/rm without a Docker daemon."""

    def __init__(self, *, inspect_json=None, fail_start=False, fail_inspect=False, fast_exit=False,
                 publisher_error_stream="stdout"):
        self.inspect_json = inspect_json or verified_inspect()
        self.fail_start = fail_start
        self.fail_inspect = fail_inspect
        self.fast_exit = fast_exit
        self.publisher_error_stream = publisher_error_stream
        self.containers = {}
        self.calls = []
        self.image_id = IMAGE_ID
        self.previous_container_ids = []

    def __call__(self, argv, *, stdout, stderr, timeout, check):
        self.calls.append(list(argv))
        operation = argv[1]
        if operation == "pull":
            return SimpleNamespace(returncode=0)
        if operation == "container" and argv[2] == "ls":
            stdout.write(("\n".join(self.previous_container_ids) + "\n").encode("ascii"))
            return SimpleNamespace(returncode=0)
        if operation == "create":
            cidfile = Path(argv[argv.index("--cidfile") + 1])
            labels = {}
            for index, token in enumerate(argv):
                if token == "--label":
                    key, value = argv[index + 1].split("=", 1)
                    labels[key] = value
            action = "inspect" if argv[argv.index(prepare.IMAGE) + 1] == "inspect" else "download"
            self.containers[CID] = {
                "labels": labels,
                "action": action,
                "state": "created",
                "polls": 0,
                "publisher_profile_mounted": any(
                    token == "--mount"
                    and index + 1 < len(argv)
                    and "target=/run/anvil-flare-publishers.json" in argv[index + 1]
                    for index, token in enumerate(argv)
                ),
                "publisher_profile_source": next((
                    argv[index + 1].split("source=", 1)[1].split(",", 1)[0]
                    for index, token in enumerate(argv)
                    if token == "--mount" and index + 1 < len(argv)
                    and "target=/run/anvil-flare-publishers.json" in argv[index + 1]
                ), None),
            }
            cidfile.write_text(CID, encoding="ascii")
            stdout.write((CID + "\n").encode())
            return SimpleNamespace(returncode=0)
        if operation == "start":
            if self.fail_start:
                raise subprocess.TimeoutExpired(argv, timeout)
            self.containers[argv[2]]["state"] = "exited" if self.fast_exit else "running"
            return SimpleNamespace(returncode=0)
        if operation == "inspect":
            container_id = argv[-1]
            row = self.containers[container_id]
            if row["state"] == "running":
                row["polls"] += 1
                if row["polls"] > 1:
                    row["state"] = "exited"
            document = {
                "Id": container_id,
                "Image": self.image_id,
                "Config": {
                    "Image": prepare.IMAGE,
                    "Labels": row["labels"],
                },
                "HostConfig": {
                    "Memory": prepare.RAM_BYTES,
                    "MemorySwap": prepare.RAM_BYTES,
                    "Runtime": "runc",
                    "NetworkMode": "bridge" if row["action"] == "download" else "none",
                    "DeviceRequests": [],
                    "Devices": [],
                },
                "Mounts": [{
                    "Name": prepare.HOME_VOLUME,
                    "Destination": prepare.HOME_TARGET,
                    "RW": row["action"] == "download",
                }],
                "State": {
                    "Running": row["state"] == "running",
                    "Status": row["state"],
                    "ExitCode": 1 if self.fail_inspect and row["action"] == "inspect" else 0,
                },
            }
            if row.get("publisher_profile_mounted", False):
                document["Mounts"].append({
                    "Type": "bind",
                    "Source": row["publisher_profile_source"],
                    "Destination": "/run/anvil-flare-publishers.json",
                    "RW": False,
                })
            stdout.write(json.dumps(document).encode())
            return SimpleNamespace(returncode=0)
        if operation == "logs":
            action = self.containers[argv[-1]]["action"]
            if action == "download":
                payload = PACKAGE_PATH + "\n"
            elif self.fail_inspect:
                payload = (
                    "cannot verify signing key " + prepare.PUBLISHER_FINGERPRINT
                    + ": no embedded, remembered, or --publisher-keys profile names it "
                    + "(publisher local-inference-lab at https://huggingface.co/"
                    + prepare.MODEL + ")\n"
                )
            else:
                payload = json.dumps(self.inspect_json) + "\n"
            destination = stderr if action == "inspect" and self.fail_inspect and self.publisher_error_stream == "stderr" else stdout
            destination.write(payload.encode())
            return SimpleNamespace(returncode=0)
        if operation == "rm":
            assert argv[-1] == CID
            del self.containers[CID]
            return SimpleNamespace(returncode=0)
        raise AssertionError(f"unexpected fake Docker command: {argv}")


def test_dry_run_is_offline_and_contains_host_reserve_and_exact_identity():
    called = []
    plan = prepare.prepare(
        candidate_recipe(), dry_run=True, confirm=False,
        runner=lambda *args, **kwargs: called.append((args, kwargs)),
        memory_check=lambda *_args, **_kwargs: called.append("memory"),
    )
    assert called == []
    assert plan["revision"] == prepare.REVISION
    assert plan["filename"] == prepare.FILENAME
    assert plan["image"] == prepare.IMAGE
    assert plan["persistent_home_volume"] == prepare.HOME_VOLUME
    assert len(plan["recipe_digest"]) == 64
    assert plan["container_resources"] == {
        "ram_limit_bytes": prepare.RAM_BYTES,
        "ram_plus_swap_limit_bytes": prepare.RAM_BYTES,
        "runtime": "runc",
        "gpu_devices": "none",
        "host_memory_reserve_mib": prepare.HOST_RESERVE_MIB,
    }
    assert plan["host_admission"]["decision"] == "not_run_dry_run"
    assert plan["host_admission"]["host_memory_reserve_mib"] == prepare.HOST_RESERVE_MIB


def test_timeout_cleanup_uses_owned_immutable_cid():
    fake = ManagedDockerFake(fail_start=True)
    operation_id = "f" * 32
    with pytest.raises(prepare.PreparationError, match="timed out"):
        prepare._execute_container(
            "download", operation_id, runner=fake, clock=lambda: 0.0, sleep=lambda _delay: None,
        )
    assert not fake.containers
    assert any(call[1] == "rm" and call[-1] == CID for call in fake.calls)


def test_unconfirmed_execution_is_refused_before_host_or_docker_checks():
    calls = []
    with pytest.raises(prepare.PreparationError, match="pass --confirm or --dry-run"):
        prepare.prepare(
            candidate_recipe(), dry_run=False, confirm=False,
            runner=lambda *args, **kwargs: calls.append("docker"),
            memory_check=lambda *_args, **_kwargs: calls.append("memory"),
        )
    assert calls == []


def test_accepted_public_profile_is_copied_mounted_read_only_and_cleaned(tmp_path, monkeypatch):
    key = bytes(range(32))
    fingerprint = __import__("hashlib").sha256(key).hexdigest()
    monkeypatch.setattr(prepare, "PUBLISHER_FINGERPRINT", fingerprint)
    profile = tmp_path / "publisher.json"
    profile.write_text(json.dumps([{
        "name": "Pinned test publisher",
        "namespace": prepare.PUBLISHER_NAMESPACE,
        "public_key": list(key),
        "development": False,
    }]), encoding="utf-8")
    fake = ManagedDockerFake()
    receipt = prepare.prepare(
        candidate_recipe(), dry_run=False, confirm=True, publisher_profile=str(profile),
        runner=fake, memory_check=lambda _limits: None,
        lock_path=tmp_path / "flare-lock",
        clock=iter([0.0, 0.1, 0.2, 0.3, 1.0, 1.1, 1.2, 1.3]).__next__,
        sleep=lambda _delay: None,
    )
    assert receipt["signed_index_verification"] == "verified_by_flare_inspect"
    inspect_create = next(call for call in fake.calls if call[1] == "create"
                          and call[call.index(prepare.IMAGE) + 1] == "inspect")
    profile_mount_index = inspect_create.index("--mount") + 1
    profile_mount = inspect_create[profile_mount_index + 2]
    assert "target=/run/anvil-flare-publishers.json" in profile_mount
    assert profile_mount.endswith(",readonly")
    assert inspect_create[inspect_create.index(prepare.IMAGE) + 1:inspect_create.index(prepare.IMAGE) + 4] == [
        "inspect", "--json", "--publisher-keys",
    ]
    mounted_source = profile_mount.split("source=", 1)[1].split(",", 1)[0]
    assert not Path(mounted_source).exists()
    assert not fake.containers


def self_action(fake, container_id):
    return fake.containers[container_id]["action"]

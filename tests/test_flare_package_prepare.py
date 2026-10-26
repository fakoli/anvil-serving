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


def test_docker_options_and_read_only_inspect_mount_precede_image():
    operation_id = "c" * 32
    download = prepare.build_container_argv(
        "download", operation_id=operation_id, step="download", cidfile="/tmp/cid"
    )
    image_index = download.index(prepare.IMAGE)
    assert image_index > download.index("--memory")
    assert image_index > download.index("--mount")
    assert download[download.index("--network") + 1] == "bridge"
    assert download[download.index("--memory") + 1] == str(prepare.RAM_BYTES)
    assert download[download.index("--memory-swap") + 1] == str(prepare.RAM_BYTES)
    assert download[download.index("--runtime") + 1] == "runc"
    assert "--gpus" not in download
    assert download[image_index + 1:] == [
        "download", prepare.MODEL, "--revision", prepare.REVISION,
        "--filename", prepare.FILENAME,
    ]

    inspect = prepare.build_container_argv(
        "inspect", operation_id=operation_id, step="inspect", cidfile="/tmp/cid",
        package_path=PACKAGE_PATH,
    )
    image_index = inspect.index(prepare.IMAGE)
    home_mount = inspect[inspect.index("--mount") + 1]
    assert home_mount.endswith(",readonly")
    assert inspect[inspect.index("--network") + 1] == "none"
    assert image_index > inspect.index("--network")
    assert inspect[image_index + 1:] == ["inspect", "--json", PACKAGE_PATH]
    profiled_inspect = prepare.build_container_argv(
        "inspect", operation_id=operation_id, step="inspect", cidfile="/tmp/cid",
        package_path=PACKAGE_PATH, publisher_profile_path="/tmp/publisher.json",
    )
    profiled_image_index = profiled_inspect.index(prepare.IMAGE)
    mount_indexes = [index for index, token in enumerate(profiled_inspect) if token == "--mount"]
    assert mount_indexes and max(mount_indexes) < profiled_image_index
    assert profiled_inspect[profiled_image_index + 1:profiled_image_index + 6] == [
        "inspect", "--json", "--publisher-keys",
        "/run/anvil-flare-publishers.json", PACKAGE_PATH,
    ]


def test_package_path_binds_exact_repo_revision_and_native_cache_view():
    assert prepare._safe_package_path(PACKAGE_PATH)
    alternate = PACKAGE_PATH.replace("/snapshots/", "/flare-packages/") + "/" + prepare.FILENAME
    assert prepare._safe_package_path(alternate)
    for invalid in (
        PACKAGE_PATH.replace(prepare.REPO_FOLDER, "models--other--repo"),
        PACKAGE_PATH.replace(prepare.REVISION, "f" * 40),
        PACKAGE_PATH.replace("/snapshots/", "/flare-packages/") + "/wrong.safetensors",
        "/home/flare/.cache/huggingface/hub/repo/snapshots/rev/" + prepare.FILENAME,
        "/etc/passwd",
    ):
        assert not prepare._safe_package_path(invalid)
        with pytest.raises(prepare.PreparationError, match="outside the pinned Flare cache"):
            prepare.build_container_argv(
                "inspect", operation_id="c" * 32, step="inspect", cidfile="/tmp/cid",
                package_path=invalid,
            )


@pytest.mark.parametrize(
    "mutation,expected",
    [
        (lambda recipe: recipe["serve"].update(entrypoint=["/bin/sh", "-c"]), "entrypoint"),
        (lambda recipe: recipe["serve"].update(model_path="/other/model.safetensors"), "default model"),
        (lambda recipe: recipe["serve"]["flags"].append("--revision " + "f" * 40), "revision"),
        (lambda recipe: recipe["serve"]["flags"].append("--cache-dir /tmp/other"), "override"),
        (lambda recipe: recipe["serve"]["named_volumes"].append("shadow:/home/flare/.cache"), "overlapping"),
    ],
)
def test_recipe_loader_path_must_match_prepared_identity(mutation, expected):
    recipe = candidate_recipe()
    mutation(recipe)
    with pytest.raises(prepare.PreparationError, match=expected):
        prepare.validate_recipe_identity(recipe)


def test_recipe_publisher_and_revision_flags_are_each_exactly_once():
    recipe = candidate_recipe()
    recipe["serve"]["flags"] = [
        flag for flag in recipe["serve"]["flags"] if not flag.startswith("--revision")
    ]
    with pytest.raises(prepare.PreparationError, match="revision exactly once"):
        prepare.validate_recipe_identity(recipe)


def test_inspect_projection_records_image_resources_and_no_gpu_binding():
    fake = ManagedDockerFake()
    operation_id = "d" * 32
    # Create the fake ownership record exactly as the managed create step would.
    fake.containers[CID] = {
        "labels": {
            prepare._OWNERSHIP_LABEL: "v1",
            prepare._OPERATION_LABEL: operation_id,
            prepare._STEP_LABEL: "inspect",
        },
        "action": "inspect",
        "state": "running",
        "polls": 0,
    }
    projection = prepare._inspect_projection(CID, operation_id, "inspect", runner=fake)
    assert projection["helper_image_id"] == IMAGE_ID
    assert projection["memory_limit_bytes"] == prepare.RAM_BYTES
    assert projection["ram_plus_swap_limit_bytes"] == prepare.RAM_BYTES
    assert projection["runtime"] == "runc"
    assert projection["gpu_devices"] == "none"
    assert projection["network"] == "none"
    assert projection["flare_home_mount_read_only"] is True
    assert projection["observed_running"] is True
    assert "Env" not in json.dumps(projection)


def test_inspect_projection_fails_closed_on_malformed_runtime_sections():
    fake = ManagedDockerFake()
    operation_id = "d" * 32
    fake.containers[CID] = {
        "labels": {
            prepare._OWNERSHIP_LABEL: "v1",
            prepare._OPERATION_LABEL: operation_id,
            prepare._STEP_LABEL: "inspect",
        },
        "action": "inspect",
        "state": "running",
        "polls": 0,
    }
    original = fake.__call__

    def malformed(argv, **kwargs):
        result = original(argv, **kwargs)
        if argv[1] == "inspect" and argv[2] == "--type":
            kwargs["stdout"].seek(0)
            kwargs["stdout"].truncate()
            kwargs["stdout"].write(b'{"Id":"' + CID.encode() + b'","Config":null}')
        return result

    with pytest.raises(prepare.PreparationError, match="missing required runtime sections"):
        prepare._inspect_projection(CID, operation_id, "inspect", runner=malformed)


def test_public_profile_must_match_pinned_identity(tmp_path):
    profile = tmp_path / "publisher.json"
    key = bytes(32)
    profile.write_text(json.dumps([{
        "name": "Pinned public publisher",
        "namespace": prepare.PUBLISHER_NAMESPACE,
        "public_key": list(key),
        "development": False,
    }]), encoding="utf-8")
    with pytest.raises(prepare.PreparationError, match="exact pinned namespace and fingerprint"):
        prepare._validated_publisher_profile(str(profile))


def test_success_receipt_separates_verification_and_binds_loader():
    identity = prepare.validate_recipe_identity(candidate_recipe())
    helper = {
        "download": {"helper_image_id": IMAGE_ID, "memory_limit_bytes": prepare.RAM_BYTES},
        "inspect": {"helper_image_id": IMAGE_ID, "memory_limit_bytes": prepare.RAM_BYTES},
    }
    admission = {
        "decision": "passed",
        "ram_limit_mib": 8192,
        "ram_plus_swap_limit_mib": 8192,
        "host_memory_reserve_mib": prepare.HOST_RESERVE_MIB,
        "check": "recipe_memory.check_host",
    }
    receipt = prepare._inspect_receipt(
        json.dumps(verified_inspect()).encode(),
        package_path=PACKAGE_PATH,
        identity=identity,
        helper=helper,
        host_admission=admission,
    )
    assert receipt["signed_index_verification"] == "verified_by_flare_inspect"
    assert receipt["payload_digest_coverage"] == "not_checked_by_flare_inspect"
    assert receipt["loader_validation"] == "not_run"
    assert receipt["recipe_digest"] == identity["recipe_digest"]
    assert receipt["helper_binding"]["inspect"]["helper_image_id"] == IMAGE_ID
    assert receipt["host_admission"]["host_memory_reserve_mib"] == prepare.HOST_RESERVE_MIB


def test_prepare_success_uses_owned_containers_and_releases_shared_lock(tmp_path):
    fake = ManagedDockerFake()
    plan = prepare.prepare(
        candidate_recipe(), dry_run=False, confirm=True, runner=fake,
        memory_check=lambda limits: None,
        lock_path=tmp_path / "flare-lock",
        clock=iter([0.0, 0.1, 0.2, 0.3, 1.0, 1.1, 1.2, 1.3]).__next__,
        sleep=lambda _delay: None,
    )
    assert plan["signed_index_verification"] == "verified_by_flare_inspect"
    assert plan["package_path"] == PACKAGE_PATH
    assert not fake.containers
    creates = [call for call in fake.calls if call[1] == "create"]
    assert len(creates) == 2
    assert all("--gpus" not in call for call in creates)
    assert [call[call.index("--network") + 1] for call in creates] == ["bridge", "none"]


def test_fast_successful_helper_exit_before_first_running_poll_is_accepted(tmp_path):
    fake = ManagedDockerFake(fast_exit=True)
    receipt = prepare.prepare(
        candidate_recipe(), dry_run=False, confirm=True, runner=fake,
        memory_check=lambda _limits: None,
        lock_path=tmp_path / "flare-lock",
        clock=iter([0.0, 0.1, 0.2, 0.3, 1.0, 1.1, 1.2, 1.3]).__next__,
        sleep=lambda _delay: None,
    )
    assert receipt["signed_index_verification"] == "verified_by_flare_inspect"
    assert not fake.containers


def test_unowned_container_is_never_removed():
    fake = ManagedDockerFake()
    fake.containers[CID] = {
        "labels": {prepare._OWNERSHIP_LABEL: "v1", prepare._OPERATION_LABEL: "other", prepare._STEP_LABEL: "inspect"},
        "action": "inspect",
        "state": "created",
        "polls": 0,
    }
    with pytest.raises(prepare.PreparationError, match="ownership"):
        prepare._remove_owned_container(CID, "e" * 32, "inspect", runner=fake)
    assert not any(call[1] == "rm" for call in fake.calls)


def test_shared_home_lock_rejects_parallel_operation(tmp_path):
    lock_path = tmp_path / "shared-home"
    with prepare._preparation_lock(lock_path):
        with pytest.raises(prepare.PreparationError, match="another Flare package preparation"):
            with prepare._preparation_lock(lock_path):
                pytest.fail("parallel shared-home operation entered the lock")


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



def test_missing_default_profile_fails_closed_and_removes_only_owned_helper(tmp_path):
    fake = ManagedDockerFake(fail_inspect=True, publisher_error_stream="stderr")
    with pytest.raises(prepare.PreparationError, match="cannot verify signing key") as error:
        prepare.prepare(
            candidate_recipe(), dry_run=False, confirm=True, runner=fake,
            memory_check=lambda _limits: None,
            lock_path=tmp_path / "flare-lock",
            clock=iter([0.0, 0.1, 0.2, 0.3, 1.0, 1.1, 1.2, 1.3]).__next__,
            sleep=lambda _delay: None,
        )
    assert prepare.PUBLISHER_FINGERPRINT in str(error.value)
    assert not fake.containers
    assert sum(call[1] == "rm" for call in fake.calls) == 2


def test_retry_refuses_preexisting_labeled_helper_before_pull_or_new_writer(tmp_path):
    fake = ManagedDockerFake()
    fake.previous_container_ids = ["c" * 64]
    with pytest.raises(prepare.PreparationError, match="earlier labeled Flare preparation helper"):
        prepare.prepare(
            candidate_recipe(), dry_run=False, confirm=True, runner=fake,
            memory_check=lambda _limits: None,
            lock_path=tmp_path / "flare-lock",
            clock=iter([0.0, 0.1, 0.2, 0.3, 1.0, 1.1, 1.2, 1.3]).__next__,
            sleep=lambda _delay: None,
        )
    assert any(call[1:3] == ["container", "ls"] for call in fake.calls)
    assert not any(call[1] in {"pull", "create"} for call in fake.calls)


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

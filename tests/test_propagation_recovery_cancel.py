"""Exact-job settlement uses real custody, locks and delayed native writes."""
from dataclasses import replace
import errno
import multiprocessing
from pathlib import Path
import subprocess
import sys

import pytest

from anvil_serving import workflows_cli
from anvil_serving.control_plane.controller.propagation_supervisor import _identity
from anvil_serving.control_plane.propagation import capability_declaration
from anvil_serving.control_plane.propagation_jobs import PropagationJobError, _hash
from anvil_serving.propagation_fencing import NativeMutationFence, PropagationFenceError, TrustedNativeOwner
from tests.test_controller import running_controller, _authorization_policy, _request
from tests.test_propagation_jobs import ControlledOwner
from tests.test_propagation_contracts import _matches_declared_schema

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="native execution owner is Linux")


def test_original_job_empty_custody_recovery_and_delayed_writer(tmp_path, monkeypatch):
    owner = ControlledOwner(tmp_path, count=2)
    accepted = owner.accept()
    job, _ = owner.jobs.submit({"intent_id": accepted["intent_id"],
        "operation_id": _hash(["effect/v1", owner.contract.digest, "fleet", "apply"]),
        "canonical_contract": owner.contract.canonical, "preview_digest": "a" * 64,
        "profile_id": "profile-1", "profile_digest": "a" * 64,
        "resources": ["catalog-000", "catalog-001"], "deadline": owner.contract.value["deadline_at"]})
    job_id = job["job_id"]
    processes = [subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"]) for _ in range(2)]
    try:
        identities = [_identity(process.pid) for process in processes]
        assert all(identities)
        token = owner.jobs.prepare_launch(job_id)
        owner.jobs.register_launch(job_id, token, identities[0])
        owner.jobs.begin_execution(job_id, token, identities[0])
        planned = owner.jobs.prepare_profile_child(job_id, identities[0])
        owner.jobs.register_profile_child(job_id, identities[1])
        original_result = {"outcome": "cancelled", "native_effects": planned, "quiescent": False}
        owner.jobs.record_result(job_id, identities[0], original_result)
        args = {"intent_id": accepted["intent_id"], "job_id": job_id,
                "expected_digest": owner.contract.digest}
        native_args = {"intent_id": accepted["intent_id"], "job_id": job_id,
                      "contract_digest": owner.contract.digest, "generation": 1,
                      "target_id": "target-000", "resource_id": "catalog-000"}
        assert owner.service.revoked_authority(**native_args)["revoked"] is True
        with pytest.raises(PropagationJobError, match="stale_generation"):
            owner.service.current_authority(**native_args)
        for key, value in (("job_id", "other-job"), ("generation", 2), ("resource_id", "other-resource")):
            with pytest.raises(PropagationJobError):
                owner.service.revoked_authority(**{**native_args, key: value})

        fences = []
        for target in owner.contract.value["targets"]:
            path = tmp_path / (target["target_id"] + ".json")
            path.write_bytes(b"before")
            path.chmod(0o600)
            def current(digest, generation, epoch, target=target):
                try:
                    return owner.service.current_authority(**{**native_args,
                        "target_id": target["target_id"], "resource_id": target["resource_keys"][0],
                        "contract_digest": digest, "generation": generation})["current"] is True
                except PropagationJobError:
                    return False
            def revoked(original, digest, generation, epoch, target=target):
                return original == job_id and epoch == "epoch-1" and owner.service.revoked_authority(
                    **{**native_args, "target_id": target["target_id"], "resource_id": target["resource_keys"][0],
                       "contract_digest": digest, "generation": generation})["revoked"] is True
            fence = NativeMutationFence(TrustedNativeOwner("owner-1", target["resource_keys"][0], tmp_path,
                effect_bindings={"catalog": ("catalog-apply", path)}, current_authority=current,
                cancelled_authority=revoked), tmp_path)
            grant = fence.grant(canonical_contract=owner.contract.canonical, generation=1,
                                effects=("catalog",), target_paths=(path,))
            fences.append((target, path, fence, grant))

        calls = []
        def evidence(raw, stored_job, custody):
            assert raw == owner.contract.canonical and stored_job["job_id"] == job_id and custody == identities
            calls.append(job_id)
            rows = [{**{key: target[key] for key in (
                "target_id", "installation_id", "profile_id", "runtime_id", "expected_identity_digest")},
                "native": fence.inspect_cancelled(grant, canonical_contract=raw,
                    target_paths=(path,), job_id=job_id)} for target, path, fence, grant in fences]
            return {"observed_at": rows[0]["native"]["observed_at"], "targets": rows}
        owner.service.profile = replace(owner.profile, cancellation_evidence=evidence)
        with pytest.raises(PropagationJobError, match="operation_in_progress"):
            owner.service.recover_cancelled(**args)
        process_stat = Path(f"/proc/{identities[0]['pid']}/stat")
        original_stat = process_stat.read_text(encoding="ascii")
        for process in processes:
            process.terminate()
            process.wait(timeout=5)
        assert all(_identity(process.pid) is None for process in processes)

        original = owner.jobs.lookup_internal(job_id)
        def unchanged():
            assert owner.jobs.lookup_internal(job_id) == original
            with owner.jobs._connection() as db:
                assert db.execute("SELECT count(*) FROM propagation_native_resources WHERE job_id=?", (job_id,)).fetchone()[0] == 2
                assert db.execute("SELECT count(*) FROM propagation_native_settlements").fetchone()[0] == 0
                assert db.execute("SELECT result_json FROM propagation_native_children WHERE job_id=?", (job_id,)).fetchone()[0] is None
            assert all(path.read_bytes() == b"before" for _, path, _, _ in fences)

        read_text = Path.read_text
        for observed_stat, boot in ((original_stat, None), (original_stat, ""), ("malformed", "boot")):
            def observe(path, *args, **kwargs):
                if path == process_stat:
                    return observed_stat
                if path == Path("/proc/sys/kernel/random/boot_id"):
                    if boot is None:
                        raise FileNotFoundError(errno.ENOENT, "synthetic missing boot identity")
                    return boot
                return read_text(path, *args, **kwargs)
            with monkeypatch.context() as isolated:
                isolated.setattr(Path, "read_text", observe)
                with pytest.raises(PropagationJobError, match="process_observation_failed"):
                    owner.service.recover_cancelled(**args)
            unchanged()
        state_offset = original_stat.rfind(")") + 2
        zombie_stat = original_stat[:state_offset] + "Z" + original_stat[state_offset + 1:]
        with monkeypatch.context() as isolated:
            isolated.setattr(Path, "read_text", lambda path, *args, **kwargs:
                zombie_stat if path == process_stat else read_text(path, *args, **kwargs))
            assert _identity(identities[0]["pid"]) is None

        target, path, fence, grant = fences[0]
        ctx = multiprocessing.get_context("fork")
        ready, release, outcome = ctx.Event(), ctx.Event(), ctx.Queue()
        def delayed_writer():
            with fence._lock():
                ready.set()
                assert release.wait(5)
            try:
                with fence.transaction(grant, canonical_contract=owner.contract.canonical, target_paths=(path,)) as journal:
                    journal.begin_effect("catalog", path, b"before", b"unsafe")
                    journal.write("catalog", b"unsafe")
            except PropagationFenceError as error:
                outcome.put(error.code)
        writer = ctx.Process(target=delayed_writer)
        writer.start()
        try:
            assert ready.wait(5)
            with pytest.raises(PropagationFenceError, match="operation_in_progress"):
                owner.service.recover_cancelled(**args)
            unchanged()
        finally:
            release.set()
            writer.join(5)
            if writer.is_alive():
                writer.kill()
                writer.join(5)
        assert writer.exitcode == 0 and outcome.get(timeout=1) == "stale_generation"
        unchanged()

        missing = NativeMutationFence(replace(fence.owner, cancelled_authority=lambda *_: False), tmp_path)
        missing_grant = missing.grant(canonical_contract=owner.contract.canonical, generation=1,
                                      effects=("catalog",), target_paths=(path,))
        with pytest.raises(PropagationFenceError, match="revocation_unproven"):
            missing.inspect_cancelled(missing_grant, canonical_contract=owner.contract.canonical, target_paths=(path,), job_id=job_id)
        bound_root = tmp_path / "reserved"
        bound_root.mkdir(mode=0o700)
        bound_path = bound_root / "catalog.json"
        bound_path.write_bytes(b"before")
        bound_path.chmod(0o600)
        bound = NativeMutationFence(replace(fence.owner, storage_root=bound_root,
            backup_root=bound_root / "backup", effect_bindings={"catalog": ("catalog-apply", bound_path)},
            current_authority=lambda *_: True), bound_root)
        bound_grant = bound.grant(canonical_contract=owner.contract.canonical, generation=1,
                                 effects=("catalog",), target_paths=(bound_path,))
        bound.bind_job(bound_grant, canonical_contract=owner.contract.canonical,
                       target_paths=(bound_path,), job_id=job_id, job_digest="a" * 64)
        with pytest.raises(PropagationFenceError, match="native_effects_present"):
            bound.inspect_cancelled(bound_grant, canonical_contract=owner.contract.canonical,
                                    target_paths=(bound_path,), job_id=job_id)
        orphan = fence.journal_root / (fence.owner.resource_id + ".orphan.json")
        orphan.write_text("{}")
        orphan.chmod(0o600)
        with pytest.raises(PropagationFenceError, match="native_effects_present"):
            owner.service.recover_cancelled(**args)
        orphan.unlink()
        unchanged()
        for corrupt in (lambda data: data["targets"].pop(),
                        lambda data: data["targets"][0].update(target_id="wrong-target"),
                        lambda data: data["targets"][0]["native"].update(job_id="wrong-job"),
                        lambda data: data["targets"][0]["native"].update(generation=True)):
            def bad(*values):
                data = evidence(*values)
                corrupt(data)
                return data
            owner.service.profile = replace(owner.profile, cancellation_evidence=bad)
            with pytest.raises(PropagationJobError):
                owner.service.recover_cancelled(**args)
            unchanged()
        owner.service.profile = replace(owner.profile, cancellation_evidence=evidence)
        binding = owner.jobs.cancellation_binding(job_id)
        with pytest.raises(PropagationJobError, match="settlement_conflict"):
            owner.jobs.reconcile_cancelled(job_id, "0" * 64, {"job_id": job_id})
        unchanged()
        first = owner.service.recover_cancelled(**args)
        assert first["state"] == "cancelled" and first["quiescent"] is True
        schema = next(row["result_schema"] for row in capability_declaration()["operations"]
                      if row["name"] == "propagation.recovery.cancel.v1")
        assert _matches_declared_schema(first, schema)
        settled = owner.jobs.lookup_internal(job_id)
        assert {key: value for key, value in settled.items() if key != "state"} == {key: value for key, value in original.items() if key != "state"}
        count = len(calls)
        assert owner.service.recover_cancelled(**args) == first and len(calls) == count
        assert owner.jobs.cancellation_receipt(job_id, args["intent_id"], args["expected_digest"])["custody_digest"] == binding["custody_digest"]
        with owner.jobs._connection() as db:
            assert db.execute("SELECT count(*) FROM propagation_native_resources WHERE job_id=?", (job_id,)).fetchone()[0] == 0
        with pytest.raises(PropagationJobError, match="intent_conflict"):
            owner.service.recover_cancelled(**{**args, "expected_digest": "b" * 64})

        policy = _authorization_policy(tmp_path, [
            {"id": role, "scopes": ["propagation:" + role], "credential_env": role.upper()}
            for role in ("recovery", "admission", "activity", "status")])
        env = {role.upper(): "synthetic-" + role for role in ("recovery", "admission", "activity", "status")}
        env["ANVIL_CONTROLLER_TOKEN"] = "synthetic-controller"
        with running_controller(env=env, authorization_policy=policy, propagation_service=owner.service) as (host, port):
            for role in ("recovery", "admission", "activity", "status"):
                _, _, result, _ = _request(host, port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1,
                    "method": "tools/call", "params": {"name": "propagation.recovery.cancel.v1", "arguments": args}},
                    {"Authorization": "Bearer " + env[role.upper()]})
                assert (result.get("result", {}).get("structuredContent", {}).get("ok") is True) == (role == "recovery")
        argv = ["recovery", "cancel", "--intent-id", args["intent_id"], "--job-id", job_id,
                "--expected-digest", args["expected_digest"], "--confirm"]
        assert workflows_cli._arguments(argv) == ("recovery_cancel", args)
        seen = []
        monkeypatch.setattr(workflows_cli, "_config", lambda **kwargs: seen.append(kwargs) or ("http://127.0.0.1:8765", "synthetic"))
        monkeypatch.setattr(workflows_cli, "_call", lambda *_args: first)
        assert workflows_cli.main(argv).data == first and seen == [{"recovery": True}]
        assert all(path.read_bytes() == b"before" for _, path, _, _ in fences)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)

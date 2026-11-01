"""Offline campaign scheduling and freeze regressions; no model traffic."""
import json
import os
import socket
from concurrent.futures import ThreadPoolExecutor

import pytest

from anvil_serving.benchmarking import campaigns, campaign_execution, campaigns_cli, worker
from anvil_serving.benchmarking.jobs import BenchmarkJobError, JOB_SPEC_SCHEMA
from anvil_serving.control_plane.controller.store import BenchmarkJobStore


NOW = "2026-10-24T12:00:00Z"
STATE_MANIFEST = {"manifest_sha256": "f" * 64}


def specification(*, arms=1, concurrency=1, tasks=None, repeats=1):
    tasks = tasks or [("agentic", "structured-edit")]
    endpoint = {"base_url": "http://127.0.0.1:8000/v1", "model": "llm.primary"}
    local = {"id": socket.gethostname()}
    result = {"schema": campaigns.SCHEMA, "campaign_id": "campaign-1", "ownership_id": "operator-1",
        "submitted_at": NOW, "budgets": {"total_s": 7200, "per_arm_s": 3600, "drain_reserve_s": 10},
        "telemetry": dict.fromkeys(("engine", "gpu", "host", "cgroup"), "required"),
        "drain_policy": {}, "arms": [], "cells": []}
    for index in range(arms):
        arm = f"arm-{index}"
        result["arms"].append({"arm_id": arm, "identity": {"model": "model", "revision": "rev",
            "image": "sha256:" + "a"*64, "runtime": "runtime", "hardware": ["GPU-a"],
            "recipe_selector": "recipe", "recipe_sha256": "b"*64, "served_model": "served-model"},
            "activation": {"registry_path": "/operator/recipes.toml", "monitoring_receipt_path": "/operator/active-recipe.json",
                           "observer_base_url": "http://127.0.0.1:19100", "upstream_base_url": "http://127.0.0.1:8000/v1",
                           "container": "fixture-container"}, "endpoint": endpoint, "worker": local})
        for repeat in range(1, repeats+1):
            for count in ([1,4] if concurrency == "both" else [concurrency]):
                for number, (suite, task) in enumerate(tasks):
                    cell_id = f"{arm}-r{repeat}-c{count}-t{number}"
                    params = {"case_ids" if suite == "agentic" else "instance_ids": [task]}
                    if task == "tool-recovery":
                        params["recovery_result"] = "error"
                    result["cells"].append({"cell_id": cell_id, "arm_id": arm,
                        "batch_id": f"{arm}-r{repeat}-c{count}-b{number//count}", "task_id": task,
                        "repeat": repeat, "concurrency": count, "cache_condition": "uncontrolled",
                        "spec": {"schema": JOB_SPEC_SCHEMA, "run_id": cell_id, "ownership_id": "operator-1",
                            "suite": suite, "profile": "pilot-v2", "endpoint": endpoint, "worker": local,
                            "submitted_at": NOW, "timeout_s": 30, "parameters": params}})
    return result


@pytest.fixture
def store(tmp_path):
    return BenchmarkJobStore(str(tmp_path / "jobs.sqlite"), run_root=str(tmp_path / "runs"))


@pytest.fixture
def fake_execution(monkeypatch):
    receipts = {}
    launches = []
    monkeypatch.setattr(campaigns, "source_sha256", lambda: "c"*64)
    monkeypatch.setattr(campaign_execution, "prepare_child", lambda spec, **kwargs: {"plan_sha256": "d"*64})
    monkeypatch.setattr(campaign_execution, "verify_prepared_child", lambda *args, **kwargs: {})
    monkeypatch.setattr(campaign_execution, "batch_ready", lambda *args, **kwargs: True)
    monkeypatch.setattr(campaign_execution, "reconcile_batch", lambda manifest, batch, **kwargs:
        receipts.get(batch, {"status": "pending", "active_seconds": None, "reserved_seconds": 0}))
    return receipts, launches


def test_prepare_freezes_input_and_rejects_drift(store, fake_execution, monkeypatch):
    value = specification()
    manifest = campaigns.prepare_campaign(value, store)
    assert campaigns.prepare_campaign(value, store) == manifest
    value["cells"][0]["spec"]["timeout_s"] += 1
    with pytest.raises(BenchmarkJobError, match="different input"):
        campaigns.prepare_campaign(value, store)
    monkeypatch.setattr(campaigns, "source_sha256", lambda: "e"*64)
    with pytest.raises(BenchmarkJobError, match="source changed"):
        campaigns.run_campaign(manifest, store, arm_id="arm-0")
    assert store.status(manifest["cells"][0]["spec"]["run_id"]) is None


def test_c4_launches_exact_batch_and_resumes_terminal_without_retry(store, fake_execution, monkeypatch):
    receipts, launches = fake_execution
    tasks = [("agentic", task) for task in ("structured-edit", "tool-sequence", "parallel-tools", "dependent-result")]
    manifest = campaigns.prepare_campaign(specification(concurrency=4, tasks=tasks), store)
    def launch(**kwargs):
        launches.append(kwargs["run_id"])
        store.claim(kwargs["run_id"])
    def release(manifest, batch, **kwargs):
        assert len(launches) == 4
        for run_id in launches:
            store.transition(run_id, "failed", failure={"class": "model_behavior", "message": "retained failure"})
        receipts[batch] = {"status": "closed", "active_seconds": 3, "reserved_seconds": 40}
    monkeypatch.setattr(worker, "launch_benchmark_job", launch)
    monkeypatch.setattr(campaign_execution, "release_batch", release)
    assert campaigns.run_campaign(manifest, store, arm_id="arm-0", poll_s=0)["state"] == "completed"
    campaigns.run_campaign(manifest, store, arm_id="arm-0", poll_s=0)
    assert len(launches) == 4


def test_cancel_persists_intent_before_owned_cancellation(store, fake_execution, monkeypatch):
    manifest = campaigns.prepare_campaign(specification(), store)
    cell = manifest["cells"][0]
    store.submit(campaign_execution.bind_campaign_child(cell["spec"], manifest_sha256=manifest["manifest_sha256"],
        campaign_id=manifest["campaign_id"], **{key: cell[key] for key in ("cell_id", "arm_id", "batch_id")}))
    def cancel(store, run_id):
        directory = campaigns.campaign_directory(store.run_root, manifest["ownership_id"], manifest["campaign_id"])
        assert json.loads((directory / "state.json").read_text())["cancel_requested"] is True
        return store.transition(run_id, "cancelled")
    monkeypatch.setattr(worker, "cancel_benchmark_job", cancel)
    campaigns.cancel_campaign(manifest, store)
    assert campaigns.run_campaign(manifest, store, arm_id="arm-0")["state"] == "cancelled"


def test_cli_plan_is_read_only_and_mutations_require_confirmation(tmp_path, monkeypatch):
    path = tmp_path / "campaign.json"
    path.write_text(json.dumps(specification()))
    monkeypatch.setattr(campaigns_cli, "_store", lambda: pytest.fail("read-only plan opened store"))
    assert campaigns_cli.run(["plan", "--spec", str(path)])["campaign_id"] == "campaign-1"
    with pytest.raises(BenchmarkJobError, match="requires --confirm"):
        campaigns_cli.run(["prepare", "--spec", str(path)])


def test_campaign_lock_rejects_symlink_nonregular_and_contention(tmp_path):
    target = tmp_path / "target"
    target.write_text("x")
    (tmp_path / ".lock").symlink_to(target)
    with pytest.raises(BenchmarkJobError, match="regular file"):
        with campaigns.campaign_lock(tmp_path, timeout_s=0):
            pass
    (tmp_path / ".lock").unlink()
    if hasattr(os, "mkfifo"):
        os.mkfifo(tmp_path / ".lock")
        with pytest.raises(BenchmarkJobError, match="regular file"):
            with campaigns.campaign_lock(tmp_path, timeout_s=0):
                pass
        (tmp_path / ".lock").unlink()
    descriptor = os.open(tmp_path / ".lock", os.O_RDWR | os.O_CREAT)
    try:
        import fcntl
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        with pytest.raises(BenchmarkJobError, match="bounded deadline"):
            with campaigns.campaign_lock(tmp_path, timeout_s=0):
                pass
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def test_concurrent_run_cancel_does_not_release_traffic(store, fake_execution, monkeypatch):
    import threading
    manifest = campaigns.prepare_campaign(specification(), store)
    launched = threading.Event()
    def launch(**kwargs):
        store.claim(kwargs["run_id"])
        launched.set()
    monkeypatch.setattr(worker, "launch_benchmark_job", launch)
    monkeypatch.setattr(worker, "cancel_benchmark_job", lambda store, run_id: store.transition(run_id, "cancelled"))
    monkeypatch.setattr(campaign_execution, "batch_ready", lambda *args, **kwargs: False)
    monkeypatch.setattr(campaign_execution, "release_batch", lambda *args, **kwargs: pytest.fail("cancelled release"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(campaigns.run_campaign, manifest, store, arm_id="arm-0")
        assert launched.wait(5)
        cancelled = pool.submit(campaigns.cancel_campaign, manifest, store)
        assert cancelled.result(5)["cancel_requested"] is True
        assert running.result(5)["state"] == "cancelled"

"""Activation checks use real registry and managed Docker record parsing."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from anvil_serving import serve_recipes as sr
from anvil_serving.benchmarking import campaign_identity as ci
from anvil_serving.benchmarking.jobs import BenchmarkJobError
from anvil_serving.router.model_metadata import endpoint_identity_sha256


@pytest.fixture
def activation(tmp_path):
    recipe = {"model": "org/model", "download": {"repo": "org/model", "revision": "a" * 40},
              "serve": {"engine": "ignis", "port": 39100}}
    registry = tmp_path / "recipes.toml"
    registry.write_text(sr.format_recipe(recipe))
    recipe_hash, registry_hash = sr.recipe_digest(recipe), hashlib.sha256(registry.read_bytes()).hexdigest()
    value = {
        "identity": {"model": "org/model", "revision": "a" * 40, "image": "sha256:" + "b" * 64,
                     "runtime": "ignis", "hardware": ["GPU-test-device"], "recipe_selector": "org/model",
                     "recipe_sha256": recipe_hash, "served_model": "served-model"},
        "endpoint": {"base_url": "http://127.0.0.1:39100/v1", "model": "served-model"},
        "activation": {"registry_path": str(registry), "monitoring_receipt_path": str(tmp_path / "active.json"),
                       "observer_base_url": "http://127.0.0.1:19100", "upstream_base_url": "http://127.0.0.1:39100/v1",
                       "container": "managed-model"},
    }
    raw = {"Name": "/managed-model", "Id": "d" * 64, "Image": "sha256:" + "b" * 64,
           "Config": {"Labels": {sr.RECIPE_MANAGED_LABEL: sr.RECIPE_MANAGED_VALUE,
                      sr.RECIPE_MODEL_LABEL: "org/model", sr.RECIPE_REVISION_LABEL: "a" * 40,
                      sr.RECIPE_DIGEST_LABEL: recipe_hash, sr.RECIPE_REGISTRY_DIGEST_LABEL: registry_hash},
                      "Cmd": ["--served-model-name", "served-model"]},
           "State": {"Status": "running", "Running": True, "StartedAt": "2026-10-24T00:00:00Z"},
           "HostConfig": {"PortBindings": {"8000/tcp": [{"HostPort": "39100"}]},
                          "DeviceRequests": [{"DeviceIDs": ["GPU-test-device"]}]}}
    receipt = {"schema": "recipe-monitoring/v1", "state": "active", "generation": "f" * 64,
               "container": "managed-model", "container_id": "d" * 64, "model": "served-model", "engine": "ignis",
               "image_digest": raw["Image"], "recipe_digest": recipe_hash, "registry_digest": registry_hash,
               "bound_port": 39100, "gpu_selection": ["GPU-test-device"], "started_at": raw["State"]["StartedAt"]}
    Path(value["activation"]["monitoring_receipt_path"]).write_text(json.dumps(receipt))
    def runner(argv, **kwargs):
        assert 0 < kwargs["timeout"] <= 8
        assert argv[:2] in (["docker", "ps"], ["docker", "inspect"])
        output = raw["Id"] if argv[1] == "ps" else json.dumps([raw])
        return subprocess.CompletedProcess(argv, 0, output, "")
    return value, raw, runner


def direct_fetch(url, headers):
    assert url == "http://127.0.0.1:39100/v1/models"
    assert headers == {"Accept": "application/json"}
    return 200, b'{"data":[{"id":"served-model","object":"model"}]}'


def test_activation_binds_live_recipe_container_and_monitoring_receipt(activation):
    value, raw, runner = activation
    result = ci.verify_activation(value, runner=runner, fetch=direct_fetch)
    assert result["generation"] == "f" * 64
    assert result["metrics_url"] == "http://127.0.0.1:19100/engine/" + "f" * 64 + "/metrics"
    assert result["container_id"] == raw["Id"]
    assert result["started_at"] == raw["State"]["StartedAt"]
    assert result["served_model"] == "served-model"
    assert len(result["binding_sha256"]) == 64


@pytest.mark.parametrize("field", ["Image", "revision", "gpu", "port", "started_at", "running", "served_model"])
def test_activation_rejects_live_identity_drift(activation, field):
    value, raw, runner = activation
    if field == "Image":
        raw[field] = "sha256:" + "0" * 64
    elif field == "revision":
        raw["Config"]["Labels"][sr.RECIPE_REVISION_LABEL] = "0" * 40
    elif field == "gpu":
        raw["HostConfig"]["DeviceRequests"][0]["DeviceIDs"] = ["GPU-other"]
    elif field == "port":
        raw["HostConfig"]["PortBindings"]["8000/tcp"][0]["HostPort"] = "39101"
    elif field == "started_at":
        raw["State"]["StartedAt"] = "2026-10-24T01:00:00Z"
    elif field == "running":
        raw["State"]["Running"] = False
    else:
        raw["Config"]["Cmd"][-1] = "other-model"
    with pytest.raises(BenchmarkJobError):
        ci.verify_activation(value, runner=runner, fetch=direct_fetch)


@pytest.mark.parametrize("change", ["container", "registry", "receipt"])
def test_activation_rechecks_ownership_after_endpoint_probe(activation, change):
    value, raw, runner = activation
    def fetch(url, headers):
        if change == "container":
            raw["State"]["StartedAt"] = "2026-10-24T01:00:00Z"
        elif change == "registry":
            path = Path(value["activation"]["registry_path"])
            path.write_bytes(path.read_bytes() + b"\n# changed\n")
        else:
            path = Path(value["activation"]["monitoring_receipt_path"])
            receipt = json.loads(path.read_text())
            receipt["generation"] = "e" * 64
            path.write_text(json.dumps(receipt))
        return direct_fetch(url, headers)
    with pytest.raises(BenchmarkJobError, match="changed during"):
        ci.verify_activation(value, runner=runner, fetch=fetch)


@pytest.mark.parametrize("bad_field", [None, "endpoint_sha256", "aliases", "readiness", "served_identity"])
def test_routed_activation_binds_alias_target_and_rejects_malformed_metadata(activation, bad_field):
    value, _, runner = activation
    value["endpoint"] = {"base_url": "http://127.0.0.1:30000/v1", "model": "llm.primary"}
    row = {"model": "served-model", "aliases": ["llm.primary"], "readiness": {"loaded": True},
           "served_identity": {"observed": "served-model"},
           "endpoint_sha256": endpoint_identity_sha256(value["activation"]["upstream_base_url"], "served-model")}
    if bad_field:
        row[bad_field] = None
    def fetch(url, headers):
        assert url == "http://127.0.0.1:30000/v1/models/fingerprints?model=llm.primary"
        return 200, json.dumps({"data": [row]}).encode()
    if bad_field:
        with pytest.raises(BenchmarkJobError, match="fingerprint"):
            ci.verify_activation(value, runner=runner, fetch=fetch)
    else:
        assert ci.verify_activation(value, runner=runner, fetch=fetch)["container_id"] == "d" * 64


@pytest.mark.parametrize("kind", ["symlink", "fifo", "oversized", "malformed"])
def test_activation_receipt_reads_are_bounded_and_regular(tmp_path, kind):
    path = tmp_path / "receipt"
    if kind == "symlink":
        path.symlink_to(tmp_path / "absent")
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "oversized":
        path.write_bytes(b" " * 65537)
    else:
        path.write_text("{")
    with pytest.raises(BenchmarkJobError):
        ci._read_json(path)


def test_activation_refuses_nonloopback_observer(activation):
    value, _, _ = activation
    value["activation"]["observer_base_url"] = "http://192.0.2.10:19100"
    with pytest.raises(BenchmarkJobError, match="literal loopback"):
        ci.validate_activation(value)


@pytest.mark.parametrize("url", ["http://127.0.0.1:19100\r\n", " http://127.0.0.1:19100", "http://user:secret@127.0.0.1:19100"])
def test_activation_refuses_ambiguous_or_credential_bearing_urls(activation, url):
    value, _, _ = activation
    value["activation"]["observer_base_url"] = url
    with pytest.raises(BenchmarkJobError):
        ci.validate_activation(value)


def test_default_discovery_is_bounded_and_does_not_inherit_environment(activation, monkeypatch):
    from anvil_serving import transports
    value, _, runner = activation
    monkeypatch.setenv("DOCKER_HOST", "tcp://192.0.2.10:2375")
    calls = []
    def bounded(argv, **kwargs):
        calls.append(kwargs)
        result = runner(argv, timeout=kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, result.stdout.encode(), b"")
    monkeypatch.setattr(transports, "_run_bounded_process", bounded)
    ci.verify_activation(value, fetch=direct_fetch)
    assert len(calls) == 4
    assert all(call["env"] == {"PATH": os.defpath} and call["max_output_bytes"] == 4 * 1024 * 1024 for call in calls)

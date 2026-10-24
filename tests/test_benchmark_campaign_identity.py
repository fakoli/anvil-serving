"""Activation checks use real registry and managed Docker record parsing."""
import hashlib
import http.client
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
           "HostConfig": {"PortBindings": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "39100"}]},
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


@pytest.mark.parametrize("change", ["container", "registry", "receipt", "binding"])
def test_activation_rechecks_ownership_after_endpoint_probe(activation, change):
    value, raw, runner = activation
    def fetch(url, headers):
        if change == "container":
            raw["State"]["StartedAt"] = "2026-10-24T01:00:00Z"
        elif change == "binding":
            raw["HostConfig"]["PortBindings"]["8000/tcp"][0]["HostIp"] = "127.0.0.2"
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


def test_activation_refuses_nonlocal_observer_before_metadata(activation, monkeypatch):
    value, _, runner = activation
    value['activation']['observer_base_url'] = 'http://192.0.2.10:19100'
    ci.validate_activation(value)  # Pure planning validates the literal address; execution proves ownership.
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def bind(self, address): raise OSError('address not assigned')
    monkeypatch.setattr(ci.socket, 'socket', lambda *args: Socket())
    with pytest.raises(BenchmarkJobError, match='not owned'):
        ci.verify_activation(value, runner=runner, fetch=lambda *args: pytest.fail('nonlocal request'))


def test_activation_accepts_proven_same_host_bridge_observer(activation, monkeypatch):
    value, _, runner = activation
    value['activation']['observer_base_url'] = 'http://192.0.2.10:19100'
    bindings = []
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def bind(self, address): bindings.append(address)
    monkeypatch.setattr(ci.socket, 'socket', lambda *args: Socket())
    result = ci.verify_activation(value, runner=runner, fetch=direct_fetch)
    assert bindings[0] == ('192.0.2.10', 0)
    assert result['metrics_url'].startswith('http://192.0.2.10:19100/engine/')


@pytest.mark.parametrize('host', ['8.8.8.8', '0.0.0.0', '169.254.1.1', '224.0.0.1', 'observer.example.test'])
def test_activation_rejects_nonlocal_address_classes_without_network(activation, host):
    value, _, _ = activation
    value['activation']['observer_base_url'] = 'http://' + host + ':19100'
    with pytest.raises(BenchmarkJobError): ci.validate_activation(value)


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


@pytest.mark.parametrize("host_ip", [None, "", "0.0.0.0", "127.0.0.2"])
def test_activation_refuses_unproven_or_different_host_binding(activation, host_ip):
    value, raw, runner = activation
    raw["HostConfig"]["PortBindings"]["8000/tcp"][0]["HostIp"] = host_ip
    def forbidden_fetch(*args):
        pytest.fail("unowned endpoint must not receive metadata requests")
    with pytest.raises(BenchmarkJobError, match="managed container binding"):
        ci.verify_activation(value, runner=runner, fetch=forbidden_fetch)


def test_same_port_on_other_local_address_cannot_impersonate_managed_endpoint(activation):
    value, _, runner = activation
    value["activation"]["upstream_base_url"] = "http://127.0.0.2:39100/v1"
    value["endpoint"]["base_url"] = "http://127.0.0.1:30000/v1"
    with pytest.raises(BenchmarkJobError, match="managed container binding"):
        ci.verify_activation(value, runner=runner, fetch=lambda *_: pytest.fail("must reject before probe"))


def test_metadata_endpoint_refuses_unbounded_dns_resolution(activation):
    value, _, _ = activation
    value["endpoint"]["base_url"] = "http://router.example.test/v1"
    with pytest.raises(BenchmarkJobError, match="metadata endpoint must use a literal"):
        ci.validate_activation(value)


@pytest.mark.parametrize("result", [(200, b'{"data":[]}'), (302, b'{}'), (200, b' ' * 65537), TimeoutError(), http.client.IncompleteRead(b'')])
def test_activation_uses_shared_absolute_deadline_reader(monkeypatch, result):
    from anvil_serving.router import model_capacity
    def bounded(url, headers, *, timeout, max_bytes):
        assert url == "http://127.0.0.1:39100/v1/models"
        assert headers == {"Accept": "application/json"}
        assert timeout == 0.2 and max_bytes == 65536
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(model_capacity, "_default_metrics_read", bounded)
    if result == (200, b'{"data":[]}'):
        assert ci._fetch_json("http://127.0.0.1:39100/v1/models", headers={"Accept": "application/json"}, timeout=0.2) == {"data": []}
    else:
        with pytest.raises(BenchmarkJobError, match="metadata is unavailable"):
            ci._fetch_json("http://127.0.0.1:39100/v1/models", headers={"Accept": "application/json"}, timeout=0.2)

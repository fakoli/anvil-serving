"""Activation checks use real registry and managed Docker record parsing."""
import hashlib
import http.client
import json
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


@pytest.mark.parametrize("url", ["http://127.0.0.1:19100\r\n", " http://127.0.0.1:19100", "http://user:secret@127.0.0.1:19100"])
def test_activation_refuses_ambiguous_or_credential_bearing_urls(activation, url):
    value, _, _ = activation
    value["activation"]["observer_base_url"] = url
    with pytest.raises(BenchmarkJobError):
        ci.validate_activation(value)


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

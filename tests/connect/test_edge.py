"""Cloudflare edge publishing tests.

Every Cloudflare interaction is injected as a scripted opener, so the review
behavior (plan merge, ownership split, idempotence, credential hygiene) is
proven without network access or real tokens.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
import sys

import pytest

from anvil_serving.connect import edge
from anvil_serving.connect.edge import EdgeConfig, EdgeError, edge_config

# The Connect manifest reader requires POSIX no-follow descriptors; the
# plan and apply flows run on POSIX only, like the rest of the family.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="the Connect manifest reader requires POSIX no-follow descriptors",
)

ROOT = Path(__file__).resolve().parents[2]
ZONE_ID = "023e105f4ecef8ad9ca31a8372d0c354"
TUNNEL_ID = "f1ba9e58-8f7b-4d6c-9a21-c0ffee424242"
ACCOUNT = "023e105f4ecef8ad9ca31a8372d0c353"


def _config(tmp_path: Path, **overrides: object) -> EdgeConfig:
    value: dict[str, object] = {
        "schema": "anvil-connect.edge-publishing/v1",
        "account_id": ACCOUNT,
        "zone_name": "example.test",
        "tunnel_id": TUNNEL_ID,
        "ca_pool": str(tmp_path / "ca.pem"),
        "origin_server_name": "connect.example.test",
    }
    value.update(overrides)
    return edge_config(value)


def _manifest(tmp_path: Path, hosts: tuple[str, ...]) -> str:
    value = json.loads((ROOT / "connect/examples/deployment.json").read_text())
    connector_id = value["connectors"][0]["id"]
    value["clients"] = []
    if isinstance(value.get("environment_files"), dict):
        value["environment_files"]["clients"] = {}
    value["connectors"][0]["resources"] = []
    value["gateway"]["gateway"]["resources"] = []
    for i, host in enumerate(hosts):
        rule = {
            "id": f"r{i}", "host": host, "path_prefix": "/",
            "methods": ["GET", "POST"], "access": "browser", "native_auth": "passthrough",
            "limits": {"request_bytes": 1048576, "concurrent": 8, "buffer_bytes": 65536,
                        "idle_seconds": 30, "duration_seconds": 300},
        }
        value["connectors"][0]["resources"].append({
            "envelope": {
                "rule": rule,
                "listen": f"127.0.0.1:{18090 + i}",
                "origin_url": "http://127.0.0.1:19000",
                "token_env": "",
            },
            "reverse_address": f"127.0.0.1:{17010 + i}",
        })
        value["gateway"]["gateway"]["resources"].append({
            "connector": connector_id,
            "rule": rule,
            "tunnel_address": f"127.0.0.1:{17010 + i}",
        })
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return str(path)


class ScriptedCloudflare:
    """Record API calls and answer from a mutable state table."""

    def __init__(self, *, ingress: list[dict], dns: dict[str, dict] | None = None, tunnel_status: str = "connected") -> None:
        self.calls: list[tuple[str, str]] = []
        self.ingress = ingress
        self.dns = dns if dns is not None else {}
        self.tunnel_status = tunnel_status
        self.counter = 0

    def __call__(self, url: str, method: str, body: bytes | None, token: str) -> object:
        self.calls.append((method, url.split("?")[0]))
        if url.endswith("/zones?name=example.test"):
            return {"success": True, "result": [{"id": ZONE_ID, "name": "example.test"}]}
        if "/cfd_tunnel/" in url and url.endswith(f"/cfd_tunnel/{TUNNEL_ID}"):
            return {"success": True, "result": {"status": self.tunnel_status, "connections": [1] if self.tunnel_status == "connected" else []}}
        if url.endswith("/configurations") and method == "GET":
            return {"success": True, "result": {"ingress": self.ingress}}
        if url.endswith("/configurations") and method == "PUT":
            self.ingress = json.loads(body)["config"]["ingress"]  # type: ignore[union-attr]
            return {"success": True, "result": self.ingress}
        if "/dns_records" in url and method == "GET":
            from urllib.parse import parse_qs, urlparse
            query = parse_qs(urlparse(url).query)
            wanted_name = query.get("name", [None])[0]
            wanted_content = query.get("content", [None])[0]
            matches = [
                record for record in self.dns.values()
                if (wanted_name is None or record.get("name") == wanted_name)
                and (wanted_content is None or record.get("content") == wanted_content)
            ]
            return {"success": True, "result": matches}
        if "/dns_records" in url and method == "POST":
            payload = json.loads(body)  # type: ignore[union-attr]
            self.counter += 1
            self.dns[payload["name"]] = {"id": f"rec{self.counter}", **payload}
            return {"success": True, "result": self.dns[payload["name"]]}
        if "/dns_records/" in url and method == "DELETE":
            record_id = url.rsplit("/", 1)[1]
            for name in [name for name, record in self.dns.items() if record.get("id") == record_id]:
                del self.dns[name]
            return {"success": True, "result": {"id": record_id}}
        if "/dns_records/" in url and method == "PUT":
            payload = json.loads(body)  # type: ignore[union-attr]
            record_id = url.rsplit("/", 1)[1]
            self.dns[payload["name"]] = {"id": record_id, **payload}
            return {"success": True, "result": self.dns[payload["name"]]}
        raise AssertionError(f"unrouted call {method} {url}")


@pytest.fixture(autouse=True)
def _api_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANVIL_CLOUDFLARE_API_TOKEN", "tok")


# --- configuration parsing --------------------------------------------------


def test_config_defaults_and_strict_fields(tmp_path: Path) -> None:
    parsed = edge_config({
        "account_id": ACCOUNT, "zone_name": "example.test",
        "tunnel_id": TUNNEL_ID, "ca_pool": str(tmp_path / "ca.pem"),
    })
    assert parsed.api_token_env == "ANVIL_CLOUDFLARE_API_TOKEN"
    assert parsed.origin_service == "https://127.0.0.1:19443"
    assert parsed.origin_server_name is None


@pytest.mark.parametrize("mutation", [
    {"account_id": "short"},
    {"zone_name": "example.test:8080"},
    {"zone_name": "*.example.test"},
    {"tunnel_id": "not-a-uuid"},
    {"ca_pool": "relative/ca.pem"},
    {"api_token_env": "lower-not-env"},
    {"origin_service": "https://0.0.0.0:443"},
    {"origin_server_name": "*.example.test"},
    {"unknown_field": True},
])
def test_config_rejects_unsafe_fields(tmp_path: Path, mutation: dict[str, object]) -> None:
    base: dict[str, object] = {
        "account_id": ACCOUNT, "zone_name": "example.test",
        "tunnel_id": TUNNEL_ID, "ca_pool": str(tmp_path / "ca.pem"),
    }
    base.update(mutation)
    with pytest.raises(EdgeError):
        edge_config(base)


def test_load_config_requires_the_private_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path / "empty"))
    with pytest.raises(EdgeError, match="edge-cloudflare.json"):
        edge.load_config()


def test_published_hosts_are_sorted_and_deduplicated(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, ("b.example.test", "a.example.test", "b.example.test"))
    assert edge.published_hosts(json.loads(Path(manifest).read_text())) == ("a.example.test", "b.example.test")


def test_plan_rejects_hosts_outside_the_zone(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manifest = _manifest(tmp_path, ("elsewhere.example.org",))
    with pytest.raises(EdgeError, match="outside the configured zone"):
        edge.plan(config, manifest, fetch=ScriptedCloudflare(ingress=[]))


# --- plan and apply behavior ------------------------------------------------


def test_plan_reports_missing_dns_and_changed_ingress(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manifest = _manifest(tmp_path, ("a.example.test", "b.example.test"))
    api = ScriptedCloudflare(ingress=[{"service": "http_status:404"}])
    report = edge.plan(config, manifest, fetch=api)
    assert report["dns"] == {"a.example.test": "create", "b.example.test": "create"}
    assert report["ingress"]["managed_hosts"] == ["a.example.test", "b.example.test"]
    assert report["ingress"]["changed"] is True
    assert report["tunnel"]["status"] == "connected"


def test_plan_preserves_foreign_rules_and_catch_all_last(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manifest = _manifest(tmp_path, ("a.example.test",))
    legacy = {"hostname": "old.example.test", "service": "http://localhost:8446"}
    api = ScriptedCloudflare(ingress=[legacy, {"service": "http_status:503"}])
    result = edge.apply(config, manifest, confirm=True, fetch=api)
    merged = api.ingress
    assert merged[0]["hostname"] == "a.example.test"  # managed rules first
    assert merged[1] == legacy  # foreign rule preserved
    assert merged[-1] == {"service": "http_status:503"}  # existing catch-all stays last
    assert merged[0]["service"] == "https://127.0.0.1:19443"
    assert result["applied"] == ["tunnel_ingress", "dns:a.example.test"]


def test_apply_is_idempotent_on_repetition(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manifest = _manifest(tmp_path, ("a.example.test",))
    api = ScriptedCloudflare(ingress=[{"service": "http_status:404"}])
    edge.apply(config, manifest, confirm=True, fetch=api)
    writes_after_first = sum(1 for method, _ in api.calls if method in {"PUT", "POST"})
    second = edge.apply(config, manifest, confirm=True, fetch=api)
    assert second["converged"] is True and second["applied"] == []
    assert sum(1 for method, _ in api.calls if method in {"PUT", "POST"}) == writes_after_first


def test_apply_without_confirm_is_a_read_only_plan(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manifest = _manifest(tmp_path, ("a.example.test",))
    api = ScriptedCloudflare(ingress=[])
    result = edge.apply(config, manifest, confirm=False, fetch=api)
    assert result["dry_run"] is True
    assert not any(method in {"PUT", "POST"} for method, _ in api.calls)


def test_apply_fails_closed_when_the_tunnel_has_no_connector(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manifest = _manifest(tmp_path, ("a.example.test",))
    api = ScriptedCloudflare(ingress=[], tunnel_status="down")
    with pytest.raises(EdgeError, match="active connector"):
        edge.apply(config, manifest, confirm=True, fetch=api)


def test_dns_updates_existing_record_with_wrong_target(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manifest = _manifest(tmp_path, ("a.example.test",))
    api = ScriptedCloudflare(ingress=[], dns={
        "a.example.test": {"id": "rec1", "name": "a.example.test", "content": "old.example.test", "proxied": False},
    })
    result = edge.apply(config, manifest, confirm=True, fetch=api)
    assert "dns:a.example.test" in result["applied"]
    assert api.dns["a.example.test"]["content"] == f"{TUNNEL_ID}.cfargotunnel.com"
    assert api.dns["a.example.test"]["proxied"] is True


def test_ingress_rule_carries_the_operator_pki(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manifest = _manifest(tmp_path, ("a.example.test",))
    api = ScriptedCloudflare(ingress=[])
    edge.apply(config, manifest, confirm=True, fetch=api)
    rule = api.ingress[0]
    assert rule["originRequest"]["caPool"].endswith("ca.pem")
    assert rule["originRequest"]["originServerName"] == "connect.example.test"
    assert rule["originRequest"]["noTLSVerify"] is False


# --- credential hygiene -----------------------------------------------------


def test_missing_token_is_an_actionable_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ANVIL_CLOUDFLARE_API_TOKEN", raising=False)
    config = _config(tmp_path)
    manifest = _manifest(tmp_path, ("a.example.test",))
    with pytest.raises(EdgeError, match="ANVIL_CLOUDFLARE_API_TOKEN"):
        edge.plan(config, manifest, fetch=ScriptedCloudflare(ingress=[]))


def test_cloudflare_rejection_surfaces_error_codes_without_the_token(tmp_path: Path) -> None:
    config = _config(tmp_path)

    def rejecting(url: str, method: str, body: bytes | None, token: str) -> object:
        assert token == "tok"
        return {"success": False, "errors": [{"code": 7003, "message": "no zone"}]}

    manifest = _manifest(tmp_path, ("a.example.test",))
    with pytest.raises(EdgeError, match="7003"):
        edge.plan(config, manifest, fetch=rejecting)


def test_config_carries_only_the_token_env_reference(tmp_path: Path) -> None:
    config = _config(tmp_path)
    names = {item.name for item in dataclasses.fields(config)}
    assert not names & {"token", "api_token", "secret", "key"}
    assert config.api_token_env == "ANVIL_CLOUDFLARE_API_TOKEN"

# --- Greptile finding 1: withdrawn hosts must be reported and retired --------


def test_plan_reports_withdrawn_hosts_as_orphans(tmp_path: Path) -> None:
    config = _config(tmp_path)
    old_manifest = _manifest(tmp_path, ("a.example.test", "old.example.test"))
    api = ScriptedCloudflare(ingress=[])
    edge.apply(config, old_manifest, confirm=True, fetch=api)
    renamed_manifest = _manifest(tmp_path, ("a.example.test", "b.example.test"))
    report = edge.plan(config, renamed_manifest, fetch=api)
    assert report["orphans"]["ingress_hosts"] == ["old.example.test"]
    assert report["orphans"]["dns_hosts"] == ["old.example.test"]
    assert report["orphans"]["retire_with"].endswith("--retire-orphans --confirm")


def test_withdrawn_host_keeps_routing_until_deliberate_retirement(tmp_path: Path) -> None:
    config = _config(tmp_path)
    old_manifest = _manifest(tmp_path, ("a.example.test", "old.example.test"))
    api = ScriptedCloudflare(ingress=[])
    edge.apply(config, old_manifest, confirm=True, fetch=api)
    renamed_manifest = _manifest(tmp_path, ("a.example.test", "b.example.test"))
    result = edge.apply(config, renamed_manifest, confirm=True, fetch=api)
    assert result["orphans_retired"] is False
    routes = [rule.get("hostname") for rule in api.ingress if isinstance(rule, dict)]
    assert "old.example.test" in routes  # still routed by default
    assert api.dns["old.example.test"]["content"].endswith("cfargotunnel.com")
    retired = edge.apply(config, renamed_manifest, confirm=True, retire_orphans=True, fetch=api)
    assert "ingress_orphans_retired" in retired["applied"]
    assert "dns_orphan_deleted:old.example.test" in retired["applied"]
    assert "old.example.test" not in [rule.get("hostname") for rule in api.ingress if isinstance(rule, dict)]
    assert "old.example.test" not in api.dns


def test_retirement_never_touches_foreign_service_rules(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manifest = _manifest(tmp_path, ("a.example.test",))
    legacy = {"hostname": "legacy.example.test", "service": "http://localhost:8446"}
    api = ScriptedCloudflare(ingress=[legacy], dns={
        "legacy.example.test": {"id": "rec9", "name": "legacy.example.test",
                                 "content": "someone-elses-tunnel.cfargotunnel.com", "proxied": True},
    })
    edge.apply(config, manifest, confirm=True, retire_orphans=True, fetch=api)
    assert legacy in api.ingress  # different origin service: not ours
    assert api.dns["legacy.example.test"]["content"].startswith("someone-elses-tunnel")


def test_orphan_ownership_requires_zone_membership(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manifest = _manifest(tmp_path, ("a.example.test",))
    foreign_zone = {"hostname": "host.other-zone.example", "service": config.origin_service}
    api = ScriptedCloudflare(ingress=[foreign_zone])
    report = edge.plan(config, manifest, fetch=api)
    assert report["orphans"]["ingress_hosts"] == []  # outside the zone: never ours


# --- Greptile finding 2: partial mutations must be reported on failure -------


def test_dns_failure_reports_completed_tunnel_write(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manifest = _manifest(tmp_path, ("a.example.test",))

    class TunnelOkDnsFail(ScriptedCloudflare):
        def __call__(self, url: str, method: str, body: bytes | None, token: str) -> object:
            if "/dns_records" in url and method == "POST":
                raise OSError("dns write refused")
            return super().__call__(url, method, body, token)

    api = TunnelOkDnsFail(ingress=[])
    with pytest.raises(EdgeError) as raised:
        edge.apply(config, manifest, confirm=True, fetch=api)
    assert raised.value.applied == ("tunnel_ingress",)
    assert raised.value.failed_step == "edge apply"
    assert "tunnel_ingress" in str(raised.value)


def test_connector_failure_reports_all_completed_mutations(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manifest = _manifest(tmp_path, ("a.example.test",))
    api = ScriptedCloudflare(ingress=[], tunnel_status="down")
    with pytest.raises(EdgeError) as raised:
        edge.apply(config, manifest, confirm=True, fetch=api)
    assert raised.value.failed_step == "connector_verification"
    assert set(raised.value.applied) == {"tunnel_ingress", "dns:a.example.test"}

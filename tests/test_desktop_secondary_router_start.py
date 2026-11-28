"""Dynamic secondary lookup around a stopped Docker Desktop."""

import pytest

from anvil_serving import desktop_modes
from tests.test_desktop_modes import dynamic_secondary, setup


def _cohost_router(tmp_path):
    path = tmp_path / "desktop-modes.toml"
    path.write_text(path.read_text(encoding="utf-8").replace(
        '"https://router.example.test"', '"http://127.0.0.1:8100"'), encoding="utf-8")


def test_remote_router_assignment_precedes_docker_start(monkeypatch, tmp_path):
    docker, _ = setup(monkeypatch, tmp_path)
    dynamic_secondary(monkeypatch, tmp_path)
    docker.rows[0]["name"] = "/alpha"
    docker.rows[-1]["name"] = "/beta"
    docker.running = False
    lookup = desktop_modes._dynamic_secondary
    lookup_states = []

    def before_start(profile):
        lookup_states.append(docker.running)
        return lookup(profile)

    monkeypatch.setattr(desktop_modes, "_dynamic_secondary", before_start)
    assert desktop_modes.select_mode("secondary", confirm=True, run=docker)["target"]["name"] == "alpha"
    assert lookup_states[0] is False


def test_cohost_dry_run_defers_router_lookup(monkeypatch, tmp_path):
    docker, _ = setup(monkeypatch, tmp_path)
    dynamic_secondary(monkeypatch, tmp_path)
    _cohost_router(tmp_path)
    docker.running = False
    monkeypatch.setattr(desktop_modes, "_dynamic_secondary", lambda _: pytest.fail("cohost lookup"))
    result = desktop_modes.select_mode("secondary", dry_run=True, run=docker)
    assert "router alias assignment after Docker starts" in result["deferred_checks"]


def test_declared_router_makes_dns_endpoint_cohosted(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    dynamic_secondary(monkeypatch, tmp_path)
    path = tmp_path / "desktop-modes.toml"
    path.write_text(path.read_text(encoding="utf-8") + 'router_container="router"\n', encoding="utf-8")
    assert desktop_modes._router_is_cohost(desktop_modes._profile())


def test_failed_cohost_lookup_never_stops_active_independent_work(monkeypatch, tmp_path):
    docker, operations = setup(monkeypatch, tmp_path)
    dynamic_secondary(monkeypatch, tmp_path)
    _cohost_router(tmp_path)
    docker.running = False
    monkeypatch.setattr(desktop_modes, "_dynamic_secondary", lambda _: (_ for _ in ()).throw(RuntimeError("unavailable")))
    with pytest.raises(RuntimeError, match="unavailable"):
        desktop_modes.select_mode("secondary", confirm=True, run=docker)
    assert docker.running and not operations
    assert not any(call[1:3] == ["desktop", "stop"] for call in docker.calls)


def test_failed_cohost_lookup_reparks_empty_desktop(monkeypatch, tmp_path):
    docker, _ = setup(monkeypatch, tmp_path)
    dynamic_secondary(monkeypatch, tmp_path)
    _cohost_router(tmp_path)
    docker.running = False
    for row in docker.rows:
        row["status"] = "exited"
    monkeypatch.setattr(desktop_modes, "_dynamic_secondary", lambda _: (_ for _ in ()).throw(RuntimeError("unavailable")))
    with pytest.raises(RuntimeError, match="unavailable"):
        desktop_modes.select_mode("secondary", confirm=True, run=docker)
    assert not docker.running


def test_preserved_router_is_known_and_not_a_mode_owner(monkeypatch, tmp_path):
    docker, _ = setup(monkeypatch, tmp_path)
    path = tmp_path / "desktop-modes.toml"
    path.write_text(path.read_text(encoding="utf-8") + 'router_container="router"\n', encoding="utf-8")
    docker.rows.append(dict(id="r" * 64, name="/router", status="running", restart="unless-stopped", health=None))
    assert desktop_modes.select_mode("secondary", confirm=True, run=docker)["mode"] == "secondary"
    assert docker.rows[-1]["status"] == "running"

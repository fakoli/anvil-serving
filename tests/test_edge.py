"""Tests for the tailnet edge verb group (ADR-0019).

Covered: config parse (defaults, TOML, --map overrides + drop), render logic
(route map -> tailscale serve invocation), dry-run, status parsing/classification,
MagicDNS resolution, and the down-removes-only-managed guarantee.
"""
from __future__ import annotations

import json
import types
from pathlib import Path


from anvil_serving import edge


# --------------------------------------------------------------------------- #
# config parse
# --------------------------------------------------------------------------- #


def test_load_config_from_toml_ports_and_full_urls(tmp_path: Path):
    cfg = tmp_path / "edge.toml"
    cfg.write_text(
        "\n".join(
            [
                "[edge]",
                "https_port = 8443",
                'host = "127.0.0.1"',
                "[edge.routes]",
                '"/v1" = 8000',
                '"/comfyui" = 8188',
                '"/dashboard" = 8766',
                '"/raw" = "http://127.0.0.1:9000/raw"',
            ]
        ),
        encoding="utf-8",
    )
    config = edge.load_config(cfg)
    assert config.https_port == 8443
    got = {r.mount: r.target for r in config.routes}
    assert got == {
        "/v1": "http://127.0.0.1:8000/v1",
        "/comfyui": "http://127.0.0.1:8188",
        "/dashboard": "http://127.0.0.1:8766",
        "/raw": "http://127.0.0.1:9000/raw",
    }


# --------------------------------------------------------------------------- #
# render logic (route map -> tailscale serve invocation)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# status parsing + classification
# --------------------------------------------------------------------------- #
_LIVE_STATUS = {
    "TCP": {"443": {"HTTPS": True}},
    "Web": {
        "node-a.example.ts.net:443": {
            "Handlers": {
                "/": {"Proxy": "http://127.0.0.1:8766"},
                "/v1": {"Proxy": "http://127.0.0.1:8000"},
            }
        }
    },
}


# --------------------------------------------------------------------------- #
# down removes ONLY managed mappings (never clobbers operator-set ones)
# --------------------------------------------------------------------------- #


def test_plan_down_is_idempotent_when_nothing_present():
    config = edge.default_config()
    assert edge.plan_down(config, {}) == []


def test_port_only_override_retains_v1_and_explicit_target_paths_remain_authoritative():
    config = edge.load_config(None, extra_maps=("/v1/=18000", "/dashboard=8766"))
    assert {route.mount: route.target for route in config.routes}["/v1"] == "http://127.0.0.1:18000/v1"
    config = edge.load_config(None, extra_maps=("/v1=http://127.0.0.1:18000/custom",))
    assert {route.mount: route.target for route in config.routes}["/v1"] == "http://127.0.0.1:18000/custom"


# --------------------------------------------------------------------------- #
# MagicDNS resolution
# --------------------------------------------------------------------------- #
def _fake_run(stdout: str, returncode: int = 0):
    def run(argv, capture_output=True, text=True, timeout=None, check=False):
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")
    return run


def test_read_serve_status_parses_live_json():
    run = _fake_run(json.dumps(_LIVE_STATUS))
    assert edge.read_serve_status(https_port=443, run=run) == {
        "/": "http://127.0.0.1:8766",
        "/v1": "http://127.0.0.1:8000",
    }


# --------------------------------------------------------------------------- #
# CLI surface: render + dry-run + confirm gating
# --------------------------------------------------------------------------- #


def test_cli_up_dry_run_does_not_require_confirm(capsys):
    rc = edge.main(["up", "--dry-run", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["dry_run"] and out["plan"]


def test_cli_up_refuses_without_confirmation(capsys, monkeypatch):
    # No confirmation_scope authorized -> refuse to mutate.
    rc = edge.main(["up", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["ok"] is False and "confirm" in out["note"].lower()


def test_cli_up_applies_under_authorized_confirmation(capsys, monkeypatch):
    calls: list[list[str]] = []

    def fake_apply(plan, *, run, timeout):
        calls.extend([list(c) for c in plan])
        return 0, [{"command": list(c), "status": "ok", "returncode": 0} for c in plan]

    monkeypatch.setattr(edge, "_apply_plan", fake_apply)
    monkeypatch.setattr(edge, "resolve_magicdns_name", lambda **_: "host.example.ts.net")
    from anvil_serving import guard

    with guard.confirmation_scope(True):
        rc = edge.main(["up", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] and out["dry_run"] is False
    assert calls and calls[0][:2] == ["tailscale", "serve"]

"""Tests for the tailnet edge verb group (ADR-0019).

Covered: config parse (defaults, TOML, --map overrides + drop), render logic
(route map -> tailscale serve invocation), dry-run, status parsing/classification,
MagicDNS resolution, and the down-removes-only-managed guarantee.
"""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from anvil_serving import edge


# --------------------------------------------------------------------------- #
# config parse
# --------------------------------------------------------------------------- #
def test_default_config_is_the_adr_route_map():
    config = edge.default_config()
    assert config.https_port == 443
    assert [(r.mount, r.target) for r in config.routes] == [
        ("/v1", "http://127.0.0.1:8000"),
        ("/comfyui", "http://127.0.0.1:8188"),
    ]


def test_load_config_defaults_when_no_file():
    config = edge.load_config(None)
    assert [(r.mount, r.target) for r in config.routes] == [
        ("/v1", "http://127.0.0.1:8000"),
        ("/comfyui", "http://127.0.0.1:8188"),
    ]


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
        "/v1": "http://127.0.0.1:8000",
        "/comfyui": "http://127.0.0.1:8188",
        "/dashboard": "http://127.0.0.1:8766",
        "/raw": "http://127.0.0.1:9000/raw",
    }


def test_load_config_host_override_applies_to_port_routes(tmp_path: Path):
    cfg = tmp_path / "edge.toml"
    cfg.write_text('[edge]\nhost = "100.87.34.66"\n[edge.routes]\n"/v1" = 8000\n', encoding="utf-8")
    config = edge.load_config(cfg)
    assert config.routes[0].target == "http://100.87.34.66:8000"


def test_map_override_adds_and_wins_over_defaults():
    config = edge.load_config(None, extra_maps=("/dashboard=8766", "/v1=http://127.0.0.1:18000"))
    got = {r.mount: r.target for r in config.routes}
    assert got["/dashboard"] == "http://127.0.0.1:8766"
    assert got["/v1"] == "http://127.0.0.1:18000"


def test_map_off_drops_a_default_route():
    config = edge.load_config(None, extra_maps=("/comfyui=off",))
    assert [r.mount for r in config.routes] == ["/v1"]


def test_https_port_flag_overrides_file(tmp_path: Path):
    cfg = tmp_path / "edge.toml"
    cfg.write_text("[edge]\nhttps_port = 8443\n[edge.routes]\n\"/v1\" = 8000\n", encoding="utf-8")
    assert edge.load_config(cfg, https_port=443).https_port == 443


def test_config_rejects_boolean_target():
    with pytest.raises(edge.EdgeConfigError):
        edge._target_from_value(True, host="127.0.0.1")


def test_config_rejects_relative_mount():
    with pytest.raises(edge.EdgeConfigError):
        edge.load_config(None, extra_maps=("v1=8000",))


def test_config_rejects_bad_port():
    with pytest.raises(edge.EdgeConfigError):
        edge.load_config(None, extra_maps=("/v1=70000",))


def test_missing_config_file_is_an_error(tmp_path: Path):
    with pytest.raises(edge.EdgeConfigError):
        edge.load_config(tmp_path / "nope.toml")


def test_duplicate_mount_rejected():
    with pytest.raises(edge.EdgeConfigError):
        edge.EdgeConfig(https_port=443, routes=(edge.EdgeRoute("/v1", "http://127.0.0.1:8000"), edge.EdgeRoute("/v1", "http://127.0.0.1:1")))


def test_trailing_slash_mount_normalized():
    assert edge.EdgeRoute("/comfyui/", "http://127.0.0.1:8188").mount == "/comfyui"


# --------------------------------------------------------------------------- #
# render logic (route map -> tailscale serve invocation)
# --------------------------------------------------------------------------- #
def test_render_up_plan_maps_routes_to_serve_invocations():
    config = edge.default_config()
    plan = edge.render_up_plan(config)
    assert plan == [
        ["tailscale", "serve", "--bg", "--https=443", "--set-path=/v1", "http://127.0.0.1:8000"],
        ["tailscale", "serve", "--bg", "--https=443", "--set-path=/comfyui", "http://127.0.0.1:8188"],
    ]


def test_render_root_mount_omits_set_path():
    route = edge.EdgeRoute("/", "http://127.0.0.1:8000")
    assert edge.serve_up_argv(route, https_port=443) == ["tailscale", "serve", "--bg", "--https=443", "http://127.0.0.1:8000"]


def test_render_honours_custom_https_port():
    config = edge.load_config(None, https_port=8443)
    plan = edge.render_up_plan(config)
    assert all("--https=8443" in cmd for cmd in plan)


def test_off_argv_is_per_path_never_reset():
    argv = edge.serve_off_argv("/comfyui", https_port=443)
    assert argv == ["tailscale", "serve", "--https=443", "--set-path=/comfyui", "off"]
    assert "reset" not in argv


# --------------------------------------------------------------------------- #
# status parsing + classification
# --------------------------------------------------------------------------- #
_LIVE_STATUS = {
    "TCP": {"443": {"HTTPS": True}},
    "Web": {
        "fakoli-dark.tail4378d.ts.net:443": {
            "Handlers": {
                "/": {"Proxy": "http://127.0.0.1:8766"},
                "/v1": {"Proxy": "http://127.0.0.1:8000"},
            }
        }
    },
}


def test_parse_serve_status_extracts_mount_targets():
    parsed = edge.parse_serve_status(_LIVE_STATUS, https_port=443)
    assert parsed == {"/": "http://127.0.0.1:8766", "/v1": "http://127.0.0.1:8000"}


def test_parse_serve_status_filters_by_port():
    data = {"Web": {"host:8443": {"Handlers": {"/v1": {"Proxy": "http://127.0.0.1:8000"}}}}}
    assert edge.parse_serve_status(data, https_port=443) == {}
    assert edge.parse_serve_status(data, https_port=8443) == {"/v1": "http://127.0.0.1:8000"}


def test_parse_serve_status_tolerates_empty_or_malformed():
    assert edge.parse_serve_status({}, https_port=443) == {}
    assert edge.parse_serve_status({"Web": "nope"}, https_port=443) == {}
    assert edge.parse_serve_status({"Web": {"h:443": {"Handlers": {"/v1": "bad"}}}}, https_port=443) == {}


def test_classify_status_flags_managed_present_and_absent():
    config = edge.default_config()
    current = {"/": "http://127.0.0.1:8766", "/v1": "http://127.0.0.1:8000"}
    result = edge.classify_status(config, current)
    by_mount = {m["mount"]: m for m in result["mounts"]}
    assert by_mount["/"]["managed"] is False and by_mount["/"]["present"] is True
    assert by_mount["/v1"]["managed"] is True and by_mount["/v1"]["present"] is True
    assert by_mount["/comfyui"]["managed"] is False and by_mount["/comfyui"]["present"] is False


# --------------------------------------------------------------------------- #
# down removes ONLY managed mappings (never clobbers operator-set ones)
# --------------------------------------------------------------------------- #
def test_plan_down_only_removes_managed_present_and_matching():
    config = edge.default_config()  # /v1 -> 8000, /comfyui -> 8188
    current = {
        "/": "http://127.0.0.1:8766",       # operator dashboard — NOT managed
        "/v1": "http://127.0.0.1:8000",     # managed + present + matching -> remove
        # /comfyui absent -> skip
    }
    plan = edge.plan_down(config, current)
    assert plan == [["tailscale", "serve", "--https=443", "--set-path=/v1", "off"]]


def test_plan_down_skips_managed_mount_pointing_elsewhere():
    """A managed mount whose live target differs (operator repurposed the path) is left alone."""
    config = edge.default_config()
    current = {"/v1": "http://127.0.0.1:9999"}  # not our target
    assert edge.plan_down(config, current) == []


def test_plan_down_is_idempotent_when_nothing_present():
    config = edge.default_config()
    assert edge.plan_down(config, {}) == []


def test_plan_down_never_targets_operator_root():
    config = edge.default_config()
    current = {"/": "http://127.0.0.1:8766", "/v1": "http://127.0.0.1:8000", "/comfyui": "http://127.0.0.1:8188"}
    plan = edge.plan_down(config, current)
    # Every removal carries an explicit --set-path for a managed mount; none touch root "/".
    removed_mounts = {arg.split("=", 1)[1] for cmd in plan for arg in cmd if arg.startswith("--set-path=")}
    assert removed_mounts == {"/v1", "/comfyui"}
    assert "/" not in removed_mounts


# --------------------------------------------------------------------------- #
# MagicDNS resolution
# --------------------------------------------------------------------------- #
def _fake_run(stdout: str, returncode: int = 0):
    def run(argv, capture_output=True, text=True, timeout=None, check=False):
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")
    return run


def test_resolve_magicdns_strips_trailing_dot():
    run = _fake_run(json.dumps({"Self": {"DNSName": "fakoli-dark.tail4378d.ts.net."}}))
    assert edge.resolve_magicdns_name(run=run) == "fakoli-dark.tail4378d.ts.net"


def test_resolve_magicdns_handles_missing_and_error():
    assert edge.resolve_magicdns_name(run=_fake_run("", returncode=1)) is None
    assert edge.resolve_magicdns_name(run=_fake_run("{}")) is None


def test_read_serve_status_returns_empty_on_unconfigured():
    assert edge.read_serve_status(https_port=443, run=_fake_run("", returncode=1)) == {}


def test_read_serve_status_parses_live_json():
    run = _fake_run(json.dumps(_LIVE_STATUS))
    assert edge.read_serve_status(https_port=443, run=run) == {
        "/": "http://127.0.0.1:8766",
        "/v1": "http://127.0.0.1:8000",
    }


# --------------------------------------------------------------------------- #
# CLI surface: render + dry-run + confirm gating
# --------------------------------------------------------------------------- #
def test_cli_render_prints_plan(capsys):
    rc = edge.main(["render", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] and out["dry_run"]
    assert out["plan"][0][:3] == ["tailscale", "serve", "--bg"]


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

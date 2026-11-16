"""`router fleet-status` — is every configured capability actually served?

Feature 3 of docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md. On 2026-08-08 the router
advertised three voice/audio routes whose backing serves had been off for hours
and no surface reported it.
"""
import json
import subprocess
import textwrap

import pytest

from anvil_serving import router_manage
from anvil_serving.router import config as router_config


def _config(tmp_path, body):
    path = tmp_path / "router.toml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return router_config.load(str(path))


_TWO_TIERS = """
    [router]
    [[router.tiers]]
    id = "primary-local"
    base_url = "http://127.0.0.1:30002/v1"
    model = "m-primary"
    dialect = "openai"
    context_limit = 4096
    privacy = "local"
    tool_support = true
    auth_env = "ANVIL_PRIMARY_LOCAL_KEY"
    health_path = "/health"

    [[router.tiers]]
    id = "omni-local"
    base_url = "http://127.0.0.1:30003/v1"
    model = "m-omni"
    dialect = "openai"
    context_limit = 4096
    privacy = "local"
    tool_support = true
    auth_env = "ANVIL_OMNI_LOCAL_KEY"
    health_path = "/health"

    [router.model_routes]
    llm.primary = "primary-local"
    vision.ocr = "omni-local"
"""


def _probe_map(mapping, default=(False, "URLError")):
    def _probe(url, timeout=4.0):
        for fragment, result in mapping.items():
            if fragment in url:
                return result
        return default
    return _probe


def test_authenticated_endpoint_counts_as_reachable(tmp_path):
    # A 401 means something IS serving and is asking for a token. Treating it
    # as down would report every authenticated tier as broken.
    config = _config(tmp_path, _TWO_TIERS)
    report = router_manage.fleet_status(
        config, _probe=_probe_map({":30002": (True, "HTTP 401"),
                                   ":30003": (True, "HTTP 200")}))
    assert report["unreachable_aliases"] == []


def test_localhost_is_not_substituted(tmp_path):
    # CLAUDE.md: 127.0.0.1 is host-relative; `localhost` must never appear.
    config = _config(tmp_path, _TWO_TIERS.replace(
        "http://127.0.0.1:30002/v1", "http://host.docker.internal:30002/v1"))
    urls = []
    router_manage.fleet_status(
        config, _probe=lambda u, timeout=4.0: (urls.append(u), (True, "ok"))[1])
    assert all("localhost" not in u for u in urls)


def test_cmd_exits_nonzero_only_for_unreachable_aliases(tmp_path, capsys):
    path = tmp_path / "router.toml"
    path.write_text(textwrap.dedent(_TWO_TIERS), encoding="utf-8")

    rc = router_manage.cmd_fleet_status(
        str(path), _probe=_probe_map({":30002": (True, "HTTP 200"),
                                      ":30003": (True, "HTTP 200")}))
    assert rc == 0
    assert "0 unreachable" in capsys.readouterr().out

    rc = router_manage.cmd_fleet_status(
        str(path), _probe=_probe_map({":30002": (True, "HTTP 200")}))
    assert rc == 1
    assert "vision.ocr" in capsys.readouterr().out


def test_configured_fleet_status_executes_inside_router_with_stdin(tmp_path):
    path = tmp_path / "candidate-router.toml"
    path.write_text(textwrap.dedent(_TWO_TIERS), encoding="utf-8")
    nested = {
        "rows": [{
            "kind": "alias",
            "name": "llm.primary",
            "target": "primary-local",
            "host": "100.64.0.10",
            "endpoint": "http://100.64.0.10:30002/health",
            "endpoint_kind": "host-relative-loopback",
            "probe_perspective": "router-runtime",
            "reachable": True,
            "detail": "HTTP 200",
            "failure_class": None,
        }],
        "checked": 1,
        "unreachable": 0,
        "perspective_mismatches": 0,
        "unreachable_aliases": [],
        "evidence_source": "configured-file",
        "probe_perspective": "router-runtime",
    }
    seen = []

    def _run(argv, **kwargs):
        seen.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps(nested), "")

    report = router_manage.configured_fleet_status(str(path), _run=_run)

    argv, kwargs = seen[0]
    assert argv[:4] == ["docker", "exec", "-i", "anvil-router"]
    assert "runtime_fleet_status" in argv[6]
    assert "router_manage.main" not in argv[6]
    assert str(path) not in argv
    assert kwargs["input"].replace("\r\n", "\n") == textwrap.dedent(_TWO_TIERS)
    assert report["evidence_source"] == "configured-file"
    assert report["probe_perspective"] == "router-runtime"
    assert len(report["config_sha256"]) == 64
    assert "host" not in report["rows"][0]
    assert "endpoint" not in report["rows"][0]


def test_installed_fleet_status_has_a_bounded_total_timeout():
    seen = {}

    def _run(argv, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    with pytest.raises(ValueError, match="total timeout"):
        router_manage.installed_fleet_status(timeout=4, _run=_run)

    assert seen["timeout"] == 266.0


@pytest.mark.parametrize("timeout", [0, -1, 61, True, "4"])
def test_cmd_rejects_unbounded_probe_timeout(timeout, capsys):
    assert router_manage.cmd_fleet_status("ignored.toml", timeout=timeout) == 2
    assert "probe timeout" in capsys.readouterr().err

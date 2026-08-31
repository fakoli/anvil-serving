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


def test_reports_each_alias_with_its_backing_endpoint(tmp_path):
    config = _config(tmp_path, _TWO_TIERS)
    report = router_manage.fleet_status(
        config, _probe=_probe_map({":30002": (True, "HTTP 200"),
                                   ":30003": (True, "HTTP 200")}))
    assert report["checked"] == 2
    assert report["unreachable"] == 0
    assert report["unreachable_aliases"] == []
    assert {r["name"] for r in report["rows"]} == {"llm.primary", "vision.ocr"}


def test_unreachable_alias_is_named(tmp_path):
    # The incident: a configured alias whose backing serve is simply not there.
    config = _config(tmp_path, _TWO_TIERS)
    report = router_manage.fleet_status(
        config, _probe=_probe_map({":30002": (True, "HTTP 200")}))
    assert report["unreachable"] == 1
    assert report["unreachable_aliases"] == ["vision.ocr"]


def test_authenticated_endpoint_counts_as_reachable(tmp_path):
    # A 401 means something IS serving and is asking for a token. Treating it
    # as down would report every authenticated tier as broken.
    config = _config(tmp_path, _TWO_TIERS)
    report = router_manage.fleet_status(
        config, _probe=_probe_map({":30002": (True, "HTTP 401"),
                                   ":30003": (True, "HTTP 200")}))
    assert report["unreachable_aliases"] == []


def test_container_relative_host_is_translated_and_reported(tmp_path):
    # The router runs in a container, so its config names the Docker host as
    # host.docker.internal, which does not resolve on the host itself. Probing
    # it verbatim would report a healthy serve as unreachable.
    config = _config(tmp_path, _TWO_TIERS.replace(
        "http://127.0.0.1:30002/v1", "http://host.docker.internal:30002/v1"))
    probed = []

    def _probe(url, timeout=4.0):
        probed.append(url)
        return True, "HTTP 200"

    report = router_manage.fleet_status(config, _probe=_probe)

    assert all("host.docker.internal" not in url for url in probed)
    assert any(url.startswith("http://127.0.0.1:30002/") for url in probed)
    primary = next(r for r in report["rows"] if r["name"] == "llm.primary")
    assert "host-relative loopback" in primary["detail"]
    assert primary["endpoint_kind"] == "host-relative-loopback"
    assert primary["probe_perspective"] == "command-host"
    assert "host" not in primary
    assert "endpoint" not in primary


def test_router_runtime_perspective_preserves_container_relative_endpoint(tmp_path):
    config = _config(tmp_path, _TWO_TIERS.replace(
        "http://127.0.0.1:30002/v1", "http://host.docker.internal:30002/v1"))
    probed = []

    report = router_manage.fleet_status(
        config,
        probe_perspective="router-runtime",
        _probe=lambda url, timeout=4.0: (probed.append(url), (True, "HTTP 200"))[1],
    )

    assert any(url.startswith("http://host.docker.internal:30002/") for url in probed)
    primary = next(row for row in report["rows"] if row["name"] == "llm.primary")
    assert primary["reachable"] is True
    assert primary["probe_perspective"] == "router-runtime"


def test_command_host_failure_is_typed_as_perspective_mismatch(tmp_path):
    config = _config(tmp_path, _TWO_TIERS.replace(
        "http://127.0.0.1:30002/v1", "http://host.docker.internal:30002/v1"))

    report = router_manage.fleet_status(
        config,
        _probe=_probe_map({":30003": (True, "HTTP 200")}),
    )

    primary = next(row for row in report["rows"] if row["name"] == "llm.primary")
    assert primary["failure_class"] == "probe_perspective_mismatch"
    assert report["perspective_mismatches"] == 1


def test_localhost_is_not_substituted(tmp_path):
    # CLAUDE.md: 127.0.0.1 is host-relative; `localhost` must never appear.
    config = _config(tmp_path, _TWO_TIERS.replace(
        "http://127.0.0.1:30002/v1", "http://host.docker.internal:30002/v1"))
    urls = []
    router_manage.fleet_status(
        config, _probe=lambda u, timeout=4.0: (urls.append(u), (True, "ok"))[1])
    assert all("localhost" not in u for u in urls)


def test_audio_routes_are_checked(tmp_path):
    config = _config(tmp_path, _TWO_TIERS + """
    [[router.audio_routes]]
    id = "local-stt"
    purpose = "stt"
    model = "tdt"
    base_url = "http://127.0.0.1:30010/v1"
    default = true
    """)
    report = router_manage.fleet_status(
        config, _probe=_probe_map({":30002": (True, "HTTP 200"),
                                   ":30003": (True, "HTTP 200")}))
    audio = [r for r in report["rows"] if r["kind"] == "audio"]
    assert len(audio) == 1
    assert audio[0]["reachable"] is False


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


def test_cmd_json_is_machine_readable(tmp_path, capsys):
    path = tmp_path / "router.toml"
    path.write_text(textwrap.dedent(_TWO_TIERS), encoding="utf-8")
    router_manage.cmd_fleet_status(
        str(path), as_json=True,
        _probe=_probe_map({":30002": (True, "HTTP 200"), ":30003": (True, "HTTP 200")}))
    report = json.loads(capsys.readouterr().out)
    assert report["checked"] == 2
    assert report["evidence_source"] == "configured-file"
    assert report["probe_perspective"] == "command-host"
    assert len(report["config_sha256"]) == 64
    serialized = json.dumps(report)
    assert "127.0.0.1" not in serialized
    assert str(path) not in serialized


def test_installed_fleet_status_executes_inside_router_and_sanitizes_rows():
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
        "config_sha256": "a" * 64,
    }
    seen = []

    def _run(argv, **kwargs):
        seen.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps(nested), "")

    report = router_manage.installed_fleet_status(_run=_run)

    assert seen[0][0][:3] == ["docker", "exec", "anvil-router"]
    assert "runtime_fleet_status" in seen[0][0][5]
    assert "router_manage.main" not in seen[0][0][5]
    assert report["evidence_source"] == "installed-router"
    assert report["probe_perspective"] == "router-runtime"
    assert "host" not in report["rows"][0]
    assert "endpoint" not in report["rows"][0]


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


def test_runtime_fleet_status_probes_directly_without_recursive_dispatch(tmp_path):
    path = tmp_path / "router.toml"
    path.write_text(textwrap.dedent(_TWO_TIERS), encoding="utf-8")
    probed = []

    report = router_manage.runtime_fleet_status(
        str(path),
        _probe=lambda url, timeout=4.0: (
            probed.append((url, timeout)),
            (True, "HTTP 200"),
        )[1],
    )

    assert len(probed) == 2
    assert report["unreachable"] == 0
    assert report["probe_perspective"] == "router-runtime"
    assert all(
        row["probe_perspective"] == "router-runtime" for row in report["rows"]
    )


def test_cmd_explicit_router_runtime_uses_container_probe(tmp_path, capsys):
    path = tmp_path / "router.toml"
    path.write_text(textwrap.dedent(_TWO_TIERS), encoding="utf-8")
    calls = []
    report = {
        "rows": [],
        "checked": 0,
        "unreachable": 0,
        "perspective_mismatches": 0,
        "unreachable_aliases": [],
        "evidence_source": "configured-file",
        "probe_perspective": "router-runtime",
        "config_sha256": "a" * 64,
    }

    rc = router_manage.cmd_fleet_status(
        str(path),
        as_json=True,
        probe_perspective="router-runtime",
        _probe=lambda *_args, **_kwargs: pytest.fail("host probe must not run"),
        _configured_runtime=lambda *args, **kwargs: (
            calls.append((args, kwargs)), report
        )[1],
    )

    assert rc == 0
    assert calls == [((str(path),), {"container": "anvil-router", "timeout": 4.0})]
    assert json.loads(capsys.readouterr().out)["probe_perspective"] == "router-runtime"


@pytest.mark.parametrize("container", ["", "--privileged", "bad/name", "bad name"])
def test_configured_fleet_status_rejects_unsafe_container_names(tmp_path, container):
    path = tmp_path / "router.toml"
    path.write_text(textwrap.dedent(_TWO_TIERS), encoding="utf-8")
    with pytest.raises(ValueError, match="container name"):
        router_manage.configured_fleet_status(str(path), container=container)


def test_configured_fleet_status_rejects_oversized_config(tmp_path):
    path = tmp_path / "router.toml"
    path.write_bytes(b"x" * (router_manage.MAX_ROUTER_CONFIG_BYTES + 1))

    with pytest.raises(ValueError, match="1 MiB"):
        router_manage.configured_fleet_status(str(path))


def test_cmd_defaults_to_installed_router_evidence(capsys):
    report = {
        "rows": [],
        "checked": 0,
        "unreachable": 0,
        "perspective_mismatches": 0,
        "unreachable_aliases": [],
        "evidence_source": "installed-router",
        "probe_perspective": "router-runtime",
    }
    calls = []

    rc = router_manage.cmd_fleet_status(
        as_json=True,
        _installed=lambda **kwargs: (calls.append(kwargs), report)[1],
    )

    assert rc == 0
    assert calls == [{
        "container": "anvil-router",
        "installed_config": "/etc/anvil/config.toml",
        "timeout": 4.0,
    }]
    assert json.loads(capsys.readouterr().out)["evidence_source"] == "installed-router"


@pytest.mark.parametrize("container", ["", "--privileged", "bad/name", "bad name"])
def test_installed_fleet_status_rejects_unsafe_container_names(container):
    with pytest.raises(ValueError, match="container name"):
        router_manage.installed_fleet_status(container=container)


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


def test_missing_config_is_a_usage_error(capsys):
    rc = router_manage.cmd_fleet_status("/nonexistent/router.toml")
    assert rc == 2
    assert "could not load router config" in capsys.readouterr().err

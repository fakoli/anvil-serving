"""`fleet version` — is every declared host running the same anvil-serving?

Feature 8 of docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md ("Undetected version
skew"). On 2026-08-08 a fleet host ran two minors behind the operator host;
the older code resolved transports differently and reported an error naming
the wrong cause. Nothing anywhere reported the version skew that actually
caused it.
"""

from __future__ import annotations

import json
import subprocess
import types

from anvil_serving import cli, fleet


def _run_by_host(mapping):
    """mapping: host -> (returncode, stdout, stderr) tuple, or a callable that raises."""

    def _run(argv, **kwargs):
        assert argv[:3] == ["ssh", "-n", "-o"]
        host = argv[4]
        outcome = mapping[host]
        if callable(outcome):
            return outcome(**kwargs)
        rc, stdout, stderr = outcome
        return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)

    return _run


def test_one_host_skewed_exits_one(capsys):
    rc = fleet.cmd_version(
        ["dark", "mini"],
        local="1.2.3",
        _run=_run_by_host(
            {
                "dark": (0, "anvil-serving 1.0.0\n", ""),
                "mini": (0, "anvil-serving 1.2.3\n", ""),
            }
        ),
    )
    assert rc == 1
    out = capsys.readouterr().out
    assert "1 skewed" in out


def test_timeout_reports_timeout_state():
    def _raise(**_kwargs):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=1)

    report = fleet.collect_fleet_versions(
        ["dark"],
        "1.2.3",
        timeout=1,
        _run=_run_by_host({"dark": _raise}),
    )
    assert report["hosts"][0]["state"] == "timeout"
    assert report["skewed"] == 0
    assert report["timeout"] == 1


def test_no_hosts_anywhere_exits_two(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path / "does-not-exist"))
    rc = fleet.main(["version"])
    assert rc == 2
    assert "no operator topology" in capsys.readouterr().err


def test_global_json_preserves_typed_report_when_version_gate_fails(monkeypatch, capsys):
    monkeypatch.setattr(fleet, "local_version", lambda: "1.2.3")
    monkeypatch.setattr(
        fleet,
        "probe_host",
        lambda host, **_kwargs: {
            "host": host,
            "state": "not-installed",
            "version": None,
            "detail": "anvil-serving not found",
        },
    )

    assert cli.main(["fleet", "version", "--host", "worker", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "fleet_version_gate_failed"
    assert payload["data"]["not_installed"] == 1
    assert payload["data"]["hosts"] == [
        {
            "detail": "anvil-serving not found",
            "host": "worker",
            "skew": False,
            "state": "not-installed",
            "version": None,
        }
    ]

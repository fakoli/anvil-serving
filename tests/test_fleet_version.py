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
import textwrap
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


def test_all_hosts_ok_no_skew_exits_zero(capsys):
    rc = fleet.cmd_version(
        ["dark", "mini"],
        local="1.2.3",
        _run=_run_by_host(
            {
                "dark": (0, "anvil-serving 1.2.3\n", ""),
                "mini": (0, "anvil-serving 1.2.3\n", ""),
            }
        ),
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "0 skewed" in out
    assert "2 host(s)" in out


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


def test_not_installed_exits_one():
    rc = fleet.cmd_version(
        ["dark"],
        local="1.2.3",
        _run=_run_by_host({"dark": (127, "", "bash: anvil-serving: command not found")}),
    )
    assert rc == 1


def test_unreachable_only_exits_zero():
    # A sleeping laptop is an availability gap, not proof of divergent code.
    rc = fleet.cmd_version(
        ["dark"],
        local="1.2.3",
        _run=_run_by_host(
            {"dark": (255, "", "ssh: connect to host dark port 22: Connection refused")}
        ),
    )
    assert rc == 0


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


def test_version_parse_strips_windows_carriage_return():
    # Windows OpenSSH clients can leave a trailing \r on captured text output.
    row = fleet.probe_host(
        "dark",
        _run=lambda argv, **_kwargs: types.SimpleNamespace(
            returncode=0, stdout="anvil-serving 1.2.3\r\n", stderr=""
        ),
    )
    assert row["version"] == "1.2.3"
    assert row["state"] == "ok"


def test_host_flag_overrides_topology_derived_hosts(tmp_path, monkeypatch):
    topology_path = tmp_path / "operator-topology.toml"
    topology_path.write_text(
        textwrap.dedent("""
        schema_version = 1
        id = "t"

        [[hosts]]
        id = "topology-only-host"
        roles = ["gpu"]
    """),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    monkeypatch.setattr("socket.gethostname", lambda: "operator-laptop")

    seen = {}

    def _fake_cmd_version(hosts, **kwargs):
        seen["hosts"] = hosts
        return 0

    monkeypatch.setattr(fleet, "cmd_version", _fake_cmd_version)

    rc = fleet.main(["version", "--host", "explicit-host"])

    assert rc == 0
    assert seen["hosts"] == ["explicit-host"]  # topology-only-host must never be probed


def test_topology_derived_hosts_exclude_local(tmp_path, monkeypatch):
    topology_path = tmp_path / "operator-topology.toml"
    topology_path.write_text(
        textwrap.dedent("""
        schema_version = 1
        id = "t"

        [[hosts]]
        id = "fakoli-dark"
        roles = ["gpu"]

        [[hosts]]
        id = "fakoli-mini"
        roles = ["gateway"]
    """),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    monkeypatch.setattr("socket.gethostname", lambda: "fakoli-mini.local")

    hosts, note = fleet._topology_remote_hosts()

    assert hosts == ["fakoli-dark"]
    assert note is None


def test_declared_remote_hosts_notes_unmatched_hostname():
    from anvil_serving.topology import parse_topology

    topology = parse_topology(
        {
            "schema_version": 1,
            "id": "t",
            "hosts": [
                {"id": "dark", "roles": ["gpu"]},
                {"id": "mini", "roles": ["gateway"]},
            ],
        }
    )

    hosts, note = fleet.declared_remote_hosts(topology, hostname="unrelated-workstation")

    assert sorted(hosts) == ["dark", "mini"]
    assert note is not None and "unrelated-workstation" in note


def test_no_hosts_anywhere_exits_two(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path / "does-not-exist"))
    rc = fleet.main(["version"])
    assert rc == 2
    assert "no operator topology" in capsys.readouterr().err


def test_local_only_never_exits_nonzero(capsys):
    rc = fleet.cmd_version([], local="1.2.3")
    assert rc == 0
    out = capsys.readouterr().out
    assert "no remote hosts" in out


def test_json_output_is_machine_readable(capsys):
    rc = fleet.cmd_version(
        ["dark"],
        local="1.2.3",
        as_json=True,
        _run=_run_by_host({"dark": (0, "anvil-serving 1.2.3\n", "")}),
    )
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["local_version"] == "1.2.3"
    assert report["hosts"][0]["host"] == "dark"
    assert report["hosts"][0]["skew"] is False


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


def test_global_json_returns_the_same_typed_report_on_success(monkeypatch, capsys):
    monkeypatch.setattr(fleet, "local_version", lambda: "1.2.3")
    monkeypatch.setattr(
        fleet,
        "probe_host",
        lambda host, **_kwargs: {
            "host": host,
            "state": "ok",
            "version": "1.2.3",
            "detail": None,
        },
    )

    assert cli.main(["fleet", "version", "--host", "worker", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["host_count"] == 1
    assert payload["data"]["hosts"][0]["skew"] is False

"""`fleet drift` -- does a host's live operator home match its repo snapshot?

Feature 7 of docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md ("Undetected config
drift"). Observed live on 2026-08-08: a live home six commits behind its
repo while serving production, and a second host whose live home was a
wholesale byte-copy of the wrong host's home. Read-only.
"""
from __future__ import annotations

import json
import subprocess
import types

from anvil_serving import fleet


def _write(path, name, content):
    full = path / name
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return full


def _repo(tmp_path, host, files):
    repo = tmp_path / "repo"
    home = repo / "hosts" / host / "operator-home"
    for name, content in files.items():
        _write(home, name, content)
    return repo


class _TrackingOpen:
    """Wraps builtins.open to assert a forbidden filename is never opened."""

    def __init__(self, real_open, forbidden):
        self.real_open = real_open
        self.forbidden = forbidden
        self.touched = []

    def __call__(self, path, *args, **kwargs):
        self.touched.append(str(path))
        assert self.forbidden not in str(path), "must never read %s" % self.forbidden
        return self.real_open(path, *args, **kwargs)


def test_identical_local_host_exits_zero(tmp_path):
    repo = _repo(tmp_path, "laptop", {"config.toml": "a = 1\n"})
    live = tmp_path / "live"
    _write(live, "config.toml", "a = 1\n")

    rc = fleet.cmd_drift(str(repo), ["laptop"], home=str(live), hostname="laptop")
    assert rc == 0


def test_differing_file_is_named_and_exits_one(tmp_path, capsys):
    repo = _repo(tmp_path, "laptop", {"config.toml": "a = 1\n"})
    live = tmp_path / "live"
    _write(live, "config.toml", "a = 2\n")

    rc = fleet.cmd_drift(str(repo), ["laptop"], home=str(live), hostname="laptop")
    out = capsys.readouterr().out

    assert rc == 1
    assert "differs config.toml" in out


def test_missing_live_file_exits_one(tmp_path, capsys):
    repo = _repo(tmp_path, "laptop", {"config.toml": "a = 1\n"})
    live = tmp_path / "live"
    live.mkdir()

    rc = fleet.cmd_drift(str(repo), ["laptop"], home=str(live), hostname="laptop")
    out = capsys.readouterr().out

    assert rc == 1
    assert "missing-live config.toml" in out


def test_unreachable_remote_host_exits_zero(tmp_path):
    # An unreachable host reports state (timeout/unreachable), not drift --
    # a sleeping host is an availability gap, not proof of divergence (same
    # reasoning as fleet version).
    repo = _repo(tmp_path, "dark", {"config.toml": "a = 1\n"})

    def _run(argv, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=1)

    rc = fleet.cmd_drift(str(repo), ["dark"], hostname="operator-laptop", _run=_run)
    assert rc == 0


def test_crlf_vs_lf_same_content_is_identical(tmp_path):
    repo = _repo(tmp_path, "laptop", {"config.toml": "a = 1\nb = 2\n"})
    live = tmp_path / "live"
    _write(live, "config.toml", "a = 1\r\nb = 2\r\n")

    rc = fleet.cmd_drift(str(repo), ["laptop"], home=str(live), hostname="laptop")
    assert rc == 0


def test_env_in_live_but_not_repo_is_never_read(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "laptop", {"config.toml": "a = 1\n"})
    live = tmp_path / "live"
    _write(live, "config.toml", "a = 1\n")
    _write(live, ".env", "SECRET=do-not-read-me\n")

    import builtins
    tracker = _TrackingOpen(builtins.open, ".env")
    monkeypatch.setattr(builtins, "open", tracker)

    rc = fleet.cmd_drift(str(repo), ["laptop"], home=str(live), hostname="laptop")

    assert rc == 0
    assert not any(".env" in p for p in tracker.touched)


def test_ssh_probe_never_sent_env_filename(tmp_path):
    repo = _repo(tmp_path, "dark", {"config.toml": "a = 1\n"})
    seen_argv = []

    def _run(argv, **_kwargs):
        seen_argv.append(argv)
        payload = json.dumps({"config.toml": fleet._sha256_normalized(b"a = 1\n")})
        return types.SimpleNamespace(returncode=0, stdout=payload, stderr="")

    rc = fleet.cmd_drift(str(repo), ["dark"], hostname="operator-laptop", _run=_run)

    assert rc == 0
    assert len(seen_argv) == 1
    assert "'.env'" not in seen_argv[0][-1]  # the embedded repo-file-name list


def test_remote_host_reachable_reports_differs(tmp_path, capsys):
    repo = _repo(tmp_path, "dark", {"config.toml": "a = 1\n"})

    def _run(argv, **_kwargs):
        payload = json.dumps({"config.toml": fleet._sha256_normalized(b"a = 2\n")})
        return types.SimpleNamespace(returncode=0, stdout=payload, stderr="")

    rc = fleet.cmd_drift(str(repo), ["dark"], hostname="operator-laptop", _run=_run)
    out = capsys.readouterr().out

    assert rc == 1
    assert "differs config.toml" in out


def test_host_filter_restricts_to_named_host(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "dark", {"config.toml": "a = 1\n"})
    _repo(tmp_path, "mini", {"config.toml": "a = 1\n"})  # second host, same repo root
    monkeypatch.setattr("socket.gethostname", lambda: "unrelated-workstation")

    seen = {}

    def _fake_cmd_drift(repo_arg, hosts, **kwargs):
        seen["hosts"] = list(hosts)
        return 0

    monkeypatch.setattr(fleet, "cmd_drift", _fake_cmd_drift)

    rc = fleet.main(["drift", "--repo", str(repo), "--host", "dark"])

    assert rc == 0
    assert seen["hosts"] == ["dark"]  # mini must never be probed


def test_unknown_host_flag_exits_two(tmp_path, capsys):
    repo = _repo(tmp_path, "dark", {"config.toml": "a = 1\n"})

    rc = fleet.main(["drift", "--repo", str(repo), "--host", "no-such-host"])

    assert rc == 2
    assert "no-such-host" in capsys.readouterr().err


def test_no_host_flag_discovers_repo_hosts(tmp_path):
    repo = _repo(tmp_path, "dark", {"config.toml": "a = 1\n"})
    _write(repo / "hosts" / "mini" / "operator-home", "config.toml", "a = 1\n")

    assert sorted(fleet.discover_repo_hosts(str(repo))) == ["dark", "mini"]


def test_json_output_is_machine_readable(tmp_path):
    repo = _repo(tmp_path, "laptop", {"config.toml": "a = 1\n"})
    live = tmp_path / "live"
    _write(live, "config.toml", "a = 1\n")

    rc = fleet.cmd_drift(str(repo), ["laptop"], home=str(live), hostname="laptop", as_json=True)
    assert rc == 0


def test_json_output_contents(tmp_path, capsys):
    repo = _repo(tmp_path, "laptop", {"config.toml": "a = 1\n"})
    live = tmp_path / "live"
    _write(live, "config.toml", "a = 2\n")

    rc = fleet.cmd_drift(str(repo), ["laptop"], home=str(live), hostname="laptop", as_json=True)
    report = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert report["repo"] == str(repo)
    assert report["drifted"] == 1
    assert report["hosts"][0]["host"] == "laptop"
    assert report["hosts"][0]["differs"] == 1
    files = {f["path"]: f["status"] for f in report["hosts"][0]["files"]}
    assert files["config.toml"] == "differs"


def test_short_host_id_matches_prefixed_hostname():
    # Observed live: repo keys the host as "dark", the machine reports
    # "fakoli-dark"; prefix matching classified local as remote and ssh'd to
    # itself. Containment keeps both spellings local.
    from anvil_serving.fleet import _local_hostname_matches
    assert _local_hostname_matches("fakoli-dark", "dark")
    assert _local_hostname_matches("dark", "fakoli-dark")
    assert not _local_hostname_matches("fakoli-dark", "mini")
    # Bare containment over-corrects: "w" is a substring of "elsewhere".
    assert not _local_hostname_matches("elsewhere", "w")
    # macOS reports a .local suffix; compare on the first DNS label.
    assert _local_hostname_matches("fakoli-mini.local", "fakoli-mini")
    assert _local_hostname_matches("ai-mbp25.local", "ai-mbp25")


def test_windows_host_falls_back_to_python_launcher(tmp_path):
    # Observed live: Windows hosts ship `python`, not `python3`; the probe
    # read "not recognized" and reported a healthy host unreachable.
    (tmp_path / "hosts" / "w" / "operator-home").mkdir(parents=True)
    (tmp_path / "hosts" / "w" / "operator-home" / "a.toml").write_text(
        "x = 1\n", encoding="utf-8")
    import hashlib
    import json as _json
    digest = hashlib.sha256(b"x = 1\n").hexdigest()
    calls = []

    def _run(argv, **_k):
        calls.append(argv)
        class R:
            pass
        r = R()
        if "python3" in argv:
            r.returncode = 1
            r.stdout = ""
            r.stderr = "'python3' is not recognized as an internal or external command"
        else:
            r.returncode = 0
            r.stdout = _json.dumps({"a.toml": digest})
            r.stderr = ""
        return r

    from anvil_serving import fleet
    row = fleet.collect_host_drift(
        "w", str(tmp_path / "hosts" / "w" / "operator-home"),
        hostname="elsewhere", _run=_run)
    assert row["state"] == "ok", row
    assert row["differs"] == 0 and row["missing"] == 0
    assert any("python3" in a for a in calls) and any(
        "python" in a and "python3" not in a for a in calls)

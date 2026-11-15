"""`fleet drift` -- does a host's live operator home match its repo snapshot?

Feature 7 of docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md ("Undetected config
drift"). Observed live on 2026-08-08: a live home six commits behind its
repo while serving production, and a second host whose live home was a
wholesale byte-copy of the wrong host's home. Read-only.
"""
from __future__ import annotations

import json
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


def test_remote_host_reachable_reports_differs(tmp_path, capsys):
    repo = _repo(tmp_path, "dark", {"config.toml": "a = 1\n"})

    def _run(argv, **_kwargs):
        payload = json.dumps({"config.toml": fleet._sha256_normalized(b"a = 2\n")})
        return types.SimpleNamespace(returncode=0, stdout=payload, stderr="")

    rc = fleet.cmd_drift(str(repo), ["dark"], hostname="operator-laptop", _run=_run)
    out = capsys.readouterr().out

    assert rc == 1
    assert "differs config.toml" in out


def test_unknown_host_flag_exits_two(tmp_path, capsys):
    repo = _repo(tmp_path, "dark", {"config.toml": "a = 1\n"})

    rc = fleet.main(["drift", "--repo", str(repo), "--host", "no-such-host"])

    assert rc == 2
    assert "no-such-host" in capsys.readouterr().err


def test_json_output_is_machine_readable(tmp_path):
    repo = _repo(tmp_path, "laptop", {"config.toml": "a = 1\n"})
    live = tmp_path / "live"
    _write(live, "config.toml", "a = 1\n")

    rc = fleet.cmd_drift(str(repo), ["laptop"], home=str(live), hostname="laptop", as_json=True)
    assert rc == 0

"""Shared test builders and per-test operator-state isolation.

Import builders directly: ``from tests.conftest import proc``.
"""
import types

import pytest


@pytest.fixture(autouse=True)
def isolated_operator_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path / "operator-home"))


def proc(rc=0, out="", err=""):
    """A subprocess.CompletedProcess-shaped fake for an injected `_run`."""
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


class Response:
    """A urllib-style response fake for an injected `_open`/urlopen seam."""

    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self.payload if size < 0 else self.payload[:size]


def enabled_cache_policy():
    """A fully-enabled, applicable host cache policy for pull/download tests."""
    return {
        "enabled": True,
        "distro": "docker-desktop",
        "threshold_gb": 16.0,
        "source_path": "host.toml",
        "configured": True,
        "applicable": True,
        "schema_version": 1,
    }

"""Container-controller host boundary tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from anvil_serving import paths, router_manage, serves


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_runtime_url_rewrites_only_exact_loopback_with_explicit_alias():
    env = {paths.LOOPBACK_ALIAS_ENV: "host.docker.internal"}

    assert paths.runtime_url(
        "http://127.0.0.1:8000/v1/models?limit=1",
        environ=env,
    ) == "http://host.docker.internal:8000/v1/models?limit=1"
    assert paths.runtime_url("http://100.87.34.66:8000/v1", environ=env) == (
        "http://100.87.34.66:8000/v1"
    )


def test_runtime_url_rejects_aliases_with_scheme_or_port():
    with pytest.raises(ValueError, match="without a scheme or port"):
        paths.runtime_url(
            "http://127.0.0.1:8000",
            environ={paths.LOOPBACK_ALIAS_ENV: "http://host.docker.internal:8000"},
        )


def test_serves_health_uses_container_loopback_alias(monkeypatch):
    seen = []
    monkeypatch.setenv(paths.LOOPBACK_ALIAS_ENV, "host.docker.internal")

    def opener(url, timeout):
        seen.append((url, timeout))
        return _Response()

    assert serves._health(30002, "/health", _open=opener) == 200
    assert seen == [("http://host.docker.internal:30002/health", 3)]


def test_router_status_reports_container_reachable_health_url(monkeypatch):
    seen = []
    monkeypatch.setenv(paths.LOOPBACK_ALIAS_ENV, "host.docker.internal")

    def run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="running\n", stderr="")

    def opener(url, timeout):
        seen.append((url, timeout))
        return _Response()

    summary = router_manage.status_summary("anvil-router", _run=run, _open=opener)

    assert summary["running"] is True
    assert summary["health_status"] == 200
    assert summary["health_url"] == "http://host.docker.internal:8000/"
    assert seen == [("http://host.docker.internal:8000/", 3)]

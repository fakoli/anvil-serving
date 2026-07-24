"""Tests for the Docker-managed Realtime voice-proxy serve
(``anvil_serving.voice.serves.proxy``).

Mirrors ``tests/voice/test_stt_serve.py``'s serve-lifecycle half: docker is
never invoked (a fake ``_run`` callable stands in for ``subprocess.run``) and
no socket is opened (a fake ``_open`` stands in for ``urllib``). No GPU, no
network. The one behavioral difference from STT/TTS is asserted directly: the
readiness probe defaults to ``{base_url}/usage`` (the proxy's health route),
not the OpenAI ``/v1/models`` an STT/TTS serve answers.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from anvil_serving.voice.serves._common import ServeNotConfigured
from anvil_serving.voice.serves.proxy import ProxyServe, ProxyServeConfig


class FakeRun:
    """Matches an argv PREFIX against canned ``(returncode, stdout, stderr)``."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        for prefix, rc, out, err in self.responses:
            if argv[: len(prefix)] == prefix:
                return SimpleNamespace(returncode=rc, stdout=out, stderr=err)
        return SimpleNamespace(returncode=1, stdout="", stderr="no matcher for %r" % (argv,))


class FakeOpenResponse:
    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def getcode(self):
        return self.status


def fake_open_ok(url, timeout=None):
    return FakeOpenResponse(200)


def fake_open_fails(url, timeout=None):
    raise OSError("connection refused")


@pytest.fixture
def manifest_with_proxy(tmp_path):
    p = tmp_path / "serves.voice.toml"
    p.write_text(
        '[[serve]]\nname = "realtime-proxy"\ncontainer = "anvil-voice-proxy"\n'
        'port = 8765\nmodel = "anvil-realtime-proxy"\nengine = "audio"\nhealth = "/usage"\n'
        'up = "echo bring-up-proxy"\n',
        encoding="utf-8",
    )
    return str(p)


def test_realtime_url_appends_path():
    serve = ProxyServe(ProxyServeConfig(base_url="http://127.0.0.1:8765", model="anvil-realtime-proxy"))
    assert serve.realtime_url == "http://127.0.0.1:8765/realtime"


def test_realtime_url_strips_trailing_slash():
    serve = ProxyServe(ProxyServeConfig(base_url="http://127.0.0.1:8765/", model="anvil-realtime-proxy"))
    assert serve.realtime_url == "http://127.0.0.1:8765/realtime"


def test_default_serve_name_is_realtime_proxy():
    serve = ProxyServe(ProxyServeConfig(base_url="http://127.0.0.1:8765", model="anvil-realtime-proxy"))
    assert serve.config.serve_name == "realtime-proxy"


def test_bring_up_raises_serve_not_configured_when_manifest_missing(tmp_path):
    missing = str(tmp_path / "does-not-exist.toml")
    serve = ProxyServe(ProxyServeConfig(base_url="http://127.0.0.1:8765", model="proxy", manifest_path=missing))
    with pytest.raises(ServeNotConfigured):
        serve.bring_up()


def test_bring_up_raises_serve_not_configured_when_entry_missing(tmp_path):
    p = tmp_path / "serves.toml"
    p.write_text(
        '[[serve]]\nname = "stt"\ncontainer = "anvil-stt"\nport = 8090\n'
        'model = "parakeet"\nengine = "audio"\n',
        encoding="utf-8",
    )
    serve = ProxyServe(ProxyServeConfig(base_url="http://127.0.0.1:8765", model="proxy", manifest_path=str(p)))
    with pytest.raises(ServeNotConfigured):
        serve.bring_up()


def test_bring_up_starts_via_declared_up_command_never_bare_docker(manifest_with_proxy):
    fake_run = FakeRun([
        (["docker", "inspect"], 1, "", "Error: No such container: anvil-voice-proxy"),
        (["echo", "bring-up-proxy"], 0, "bring-up-proxy\n", ""),
    ])
    serve = ProxyServe(
        ProxyServeConfig(base_url="http://127.0.0.1:8765", model="proxy", manifest_path=manifest_with_proxy),
        _run=fake_run,
    )
    rc = serve.bring_up()
    assert rc == 0
    assert ["echo", "bring-up-proxy"] in fake_run.calls
    assert all(c[0] == "docker" or c == ["echo", "bring-up-proxy"] for c in fake_run.calls)


def test_tear_down_stops_a_running_container(manifest_with_proxy):
    stopped = []

    def fake_run(argv, **kwargs):
        fake_run.calls.append(list(argv))
        if argv[:2] == ["docker", "inspect"]:
            state = "exited" if stopped else "running"
            return SimpleNamespace(returncode=0, stdout=state + "\n", stderr="")
        if argv[:2] == ["docker", "stop"]:
            stopped.append(argv)
            return SimpleNamespace(returncode=0, stdout="anvil-voice-proxy\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="no matcher")
    fake_run.calls = []
    serve = ProxyServe(
        ProxyServeConfig(base_url="http://127.0.0.1:8765", model="proxy", manifest_path=manifest_with_proxy),
        _run=fake_run,
    )
    rc = serve.tear_down()
    assert rc == 0
    assert ["docker", "stop", "anvil-voice-proxy"] in fake_run.calls


def test_tear_down_raises_serve_not_configured_when_manifest_missing(tmp_path):
    missing = str(tmp_path / "nope.toml")
    serve = ProxyServe(ProxyServeConfig(base_url="http://127.0.0.1:8765", model="proxy", manifest_path=missing))
    with pytest.raises(ServeNotConfigured):
        serve.tear_down()


def test_wait_ready_probes_usage_by_default(manifest_with_proxy):
    """The proxy's readiness probe defaults to `{base_url}/usage` -- NOT the
    OpenAI `/v1/models` an STT/TTS serve answers."""
    fake_run = FakeRun([(["docker", "inspect"], 0, "running\n", "")])
    seen = []

    def open_probe(url, timeout=None):
        seen.append(url)
        return FakeOpenResponse(200)

    serve = ProxyServe(
        ProxyServeConfig(base_url="http://127.0.0.1:8765", model="proxy", manifest_path=manifest_with_proxy),
        _run=fake_run, _open=open_probe,
    )
    readiness = serve.wait_ready()
    assert readiness.ready is True
    assert readiness.docker_state == "running"
    assert readiness.name == "realtime-proxy"
    assert seen == ["http://127.0.0.1:8765/usage"]


def test_wait_ready_honors_explicit_ready_url(manifest_with_proxy):
    fake_run = FakeRun([(["docker", "inspect"], 0, "running\n", "")])
    seen = []

    def open_probe(url, timeout=None):
        seen.append(url)
        return FakeOpenResponse(200)

    serve = ProxyServe(
        ProxyServeConfig(
            base_url="http://127.0.0.1:8765",
            model="proxy",
            manifest_path=manifest_with_proxy,
            ready_url="http://127.0.0.1:8765/pool",
        ),
        _run=fake_run, _open=open_probe,
    )
    readiness = serve.wait_ready()
    assert readiness.ready is True
    assert seen == ["http://127.0.0.1:8765/pool"]


def test_wait_ready_false_when_probe_fails(manifest_with_proxy):
    fake_run = FakeRun([(["docker", "inspect"], 0, "running\n", "")])
    serve = ProxyServe(
        ProxyServeConfig(base_url="http://127.0.0.1:8765", model="proxy", manifest_path=manifest_with_proxy),
        _run=fake_run, _open=fake_open_fails,
    )
    readiness = serve.wait_ready()
    assert readiness.ready is False
    assert "not responding" in readiness.detail
    assert "/usage" in readiness.detail


def test_wait_ready_reports_unconfigured_state_without_raising(tmp_path):
    missing = str(tmp_path / "nope.toml")
    serve = ProxyServe(
        ProxyServeConfig(base_url="http://127.0.0.1:8765", model="proxy", manifest_path=missing),
        _open=fake_open_fails,
    )
    readiness = serve.wait_ready()  # must not raise even though the manifest is absent
    assert readiness.docker_state == "unconfigured"
    assert readiness.ready is False


def test_dry_run_bring_up_does_not_execute_up_command(manifest_with_proxy):
    fake_run = FakeRun([(["docker", "inspect"], 1, "", "No such container")])
    serve = ProxyServe(
        ProxyServeConfig(base_url="http://127.0.0.1:8765", model="proxy", manifest_path=manifest_with_proxy),
        _run=fake_run,
    )
    rc = serve.bring_up(dry_run=True)
    assert rc == 0
    assert ["echo", "bring-up-proxy"] not in fake_run.calls

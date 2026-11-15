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


import pytest

from anvil_serving.voice.serves.proxy import ProxyServe, ProxyServeConfig
from tests.voice.conftest import FakeRun, fake_open_fails


@pytest.fixture
def manifest_with_proxy(tmp_path):
    p = tmp_path / "serves.voice.toml"
    p.write_text(
        '[[serve]]\nname = "realtime-proxy"\ncontainer = "anvil-voice-proxy"\nruntime = "docker"\n'
        'port = 8765\nmodel = "anvil-realtime-proxy"\nengine = "audio"\nhealth = "/usage"\n'
        'up = "echo bring-up-proxy"\n',
        encoding="utf-8",
    )
    return str(p)


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

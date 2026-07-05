"""Tests for the out-of-process TTS serve: lifecycle
(``anvil_serving.voice.serves.tts``, anvil task T008 serve half) and stage
(``anvil_serving.voice.stages.tts``, anvil task T008 stage half).

Dependency-light and hermetic: docker is never invoked (a fake `_run`
callable stands in for `subprocess.run`) and no socket is opened (a fake
`transport`/`_open` stands in for `urllib`). No GPU, no torch, no real audio,
no network.
"""
from __future__ import annotations

import array
import json
from types import SimpleNamespace

import pytest

from anvil_serving.voice.cancel_scope import CancelScope
from anvil_serving.voice.messages import AudioOut, EndOfResponse, TTSInput
from anvil_serving.voice.serves._common import ServeNotConfigured
from anvil_serving.voice.serves.tts import TTSServe, TTSServeConfig
from anvil_serving.voice.stages.tts import (
    TTSClientError,
    TTSStage,
    TTSStageConfig,
    build_speech_request_body,
    resample_int16,
    stream_speech,
)


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
class FakeRun:
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


class FakeReadResponse:
    """Fake of an open urllib response supporting `.read(n)` in a loop --
    what `stream_speech` needs for incremental reads (no socket).

    `status` defaults to 200 so every pre-existing test using this fake
    (constructed before F3's status check existed) keeps behaving exactly as
    before -- only tests that explicitly pass a non-2xx `status` exercise the
    new rejection path.
    """

    def __init__(self, chunks, status: int = 200):
        self._chunks = list(chunks)
        self.status = status
        self.closed = False

    def read(self, n=-1):
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def close(self):
        self.closed = True


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, *, data, headers, timeout):
        self.calls.append({"url": url, "body": json.loads(data), "headers": dict(headers), "timeout": timeout})
        return self.response


def _int16_bytes(values) -> bytes:
    return array.array("h", values).tobytes()


@pytest.fixture
def manifest_with_tts(tmp_path):
    p = tmp_path / "serves.toml"
    p.write_text(
        '[[serve]]\nname = "tts"\ncontainer = "anvil-tts"\nport = 8091\n'
        'up = "echo bring-up-tts"\n',
        encoding="utf-8",
    )
    return str(p)


# --------------------------------------------------------------------------- #
# TTSServe: lifecycle (delegates to anvil_serving.serves, never raw docker)
# --------------------------------------------------------------------------- #
def test_speech_url_appends_path():
    serve = TTSServe(TTSServeConfig(base_url="http://127.0.0.1:8091/v1", model="kokoro-82m"))
    assert serve.speech_url == "http://127.0.0.1:8091/v1/audio/speech"


def test_speech_url_strips_trailing_slash():
    serve = TTSServe(TTSServeConfig(base_url="http://127.0.0.1:8091/v1/", model="kokoro-82m"))
    assert serve.speech_url == "http://127.0.0.1:8091/v1/audio/speech"


def test_bring_up_raises_serve_not_configured_when_manifest_missing(tmp_path):
    missing = str(tmp_path / "nope.toml")
    serve = TTSServe(TTSServeConfig(base_url="http://127.0.0.1:8091/v1", model="kokoro-82m",
                                     manifest_path=missing))
    with pytest.raises(ServeNotConfigured):
        serve.bring_up()


def test_bring_up_raises_serve_not_configured_when_entry_missing(tmp_path):
    p = tmp_path / "serves.toml"
    p.write_text('[[serve]]\nname = "stt"\ncontainer = "anvil-stt"\nport = 8090\n', encoding="utf-8")
    serve = TTSServe(TTSServeConfig(base_url="http://127.0.0.1:8091/v1", model="kokoro-82m",
                                     manifest_path=str(p)))
    with pytest.raises(ServeNotConfigured):
        serve.bring_up()


def test_bring_up_starts_via_up_command_when_absent(manifest_with_tts):
    fake_run = FakeRun([
        (["docker", "inspect"], 1, "", "Error: No such container: anvil-tts"),
        (["echo", "bring-up-tts"], 0, "bring-up-tts\n", ""),
    ])
    serve = TTSServe(
        TTSServeConfig(base_url="http://127.0.0.1:8091/v1", model="kokoro-82m",
                        manifest_path=manifest_with_tts),
        _run=fake_run,
    )
    rc = serve.bring_up()
    assert rc == 0
    assert ["echo", "bring-up-tts"] in fake_run.calls
    assert all(c[0] == "docker" or c == ["echo", "bring-up-tts"] for c in fake_run.calls)


def test_tear_down_stops_a_running_container(manifest_with_tts):
    fake_run = FakeRun([
        (["docker", "inspect"], 0, "running\n", ""),
        (["docker", "stop", "anvil-tts"], 0, "anvil-tts\n", ""),
    ])
    serve = TTSServe(
        TTSServeConfig(base_url="http://127.0.0.1:8091/v1", model="kokoro-82m",
                        manifest_path=manifest_with_tts),
        _run=fake_run,
    )
    rc = serve.tear_down()
    assert rc == 0
    assert ["docker", "stop", "anvil-tts"] in fake_run.calls


def test_wait_ready_true_when_models_endpoint_responds(manifest_with_tts):
    fake_run = FakeRun([(["docker", "inspect"], 0, "running\n", "")])
    serve = TTSServe(
        TTSServeConfig(base_url="http://127.0.0.1:8091/v1", model="kokoro-82m",
                        manifest_path=manifest_with_tts),
        _run=fake_run, _open=fake_open_ok,
    )
    readiness = serve.wait_ready()
    assert readiness.ready is True
    assert readiness.docker_state == "running"
    assert readiness.name == "tts"


def test_wait_ready_false_when_probe_fails(manifest_with_tts):
    fake_run = FakeRun([(["docker", "inspect"], 0, "running\n", "")])
    serve = TTSServe(
        TTSServeConfig(base_url="http://127.0.0.1:8091/v1", model="kokoro-82m",
                        manifest_path=manifest_with_tts),
        _run=fake_run, _open=fake_open_fails,
    )
    readiness = serve.wait_ready()
    assert readiness.ready is False


def test_wait_ready_reports_unconfigured_state_without_raising(tmp_path):
    missing = str(tmp_path / "nope.toml")
    serve = TTSServe(TTSServeConfig(base_url="http://127.0.0.1:8091/v1", model="kokoro-82m",
                                     manifest_path=missing), _open=fake_open_fails)
    readiness = serve.wait_ready()
    assert readiness.docker_state == "unconfigured"
    assert readiness.ready is False


# --------------------------------------------------------------------------- #
# build_speech_request_body / resample_int16
# --------------------------------------------------------------------------- #
def test_build_speech_request_body_shape():
    body = build_speech_request_body("hello there", TTSStageConfig(model="kokoro-82m"))
    assert body == {
        "model": "kokoro-82m", "input": "hello there",
        "response_format": "pcm", "stream": True,
    }


def test_resample_int16_is_noop_when_rates_match():
    pcm = _int16_bytes([1, 2, 3, 4])
    assert resample_int16(pcm, 16000, 16000) == pcm


def test_resample_int16_is_noop_on_empty_input():
    assert resample_int16(b"", 24000, 16000) == b""


def test_resample_int16_downsamples_to_expected_length():
    # 240 samples at 24kHz -> ~160 samples at 16kHz (ratio 2/3).
    pcm = _int16_bytes(range(0, 240))
    out = resample_int16(pcm, 24000, 16000)
    n_out = len(out) // 2
    assert n_out == round(240 * (16000 / 24000))


def test_resample_int16_upsamples_to_expected_length():
    pcm = _int16_bytes([0, 1000, 2000, 3000])
    out = resample_int16(pcm, 8000, 16000)
    n_out = len(out) // 2
    assert n_out == round(4 * (16000 / 8000))


def test_resample_int16_interpolates_between_samples():
    # Two samples, upsampled 1->many: values should stay within [min, max].
    pcm = _int16_bytes([0, 1000])
    out = array.array("h")
    out.frombytes(resample_int16(pcm, 8000, 16000))
    assert all(0 <= v <= 1000 for v in out)


# --------------------------------------------------------------------------- #
# stream_speech: wire construction + incremental read (hermetic)
# --------------------------------------------------------------------------- #
def test_stream_speech_posts_to_speech_path_with_pcm_format():
    audio = _int16_bytes([1, 2, 3, 4])
    transport = FakeTransport(FakeReadResponse([audio[:4], audio[4:]]))
    config = TTSStageConfig(base_url="http://127.0.0.1:8091/v1", model="kokoro-82m")
    chunks = list(stream_speech("hi there", config, transport=transport))
    assert b"".join(chunks) == audio
    call = transport.calls[0]
    assert call["url"] == "http://127.0.0.1:8091/v1/audio/speech"
    assert call["body"]["response_format"] == "pcm"
    assert call["body"]["stream"] is True


def test_stream_speech_closes_response():
    transport = FakeTransport(FakeReadResponse([b"\x00\x00"]))
    list(stream_speech("hi", TTSStageConfig(), transport=transport))
    assert transport.response.closed


def test_stream_speech_sends_bearer_token_from_env_var(monkeypatch):
    monkeypatch.setenv("ANVIL_TEST_TTS_TOKEN", "secret-tts-token")
    transport = FakeTransport(FakeReadResponse([b"\x00\x00"]))
    config = TTSStageConfig(api_key_env="ANVIL_TEST_TTS_TOKEN")
    list(stream_speech("hi", config, transport=transport))
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer secret-tts-token"


def test_stream_speech_no_token_when_env_unset(monkeypatch):
    monkeypatch.delenv("ANVIL_TEST_TTS_TOKEN_UNSET", raising=False)
    transport = FakeTransport(FakeReadResponse([b"\x00\x00"]))
    config = TTSStageConfig(api_key_env="ANVIL_TEST_TTS_TOKEN_UNSET")
    list(stream_speech("hi", config, transport=transport))
    assert "Authorization" not in transport.calls[0]["headers"]


# --------------------------------------------------------------------------- #
# F3 -- a non-2xx response must raise, never be streamed as if it were audio
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", [429, 500])
def test_stream_speech_raises_on_non_2xx_status_without_yielding_bytes(status):
    error_body = b'{"error": "rate limited"}'
    transport = FakeTransport(FakeReadResponse([error_body], status=status))
    with pytest.raises(TTSClientError):
        list(stream_speech("hi there", TTSStageConfig(), transport=transport))


def test_stream_speech_closes_response_even_on_non_2xx_status():
    transport = FakeTransport(FakeReadResponse([b"error body"], status=500))
    with pytest.raises(TTSClientError):
        list(stream_speech("hi", TTSStageConfig(), transport=transport))
    assert transport.response.closed


def test_stream_speech_still_streams_normally_on_200():
    audio = _int16_bytes([1, 2, 3, 4])
    transport = FakeTransport(FakeReadResponse([audio[:4], audio[4:]], status=200))
    chunks = list(stream_speech("hi there", TTSStageConfig(), transport=transport))
    assert b"".join(chunks) == audio


def test_tts_stage_process_fails_the_turn_on_non_2xx_without_emitting_audio():
    """End-to-end through the stage: a 500 from the TTS serve must not
    surface as an AudioOut item -- BaseStage's per-item exception isolation
    catches the raised TTSClientError, so `process()` itself must propagate
    it rather than swallow it into a (false) successful empty result."""
    scope = CancelScope()

    def fake_stream(text, config):
        # Exercise stream_speech for real so the status check actually runs.
        transport = FakeTransport(FakeReadResponse([b"server error"], status=500))
        yield from stream_speech(text, config, transport=transport)

    stage = TTSStage(in_queue=None, cancel_scope=scope, stream_fn=fake_stream)
    with pytest.raises(TTSClientError):
        # process() is a generator now (PUNCH-LIST #1): calling it just
        # builds the generator object -- iterating it (list(...)) is what
        # actually runs the body and must raise.
        list(stage.process(_tts_input()))


# --------------------------------------------------------------------------- #
# TTSStage: incremental AudioOut, resampling, barge-in abort, EndOfResponse
# --------------------------------------------------------------------------- #
def _tts_input(turn_id="t1", generation=0, text="hello"):
    return TTSInput(turn_id=turn_id, turn_revision=0, generation=generation, text=text)


def test_tts_stage_emits_resampled_audio_chunks():
    scope = CancelScope()
    audio = _int16_bytes(range(0, 480))  # 240 samples-worth chunked below

    def fake_stream(text, config):
        yield audio[:200]
        yield audio[200:]

    config = TTSStageConfig(source_sample_rate=24000, target_sample_rate=16000)
    stage = TTSStage(in_queue=None, cancel_scope=scope, config=config, stream_fn=fake_stream)
    # process() is a generator now (PUNCH-LIST #1) -- materialize ONCE so
    # both checks below see every item.
    out = list(stage.process(_tts_input()))

    assert all(isinstance(m, AudioOut) for m in out)
    assert all(m.sample_rate == 16000 for m in out)
    total_out_samples = sum(len(m.pcm) // 2 for m in out)
    total_in_samples = len(audio) // 2
    assert total_out_samples == round(total_in_samples * (16000 / 24000))


def test_tts_stage_handles_odd_byte_chunk_boundaries():
    # First "chunk" ends mid-sample (odd byte count) -- must not corrupt output.
    scope = CancelScope()
    full = _int16_bytes([10, 20, 30, 40])

    def fake_stream(text, config):
        yield full[:3]   # 1.5 samples
        yield full[3:]   # remaining 2.5 samples

    config = TTSStageConfig(source_sample_rate=16000, target_sample_rate=16000)  # no resampling noise
    stage = TTSStage(in_queue=None, cancel_scope=scope, config=config, stream_fn=fake_stream)
    out = list(stage.process(_tts_input()))
    combined = b"".join(m.pcm for m in out)
    assert combined == full


def test_tts_stage_aborts_on_mid_stream_barge_in():
    scope = CancelScope()

    def fake_stream(text, config):
        yield _int16_bytes([1, 2])
        scope.cancel()  # barge-in lands mid-synthesis
        yield _int16_bytes([3, 4])  # must not be emitted

    config = TTSStageConfig(source_sample_rate=16000, target_sample_rate=16000)
    stage = TTSStage(in_queue=None, cancel_scope=scope, config=config, stream_fn=fake_stream)
    out = list(stage.process(_tts_input()))
    combined = b"".join(m.pcm for m in out)
    assert combined == _int16_bytes([1, 2])


def test_tts_stage_skips_already_stale_input():
    scope = CancelScope()
    scope.cancel()

    def fake_stream(text, config):
        raise AssertionError("must not synthesize an already-stale TTSInput")

    stage = TTSStage(in_queue=None, cancel_scope=scope, stream_fn=fake_stream)
    assert list(stage.process(_tts_input(generation=0))) == []


def test_tts_stage_forwards_end_of_response_unchanged():
    stage = TTSStage(in_queue=None, stream_fn=lambda t, c: iter(()))
    eor = EndOfResponse(turn_id="t1", turn_revision=0, generation=0)
    assert list(stage.process(eor)) == [eor]


def test_tts_stage_ignores_non_tts_input_items():
    stage = TTSStage(in_queue=None, stream_fn=lambda t, c: iter(()))
    assert list(stage.process("not tts input")) == []

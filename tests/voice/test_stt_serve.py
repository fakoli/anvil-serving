"""Tests for the out-of-process STT serve: lifecycle
(``anvil_serving.voice.serves.stt``, anvil task T006 serve half) and stage
(``anvil_serving.voice.stages.stt``, anvil task T006 stage half).

Dependency-light and hermetic: docker is never invoked (a fake `_run`
callable stands in for `subprocess.run`) and no socket is opened (a fake
`transport`/`_open` stands in for `urllib`). No GPU, no torch, no real audio,
no network.
"""
from __future__ import annotations

import io
import json
import wave
from types import SimpleNamespace

import pytest

from anvil_serving.voice.cancel_scope import CancelScope
from anvil_serving.voice.messages import Transcription, VADAudio
from anvil_serving.voice.serves._common import ServeNotConfigured
from anvil_serving.voice.serves.stt import STTServe, STTServeConfig
from anvil_serving.voice.stages.stt import (
    STTClientError,
    STTStage,
    STTStageConfig,
    STTStreamAssembler,
    build_transcription_fields,
    pcm_to_wav,
    transcribe_stream,
)


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
class FakeRun:
    """Stands in for `subprocess.run`: matches an argv PREFIX against a table
    of canned `(returncode, stdout, stderr)` responses, in order."""

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


class FakeLineResponse:
    """Line-iterable fake of an open urllib response (no socket) -- what
    `iter_sse_events` needs (`for raw in fp`)."""

    def __init__(self, payload: bytes):
        self._fp = io.BytesIO(payload)
        self.closed = False

    def __iter__(self):
        return iter(self._fp)

    def close(self) -> None:
        self.closed = True


class FakeReadResponse:
    """Fake of a non-streaming response: just `.read()`/`.close()`."""

    def __init__(self, payload: bytes):
        self._fp = io.BytesIO(payload)
        self.closed = False

    def read(self, *a, **kw):
        return self._fp.read(*a, **kw)

    def close(self) -> None:
        self.closed = True


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, *, data, headers, timeout):
        self.calls.append({"url": url, "data": data, "headers": dict(headers), "timeout": timeout})
        return self.response


def _sse_transcript(*events: dict, done_event: dict) -> bytes:
    out = b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)
    out += b"data: " + json.dumps(done_event).encode() + b"\n\n"
    return out


@pytest.fixture
def manifest_with_stt(tmp_path):
    p = tmp_path / "serves.toml"
    p.write_text(
        '[[serve]]\nname = "stt"\ncontainer = "anvil-stt"\nport = 8090\n'
        'up = "echo bring-up-stt"\n',
        encoding="utf-8",
    )
    return str(p)


# --------------------------------------------------------------------------- #
# STTServe: lifecycle (delegates to anvil_serving.serves, never raw docker)
# --------------------------------------------------------------------------- #
def test_transcriptions_url_appends_path():
    serve = STTServe(STTServeConfig(base_url="http://127.0.0.1:8090/v1", model="parakeet"))
    assert serve.transcriptions_url == "http://127.0.0.1:8090/v1/audio/transcriptions"


def test_transcriptions_url_strips_trailing_slash():
    serve = STTServe(STTServeConfig(base_url="http://127.0.0.1:8090/v1/", model="parakeet"))
    assert serve.transcriptions_url == "http://127.0.0.1:8090/v1/audio/transcriptions"


def test_bring_up_raises_serve_not_configured_when_manifest_missing(tmp_path):
    missing = str(tmp_path / "does-not-exist.toml")
    serve = STTServe(STTServeConfig(base_url="http://127.0.0.1:8090/v1", model="parakeet",
                                     manifest_path=missing))
    with pytest.raises(ServeNotConfigured):
        serve.bring_up()


def test_bring_up_raises_serve_not_configured_when_entry_missing(tmp_path):
    p = tmp_path / "serves.toml"
    p.write_text('[[serve]]\nname = "tts"\ncontainer = "anvil-tts"\nport = 8091\n', encoding="utf-8")
    serve = STTServe(STTServeConfig(base_url="http://127.0.0.1:8090/v1", model="parakeet",
                                     manifest_path=str(p)))
    with pytest.raises(ServeNotConfigured):
        serve.bring_up()


def test_bring_up_never_shells_out_to_docker_directly_when_absent_starts_via_up(manifest_with_stt):
    fake_run = FakeRun([
        (["docker", "inspect"], 1, "", "Error: No such container: anvil-stt"),
        (["echo", "bring-up-stt"], 0, "bring-up-stt\n", ""),
    ])
    serve = STTServe(
        STTServeConfig(base_url="http://127.0.0.1:8090/v1", model="parakeet",
                        manifest_path=manifest_with_stt),
        _run=fake_run,
    )
    rc = serve.bring_up()
    assert rc == 0
    assert ["echo", "bring-up-stt"] in fake_run.calls
    # every call went through the fake, not a real subprocess -- no bare "docker run".
    assert all(c[0] == "docker" or c == ["echo", "bring-up-stt"] for c in fake_run.calls)


def test_tear_down_stops_a_running_container(manifest_with_stt):
    fake_run = FakeRun([
        (["docker", "inspect"], 0, "running\n", ""),
        (["docker", "stop", "anvil-stt"], 0, "anvil-stt\n", ""),
    ])
    serve = STTServe(
        STTServeConfig(base_url="http://127.0.0.1:8090/v1", model="parakeet",
                        manifest_path=manifest_with_stt),
        _run=fake_run,
    )
    rc = serve.tear_down()
    assert rc == 0
    assert ["docker", "stop", "anvil-stt"] in fake_run.calls


def test_tear_down_raises_serve_not_configured_when_manifest_missing(tmp_path):
    missing = str(tmp_path / "nope.toml")
    serve = STTServe(STTServeConfig(base_url="http://127.0.0.1:8090/v1", model="parakeet",
                                     manifest_path=missing))
    with pytest.raises(ServeNotConfigured):
        serve.tear_down()


def test_wait_ready_true_when_models_endpoint_responds(manifest_with_stt):
    fake_run = FakeRun([(["docker", "inspect"], 0, "running\n", "")])
    serve = STTServe(
        STTServeConfig(base_url="http://127.0.0.1:8090/v1", model="parakeet",
                        manifest_path=manifest_with_stt),
        _run=fake_run, _open=fake_open_ok,
    )
    readiness = serve.wait_ready()
    assert readiness.ready is True
    assert readiness.docker_state == "running"
    assert readiness.name == "stt"


def test_wait_ready_false_when_probe_fails(manifest_with_stt):
    fake_run = FakeRun([(["docker", "inspect"], 0, "running\n", "")])
    serve = STTServe(
        STTServeConfig(base_url="http://127.0.0.1:8090/v1", model="parakeet",
                        manifest_path=manifest_with_stt),
        _run=fake_run, _open=fake_open_fails,
    )
    readiness = serve.wait_ready()
    assert readiness.ready is False
    assert "not responding" in readiness.detail


def test_wait_ready_reports_unconfigured_state_without_raising(tmp_path):
    missing = str(tmp_path / "nope.toml")
    serve = STTServe(STTServeConfig(base_url="http://127.0.0.1:8090/v1", model="parakeet",
                                     manifest_path=missing), _open=fake_open_fails)
    readiness = serve.wait_ready()  # must not raise even though the manifest is absent
    assert readiness.docker_state == "unconfigured"
    assert readiness.ready is False


# --------------------------------------------------------------------------- #
# pcm_to_wav / multipart fields
# --------------------------------------------------------------------------- #
def test_pcm_to_wav_produces_a_valid_wav_header_with_declared_rate():
    wav_bytes = pcm_to_wav(b"\x01\x00\x02\x00", sample_rate=16000)
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        assert w.getframerate() == 16000
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.readframes(w.getnframes()) == b"\x01\x00\x02\x00"


def test_build_transcription_fields_includes_model_and_stream_flag():
    fields = build_transcription_fields(STTStageConfig(model="parakeet-tdt", stream=True))
    assert fields["model"] == "parakeet-tdt"
    assert fields["stream"] == "true"


def test_build_transcription_fields_omits_stream_when_disabled():
    fields = build_transcription_fields(STTStageConfig(stream=False))
    assert "stream" not in fields


# --------------------------------------------------------------------------- #
# STTStreamAssembler
# --------------------------------------------------------------------------- #
def test_stream_assembler_accumulates_incremental_deltas():
    a = STTStreamAssembler()
    assert a.feed(None, json.dumps({"type": "transcript.text.delta", "delta": "hel"})) == ("hel", False)
    assert a.feed(None, json.dumps({"type": "transcript.text.delta", "delta": "lo"})) == ("hello", False)
    assert a.feed(None, json.dumps({"type": "transcript.text.done", "text": "hello there"})) == (
        "hello there", True,
    )
    assert a.done is True


def test_stream_assembler_ignores_unrecognized_event_types():
    a = STTStreamAssembler()
    assert a.feed(None, json.dumps({"type": "something.else"})) is None
    assert a.feed(None, "not json") is None


# --------------------------------------------------------------------------- #
# transcribe_stream: wire construction + SSE assembly (hermetic)
# --------------------------------------------------------------------------- #
def test_transcribe_stream_posts_multipart_to_transcriptions_path():
    payload = _sse_transcript(
        {"type": "transcript.text.delta", "delta": "hel"},
        done_event={"type": "transcript.text.done", "text": "hello"},
    )
    transport = FakeTransport(FakeLineResponse(payload))
    config = STTStageConfig(base_url="http://127.0.0.1:8090/v1", model="parakeet")
    results = list(transcribe_stream(b"\x00\x01" * 100, 16000, config, transport=transport))
    assert results == [("hel", False), ("hello", True)]
    call = transport.calls[0]
    assert call["url"] == "http://127.0.0.1:8090/v1/audio/transcriptions"
    assert call["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b"Content-Disposition: form-data; name=\"file\"" in call["data"]
    assert b"RIFF" in call["data"]  # the WAV container header


def test_transcribe_stream_closes_response():
    payload = _sse_transcript(done_event={"type": "transcript.text.done", "text": "hi"})
    transport = FakeTransport(FakeLineResponse(payload))
    list(transcribe_stream(b"\x00\x00", 16000, STTStageConfig(), transport=transport))
    assert transport.response.closed


def test_transcribe_stream_non_streaming_returns_single_final_result():
    transport = FakeTransport(FakeReadResponse(json.dumps({"text": "non streaming result"}).encode()))
    config = STTStageConfig(stream=False)
    results = list(transcribe_stream(b"\x00\x00", 16000, config, transport=transport))
    assert results == [("non streaming result", True)]


def test_transcribe_stream_non_streaming_raises_on_non_json_body():
    """F6 regression: a non-JSON body must raise, not silently succeed empty."""
    transport = FakeTransport(FakeReadResponse(b"not json at all"))
    config = STTStageConfig(stream=False)
    with pytest.raises(STTClientError):
        list(transcribe_stream(b"\x00\x00", 16000, config, transport=transport))


def test_transcribe_stream_non_streaming_raises_on_missing_text_field():
    """F6 regression: valid JSON with an unexpected shape (no 'text' field)
    must raise rather than being treated as a successful empty transcription."""
    transport = FakeTransport(FakeReadResponse(json.dumps({"error": "bad request"}).encode()))
    config = STTStageConfig(stream=False)
    with pytest.raises(STTClientError):
        list(transcribe_stream(b"\x00\x00", 16000, config, transport=transport))


def test_transcribe_stream_non_streaming_raises_when_text_is_not_a_string():
    transport = FakeTransport(FakeReadResponse(json.dumps({"text": None}).encode()))
    config = STTStageConfig(stream=False)
    with pytest.raises(STTClientError):
        list(transcribe_stream(b"\x00\x00", 16000, config, transport=transport))


def test_transcribe_stream_non_streaming_succeeds_on_valid_empty_transcription():
    """The legitimately-empty-but-valid case must still succeed (not raise)."""
    transport = FakeTransport(FakeReadResponse(json.dumps({"text": ""}).encode()))
    config = STTStageConfig(stream=False)
    results = list(transcribe_stream(b"\x00\x00", 16000, config, transport=transport))
    assert results == [("", True)]


def test_stt_stage_propagates_non_streaming_malformed_response_error():
    """End-to-end through the stage: a malformed non-streaming STT response
    must propagate as an error out of `process()`, not a false empty
    Transcription -- BaseStage's per-item isolation is what actually stops it
    from wedging the pipeline, but `process()` itself must not swallow it."""
    scope = CancelScope()

    def fake_stream(pcm, sample_rate, config):
        transport = FakeTransport(FakeReadResponse(b"not json"))
        yield from transcribe_stream(pcm, sample_rate, STTStageConfig(stream=False), transport=transport)

    stage = STTStage(in_queue=None, cancel_scope=scope, stream_fn=fake_stream)
    with pytest.raises(STTClientError):
        stage.process(_vad_audio())


def test_transcribe_stream_sends_bearer_token_from_env_var(monkeypatch):
    monkeypatch.setenv("ANVIL_TEST_STT_TOKEN", "secret-stt-token")
    transport = FakeTransport(FakeLineResponse(
        _sse_transcript(done_event={"type": "transcript.text.done", "text": "hi"})
    ))
    config = STTStageConfig(api_key_env="ANVIL_TEST_STT_TOKEN")
    list(transcribe_stream(b"\x00\x00", 16000, config, transport=transport))
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer secret-stt-token"


# --------------------------------------------------------------------------- #
# STTStage: partial + final Transcription, cancel_scope integration
# --------------------------------------------------------------------------- #
def _vad_audio(turn_id="t1", generation=0, pcm=b"\x00\x01"):
    return VADAudio(turn_id=turn_id, turn_revision=0, generation=generation, pcm=pcm, is_final=True)


def test_stt_stage_emits_partials_then_final_transcription():
    scope = CancelScope()

    def fake_stream(pcm, sample_rate, config):
        yield "hel", False
        yield "hello", False
        yield "hello there", True

    stage = STTStage(in_queue=None, cancel_scope=scope, stream_fn=fake_stream)
    out = stage.process(_vad_audio())

    assert [m.text for m in out] == ["hel", "hello", "hello there"]
    assert [m.is_final for m in out] == [False, False, True]
    assert all(isinstance(m, Transcription) for m in out)
    assert out[0].turn_id == "t1"


def test_stt_stage_skips_already_stale_segment():
    scope = CancelScope()
    scope.cancel()  # generation now 1

    def fake_stream(pcm, sample_rate, config):
        raise AssertionError("must not transcribe an already-stale segment")

    stage = STTStage(in_queue=None, cancel_scope=scope, stream_fn=fake_stream)
    assert stage.process(_vad_audio(generation=0)) is None


def test_stt_stage_stops_emitting_after_mid_stream_barge_in():
    scope = CancelScope()

    def fake_stream(pcm, sample_rate, config):
        yield "first", False
        scope.cancel()  # barge-in lands mid-transcription
        yield "must not be emitted", False
        yield "must not be emitted either", True

    stage = STTStage(in_queue=None, cancel_scope=scope, stream_fn=fake_stream)
    out = stage.process(_vad_audio())
    assert [m.text for m in out] == ["first"]
    assert out[-1].is_final is False


def test_stt_stage_ignores_non_final_vad_audio():
    stage = STTStage(in_queue=None, stream_fn=lambda p, sr, c: iter(()))
    non_final = VADAudio(turn_id="t", turn_revision=0, generation=0, pcm=b"x", is_final=False)
    assert stage.process(non_final) is None


def test_stt_stage_ignores_non_vad_audio_items():
    stage = STTStage(in_queue=None, stream_fn=lambda p, sr, c: iter(()))
    assert stage.process("not vad audio") is None

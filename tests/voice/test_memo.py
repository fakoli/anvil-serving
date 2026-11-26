"""Voice memo capture: client contract, gateway transcription, fail-closed config (ADR-0048)."""
import base64
import json
import os
import re
import shutil
import struct
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from anvil_serving.voice import corpus, memo

LOGIN = "owner@example.test"
STUDIO = "http://127.0.0.1:51358"


def multipart(fields):
    boundary = "testboundary123"
    parts = []
    for name, (filename, data) in fields.items():
        disposition = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else "")
        parts.append(f"--{boundary}\r\nContent-Disposition: {disposition}\r\n\r\n".encode() + data + b"\r\n")
    return f"multipart/form-data; boundary={boundary}", b"".join(parts) + f"--{boundary}--\r\n".encode()


def config(tmp_path, **overrides):
    values = dict(router_url="http://127.0.0.1:9", router_key="ask_synthetic",
                  allowed_logins=frozenset({LOGIN}), data_dir=tmp_path / "memos", listen_port=0)
    values.update(overrides)
    return memo.MemoConfig(**values)


def write_manifest(tmp_path, table):
    table = {"data_dir": str(tmp_path / "memos"), **table}  # absolute on every platform
    lines = ["[voice.memo]"] + [f"{k} = {json.dumps(v)}" for k, v in table.items()]
    path = tmp_path / "voice.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


BASE = {"router_url": "http://127.0.0.1:8000/v1", "router_key_env": "MEMO_KEY",
        "allowed_logins_env": "MEMO_LOGINS"}
ENV = {"MEMO_KEY": "ask_synthetic", "MEMO_LOGINS": f"{LOGIN}, second@example.test"}


def test_config_resolves_references_and_strips_v1(tmp_path):
    loaded = memo.load_memo_config(write_manifest(tmp_path, {**BASE, "route": "dark-stt"}), env=ENV)
    assert loaded.router_url == "http://127.0.0.1:8000"
    assert loaded.allowed_logins == {LOGIN, "second@example.test"}
    assert loaded.route == "dark-stt" and loaded.listen_host == "127.0.0.1"
    assert "ask_synthetic" not in repr(loaded) and LOGIN not in repr(loaded)


@pytest.mark.parametrize("table,env,message", [
    (BASE, {"MEMO_KEY": "ask_synthetic"}, "MEMO_LOGINS"),
    (BASE, {"MEMO_KEY": "ask_synthetic", "MEMO_LOGINS": " , "}, "no logins"),
    (BASE, {"MEMO_LOGINS": LOGIN}, "MEMO_KEY"),
    ({**BASE, "listen_host": "0.0.0.0"}, ENV, "loopback"),
    ({**BASE, "data_dir": "relative/memos"}, ENV, "absolute"),
    ({**BASE, "segment_seconds": 600}, ENV, "segment_seconds"),
    ({**BASE, "router_url": "ftp://127.0.0.1"}, ENV, "router_url"),
    ({**BASE, "upstream_url": "http://127.0.0.1:30010"}, ENV, "unknown"),
    ({**BASE, "router_key_file": "/run/secrets/voice-memo.key"}, ENV, "exactly one"),
    ({k: v for k, v in BASE.items() if k != "router_key_env"}, ENV, "exactly one"),
    ({**{k: v for k, v in BASE.items() if k != "router_key_env"}, "router_key_file": "relative.key"}, ENV,
     "router_key_file"),
])
def test_config_fails_closed(tmp_path, table, env, message):
    with pytest.raises(memo.ConfigError, match=message):
        memo.load_memo_config(write_manifest(tmp_path, table), env=env)


@pytest.mark.skipif(os.name != "posix", reason="owner-only file mode is the POSIX privacy contract")
def test_config_reads_protected_key_file(tmp_path):
    key = tmp_path / "voice-memo.key"
    key.write_text("ask_from_file\n", encoding="utf-8")
    key.chmod(0o600)
    table = {k: v for k, v in BASE.items() if k != "router_key_env"}
    loaded = memo.load_memo_config(write_manifest(tmp_path, {**table, "router_key_file": str(key)}), env=ENV)
    assert loaded.router_key == "ask_from_file"
    key.chmod(0o644)
    with pytest.raises(memo.ConfigError, match="private"):
        memo.load_memo_config(write_manifest(tmp_path, {**table, "router_key_file": str(key)}), env=ENV)


def test_parse_multipart():
    ctype, body = multipart({"audio": ("m.ogg", b"OggS\x00\x01binary"), "durationMs": (None, b"1234")})
    fields = memo.parse_multipart(ctype, body)
    assert fields["audio"] == ("m.ogg", b"OggS\x00\x01binary") and fields["durationMs"][1] == b"1234"
    with pytest.raises(ValueError):
        memo.parse_multipart("application/json", b"{}")


def test_split_wav_preserves_samples_within_gateway_limit():
    pcm = struct.pack("<h", 7) * (16000 * 250)  # 250 s
    segments = memo.split_wav(memo.wav_header(len(pcm)) + pcm, seconds=110)
    assert len(segments) == 3
    for segment in segments:
        assert segment[:4] == b"RIFF" and struct.unpack("<I", segment[40:44])[0] == len(segment) - 44
    assert sum(len(s) - 44 for s in segments) == len(pcm)
    longest = memo.MAX_SEGMENT_SECONDS * memo.BYTES_PER_SECOND + 44
    assert longest <= 4 * 1024 * 1024  # router default audio_max_input_bytes


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_ogg_to_wav_rejects_garbage():
    with pytest.raises(ValueError):
        memo.ogg_to_wav(b"not audio")


class FakeGateway(BaseHTTPRequestHandler):
    seen = []
    status = 200

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append((self.path, self.headers["Authorization"], body))
        if self.path == "/v1/chat/completions":
            data = json.dumps({"choices": [{"message": {"content": "<think>plan</think>\n Polished memo. "}}]}).encode()
        else:
            data = json.dumps({"text": " hello memo ", "is_final": True}).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def fake_gateway():
    FakeGateway.seen, FakeGateway.status = [], 200
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGateway)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_segment_goes_to_gateway_with_device_key_and_route(tmp_path, fake_gateway):
    wav = memo.wav_header(4) + b"\x00\x01\x02\x03"
    cfg = config(tmp_path, router_url=fake_gateway, route="dark-stt")
    assert memo.transcribe_segment(cfg, wav) == "hello memo"
    [(path, auth, body)] = FakeGateway.seen
    assert path == "/v1/audio/transcriptions" and auth == "Bearer ask_synthetic"
    assert body == {"purpose": "stt", "route": "dark-stt", "format": "wav", "is_final": True,
                    "audio_b64": base64.b64encode(wav).decode()}


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_real_opus_memo_is_segmented_into_16k_mono_wav(tmp_path, fake_gateway):
    import subprocess
    import wave
    import io
    ogg = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
         "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-f", "ogg", "pipe:1"],
        capture_output=True, check=True, timeout=60).stdout
    cfg = config(tmp_path, router_url=fake_gateway, segment_seconds=2)
    assert memo.transcribe_memo(cfg, ogg) == "hello memo hello memo hello memo"
    for _, _, body in FakeGateway.seen:
        with wave.open(io.BytesIO(base64.b64decode(body["audio_b64"]))) as segment:
            assert (segment.getframerate(), segment.getnchannels(), segment.getsampwidth()) == (16000, 1, 2)
            assert segment.getnframes() <= 2 * 16000


def test_gateway_denial_is_a_transcription_error(tmp_path, fake_gateway):
    FakeGateway.status = 403
    with pytest.raises(memo.TranscriptionError, match="403"):
        memo.transcribe_segment(config(tmp_path, router_url=fake_gateway), memo.wav_header(2) + b"\x00\x00")


@pytest.fixture
def service(tmp_path):
    cfg = config(tmp_path)
    server = memo.make_memo_server(cfg, transcribe=lambda ogg: f"heard {len(ogg)} bytes")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", cfg.data_dir
    server.shutdown()
    server.server_close()


def request(base, method, path, body=None, headers=None):
    req = urllib.request.Request(base + path, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def test_upload_transcribes_stores_and_allows_studio_origin(service):
    base, store = service
    ctype, body = multipart({"audio": ("m.ogg", b"x" * 100), "durationMs": (None, b"2000")})
    status, headers, data = request(base, "POST", "/v1/memos", body, {
        "Content-Type": ctype, "Tailscale-User-Login": LOGIN, "Origin": STUDIO})
    assert status == 200
    stored = json.loads(data)
    assert stored["transcript"] == "heard 100 bytes" and stored["durationMs"] == 2000
    assert re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{6}", stored["id"])
    assert headers["Access-Control-Allow-Origin"] == STUDIO
    assert (store / (stored["id"] + ".ogg")).exists() and (store / (stored["id"] + ".json")).exists()


def test_preview_transcribes_without_storing(service):
    base, store = service
    before = set(store.iterdir())
    ctype, body = multipart({"audio": ("p.ogg", b"y" * 42)})
    status, _, data = request(base, "POST", "/v1/memos/preview", body, {
        "Content-Type": ctype, "Tailscale-User-Login": LOGIN, "Origin": STUDIO})
    assert status == 200 and json.loads(data) == {"transcript": "heard 42 bytes"}
    assert set(store.iterdir()) == before


def test_refuses_unknown_login_origin_and_oversize(service):
    base, _ = service
    ctype, body = multipart({"audio": ("m.ogg", b"x")})
    assert request(base, "POST", "/v1/memos", body, {
        "Content-Type": ctype, "Tailscale-User-Login": "stranger@example.test"})[0] == 403
    assert request(base, "POST", "/v1/memos", body, {"Content-Type": ctype})[0] == 403
    assert request(base, "POST", "/v1/memos", body, {
        "Content-Type": ctype, "Tailscale-User-Login": LOGIN, "Origin": "https://evil.example"})[0] == 403
    assert request(base, "POST", "/v1/memos/preview", b"", {
        "Content-Type": ctype, "Tailscale-User-Login": LOGIN})[0] == 413


def test_failed_transcription_keeps_audio(tmp_path):
    def broken(ogg):
        raise memo.TranscriptionError("gateway returned HTTP 503")
    cfg = config(tmp_path)
    server = memo.make_memo_server(cfg, transcribe=broken)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ctype, body = multipart({"audio": ("m.ogg", b"z" * 10)})
        status, _, data = request(f"http://127.0.0.1:{server.server_port}", "POST", "/v1/memos", body,
                                  {"Content-Type": ctype, "Tailscale-User-Login": LOGIN})
    finally:
        server.shutdown()
        server.server_close()
    assert status == 502
    assert (cfg.data_dir / (json.loads(data)["id"] + ".ogg")).read_bytes() == b"z" * 10


def test_preflight_and_health(service):
    base, _ = service
    status, headers, _ = request(base, "OPTIONS", "/v1/memos", headers={"Origin": "http://127.0.0.1:4000"})
    assert status == 204 and headers["Access-Control-Allow-Methods"] == "GET, POST"
    assert request(base, "OPTIONS", "/v1/memos", headers={"Origin": "https://evil.example"})[0] == 403
    assert request(base, "GET", "/healthz")[0] == 200


NOTE = memo.Mode(label="Clean note", model="llm.voice", prompt="Rewrite as a clean note.")


def test_config_parses_modes_and_rejects_bad_ones(tmp_path):
    path = tmp_path / "voice.toml"
    path.write_text(
        "[voice.memo]\n" + "\n".join(f"{k} = {json.dumps(v)}" for k, v in
                                     {**BASE, "data_dir": str(tmp_path / "m")}.items())
        + '\n[voice.memo.modes.note]\nlabel = "Clean note"\nmodel = "llm.voice"\nprompt = "Rewrite."\n',
        encoding="utf-8")
    loaded = memo.load_memo_config(str(path), env=ENV)
    assert loaded.modes == {"note": memo.Mode("Clean note", "llm.voice", "Rewrite.")}
    assert "llm.voice" not in repr(loaded.modes["note"])
    for bad in ('[voice.memo.modes.Note]\nmodel = "llm.voice"\nprompt = "x"\n',
                '[voice.memo.modes.note]\nprompt = "x"\n',
                '[voice.memo.modes.note]\nmodel = "llm.voice"\nprompt = " "\n',
                '[voice.memo.modes.note]\nmodel = "llm.voice"\nprompt = "x"\nurl = "http://127.0.0.1:1"\n'):
        path.write_text(path.read_text(encoding="utf-8").split("[voice.memo.modes")[0] + bad, encoding="utf-8")
        with pytest.raises(memo.ConfigError, match="modes"):
            memo.load_memo_config(str(path), env=ENV)


def test_reference_example_modes_are_valid():
    import tomllib
    from pathlib import Path
    example = Path(__file__).resolve().parents[2] / "examples" / "primary-node" / "voice.toml"
    modes = memo._modes(tomllib.loads(example.read_text(encoding="utf-8"))["voice"]["memo"])
    assert set(modes) == {"note", "message", "minutes", "tasks", "clarify"}
    assert {mode.model for mode in modes.values()} == {"llm.voice"}


def test_conversion_goes_through_gateway_chat_with_mode_alias(tmp_path, fake_gateway):
    cfg = config(tmp_path, router_url=fake_gateway)
    assert memo.convert_transcript(cfg, NOTE, "um so buy milk") == "Polished memo."
    [(path, auth, body)] = FakeGateway.seen
    assert path == "/v1/chat/completions" and auth == "Bearer ask_synthetic"
    assert body == {"model": "llm.voice", "stream": False, "messages": [
        {"role": "system", "content": "Rewrite as a clean note."}, {"role": "user", "content": "um so buy milk"}]}


@pytest.fixture
def mode_service(tmp_path):
    calls = []

    def convert(mode, text):
        calls.append((mode.label, text))
        if text == "heard 13 bytes":
            raise memo.TranscriptionError("gateway returned HTTP 504")
        return f"[{mode.label}] {text}"

    cfg = config(tmp_path, modes={"note": NOTE})
    server = memo.make_memo_server(cfg, transcribe=lambda ogg: f"heard {len(ogg)} bytes", convert=convert)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", cfg.data_dir, calls
    server.shutdown()
    server.server_close()


def test_modes_are_listed_without_prompts_or_aliases(mode_service):
    base, _, _ = mode_service
    status, headers, data = request(base, "GET", "/v1/memos/modes",
                                    headers={"Tailscale-User-Login": LOGIN, "Origin": STUDIO})
    assert status == 200 and json.loads(data) == {"modes": [{"id": "note", "label": "Clean note"}]}
    assert headers["Access-Control-Allow-Origin"] == STUDIO
    assert request(base, "GET", "/v1/memos/modes")[0] == 403


def test_memo_with_mode_stores_raw_and_converted(mode_service):
    base, store, calls = mode_service
    ctype, body = multipart({"audio": ("m.ogg", b"x" * 7), "mode": (None, b"note")})
    status, _, data = request(base, "POST", "/v1/memos", body, {"Content-Type": ctype, "Tailscale-User-Login": LOGIN})
    stored = json.loads(data)
    assert status == 200 and stored["transcript"] == "heard 7 bytes" and stored["mode"] == "note"
    assert stored["converted"] == "[Clean note] heard 7 bytes"
    assert json.loads((store / (stored["id"] + ".json")).read_text(encoding="utf-8")) == stored
    assert calls == [("Clean note", "heard 7 bytes")]


def test_failed_conversion_still_returns_and_keeps_raw_transcript(mode_service):
    base, store, _ = mode_service
    ctype, body = multipart({"audio": ("m.ogg", b"x" * 13), "mode": (None, b"note")})
    status, _, data = request(base, "POST", "/v1/memos", body, {"Content-Type": ctype, "Tailscale-User-Login": LOGIN})
    stored = json.loads(data)
    assert status == 200 and stored["transcript"] == "heard 13 bytes" and stored["converted"] is None
    assert json.loads((store / (stored["id"] + ".json")).read_text(encoding="utf-8"))["converted"] is None


def test_unknown_mode_is_refused_before_storing_and_preview_ignores_mode(mode_service):
    base, store, calls = mode_service
    before = set(store.iterdir())
    ctype, body = multipart({"audio": ("m.ogg", b"x" * 5), "mode": (None, b"sonnet")})
    assert request(base, "POST", "/v1/memos", body, {"Content-Type": ctype, "Tailscale-User-Login": LOGIN})[0] == 400
    status, _, data = request(base, "POST", "/v1/memos/preview", body,
                              {"Content-Type": ctype, "Tailscale-User-Login": LOGIN})
    assert status == 200 and json.loads(data) == {"transcript": "heard 5 bytes"}
    assert set(store.iterdir()) == before and calls == []


def test_store_is_marked_and_refused_by_corpus_validation(service):
    _, store = service
    assert (store / corpus.EVALUATION_EXCLUDED_MARKER).is_file()
    manifest = store / "cases.jsonl"
    with pytest.raises(corpus.CorpusError, match="evaluation-excluded"):
        corpus._safe_audio_path(manifest, "memo.wav")

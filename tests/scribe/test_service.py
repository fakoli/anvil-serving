"""Anvil Scribe: record first, rewrite later, history (ADR-0048)."""
import base64
import io
import json
import os
import re
import shutil
import struct
import subprocess
import threading
import urllib.error
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from anvil_serving.scribe import cli as scribe_cli
from anvil_serving.scribe import service as memo
from anvil_serving.voice import corpus

LOGIN = "owner@example.test"
STUDIO = "http://127.0.0.1:51358"
AUTH = {"Tailscale-User-Login": LOGIN, "Origin": STUDIO}
TARGETS = {"voice": memo.Target("Voice LLM", "llm.voice", "small", True),
           "primary": memo.Target("Primary", "llm.primary", "large", False)}


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
    return memo.ScribeConfig(**values)


def write_manifest(tmp_path, table, extra=""):
    table = {"data_dir": str(tmp_path / "memos"), **table}  # absolute on every platform
    lines = ["[scribe]"] + [f"{k} = {json.dumps(v)}" for k, v in table.items()]
    path = tmp_path / "voice.toml"
    path.write_text("\n".join(lines) + "\n" + extra, encoding="utf-8")
    return str(path)


BASE = {"router_url": "http://127.0.0.1:8000/v1", "router_key_env": "MEMO_KEY",
        "allowed_logins_env": "MEMO_LOGINS"}
ENV = {"MEMO_KEY": "ask_synthetic", "MEMO_LOGINS": f"{LOGIN}, second@example.test"}


# --- configuration --------------------------------------------------------

def test_config_resolves_references_targets_and_strips_v1(tmp_path):
    extra = ('[scribe.targets.voice]\nlabel = "Voice"\nmodel = "llm.voice"\ndefault = true\n'
             '[scribe.targets.primary]\nmodel = "llm.primary"\ndetail = "big"\n')
    loaded = memo.load_config(write_manifest(tmp_path, {**BASE, "route": "dark-stt"}, extra), env=ENV)
    assert loaded.router_url == "http://127.0.0.1:8000"
    assert loaded.allowed_logins == {LOGIN, "second@example.test"}
    assert loaded.route == "dark-stt" and loaded.listen_host == "127.0.0.1"
    assert loaded.targets == {"voice": memo.Target("Voice", "llm.voice", "", True),
                              "primary": memo.Target("primary", "llm.primary", "big", False)}
    assert "ask_synthetic" not in repr(loaded) and LOGIN not in repr(loaded) and "llm.voice" not in repr(loaded)


def test_single_target_becomes_the_default(tmp_path):
    loaded = memo.load_config(write_manifest(tmp_path, BASE, '[scribe.targets.voice]\nmodel = "llm.voice"\n'),
                                   env=ENV)
    assert loaded.targets["voice"].default is True


@pytest.mark.parametrize("table,env,message", [
    (BASE, {"MEMO_KEY": "ask_synthetic"}, "MEMO_LOGINS"),
    (BASE, {"MEMO_KEY": "ask_synthetic", "MEMO_LOGINS": " , "}, "no logins"),
    (BASE, {"MEMO_LOGINS": LOGIN}, "MEMO_KEY"),
    ({**BASE, "listen_host": "0.0.0.0"}, ENV, "loopback"),
    ({**BASE, "data_dir": "relative/memos"}, ENV, "absolute"),
    ({**BASE, "segment_seconds": 600}, ENV, "segment_seconds"),
    ({**BASE, "router_url": "ftp://127.0.0.1"}, ENV, "router_url"),
    ({**BASE, "router_url": "http://127.0.0.1:notaport"}, ENV, "router_url"),
    ({**BASE, "router_url": "http://127.0.0.1:70000"}, ENV, "router_url"),
    ({**BASE, "listen_host": "::1"}, ENV, "IPv4 loopback"),
    ({**BASE, "upstream_url": "http://127.0.0.1:30010"}, ENV, "unknown"),
    ({**BASE, "router_key_file": "/run/secrets/scribe.key"}, ENV, "exactly one"),
    ({k: v for k, v in BASE.items() if k != "router_key_env"}, ENV, "exactly one"),
    ({**{k: v for k, v in BASE.items() if k != "router_key_env"}, "router_key_file": "relative.key"}, ENV,
     "router_key_file"),
])
def test_config_fails_closed(tmp_path, table, env, message):
    with pytest.raises(memo.ConfigError, match=message):
        memo.load_config(write_manifest(tmp_path, table), env=env)


@pytest.mark.parametrize("extra", [
    '[scribe.targets.Voice]\nmodel = "llm.voice"\n',
    '[scribe.targets.voice]\nlabel = "no model"\n',
    '[scribe.targets.voice]\nmodel = "llm.voice"\nurl = "http://127.0.0.1:1"\n',
    '[scribe.targets.a]\nmodel = "llm.voice"\n[scribe.targets.b]\nmodel = "llm.primary"\n',
])
def test_bad_targets_are_refused(tmp_path, extra):
    with pytest.raises(memo.ConfigError, match="targets"):
        memo.load_config(write_manifest(tmp_path, BASE, extra), env=ENV)


def test_reference_example_targets_are_valid():
    import tomllib
    example = Path(__file__).resolve().parents[2] / "examples" / "primary-node" / "scribe.toml"
    targets = memo._targets(tomllib.loads(example.read_text(encoding="utf-8"))["scribe"])
    assert [t for t, v in targets.items() if v.default] == ["voice"]


@pytest.mark.skipif(os.name != "posix", reason="owner-only file mode is the POSIX privacy contract")
def test_config_reads_protected_key_file(tmp_path):
    key = tmp_path / "scribe.key"
    key.write_text("ask_from_file\n", encoding="utf-8")
    key.chmod(0o600)
    table = {k: v for k, v in BASE.items() if k != "router_key_env"}
    loaded = memo.load_config(write_manifest(tmp_path, {**table, "router_key_file": str(key)}), env=ENV)
    assert loaded.router_key == "ask_from_file"
    key.chmod(0o644)
    with pytest.raises(memo.ConfigError, match="private"):
        memo.load_config(write_manifest(tmp_path, {**table, "router_key_file": str(key)}), env=ENV)


# --- audio and gateway ----------------------------------------------------

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
    assert memo.MAX_SEGMENT_SECONDS * memo.BYTES_PER_SECOND + 44 <= 4 * 1024 * 1024  # gateway default


def test_multipart_part_floods_are_refused_before_parsing():
    many = b"".join(b"--b\r\nContent-Disposition: form-data; name=\"f%d\"\r\n\r\nx\r\n" % i for i in range(17))
    # Every spelling the parser accepts is counted, including ones a regex would misread.
    for ctype in ("multipart/form-data; boundary=b", 'multipart/form-data; boundary="b"',
                  "multipart/form-data; xboundary=zz; boundary=b", "multipart/form-data; boundary*=utf-8''b"):
        with pytest.raises(ValueError, match="too many"):
            memo.parse_multipart(ctype, many + b"--b--\r\n")
    ctype, body = multipart({"audio": ("m.ogg", b"x" * 8), "durationMs": (None, b"1000")})
    assert memo.parse_multipart(ctype, body) == {"audio": ("m.ogg", b"x" * 8), "durationMs": (None, b"1000")}
    with pytest.raises(ValueError, match="boundary"):
        memo.parse_multipart("multipart/form-data", body)
    with pytest.raises(ValueError, match="closing delimiter"):
        memo.parse_multipart(ctype, body[:-len(b"--testboundary123--\r\n")])


def test_nested_parts_stay_raw_bytes_and_parse_fast():
    import time as _time
    nested = b"--c\r\n\r\nx\r\n" * 150000 + b"--c--\r\n"  # a recursive parser would build 150k parts
    for eol, header in ((b"\r\n", b"Content-Type: multipart/mixed; boundary=c"),
                        (b"\r\n", b"Content-Type:\r\n message/rfc822"),
                        (b"\r", b"Content-Type: multipart/mixed; boundary=c"),  # bare-CR line endings
                        (b"\r\n", b"Content-Type: =?utf-8?q?multipart/mixed?=; boundary=c"),  # RFC 2047
                        (b"\r\n", b"X-Pad: --bz\r\nContent-Type: multipart/mixed; boundary=c")):  # mid-line decoy
        payload = nested.replace(b"\r\n", eol)
        body = (b"--b" + eol + b"Content-Disposition: form-data; name=\"audio\"" + eol + header + eol + eol
                + payload + eol + b"--b--" + eol)
        started = _time.monotonic()
        fields = memo.parse_multipart("multipart/form-data; boundary=b", body)
        assert _time.monotonic() - started < 2
        assert fields["audio"][1] == payload


def test_oversized_part_headers_and_transfer_encodings_are_refused_cheaply():
    import time as _time
    huge = b"Content-Disposition: form-data; name=\"text\"" + b"; a=b" * 100000  # ~0.5 MiB of parameters
    body = b"--b\r\n" + huge + b"\r\n\r\nx\r\n--b--\r\n"
    started = _time.monotonic()
    with pytest.raises(ValueError, match="too long"):
        memo.parse_multipart("multipart/form-data; boundary=b", body)
    assert _time.monotonic() - started < 1
    with pytest.raises(ValueError, match="too long"):
        memo.parse_multipart("multipart/form-data; boundary=b" + "; a=b" * 5000, b"--b--\r\n")
    for cte in (b"base64", b"quoted-printable"):
        encoded = (b"--b\r\nContent-Disposition: form-data; name=\"audio\"\r\nContent-Transfer-Encoding: " + cte
                   + b"\r\n\r\neA==\r\n--b--\r\n")
        with pytest.raises(ValueError, match="transfer encoding"):
            memo.parse_multipart("multipart/form-data; boundary=b", encoded)
    plain = b"--b\r\nContent-Disposition: form-data; name=\"n\"\r\nContent-Transfer-Encoding: 8bit\r\n\r\nv\r\n--b--\r\n"
    assert memo.parse_multipart("multipart/form-data; boundary=b", plain) == {"n": (None, b"v")}


def test_part_edges_match_the_email_parser():
    ctype = "multipart/form-data; boundary=b"
    for head_end in (b"\r\r\n", b"\n\r\n", b"\r\n\r", b"\n\r", b"\r\n\r\n", b"\n\n", b"\r\r"):  # mixed endings
        body = b"--b\r\nContent-Disposition: form-data; name=\"a\"" + head_end + b"DATA\r\n--b--\r\n"
        assert memo.parse_multipart(ctype, body) == {"a": (None, b"DATA")}, head_end
    empty_first = b"--b\r\n--b\r\nContent-Disposition: form-data; name=\"a\"\r\n\r\nv\r\n--b--\r\n"
    assert memo.parse_multipart(ctype, empty_first) == {"a": (None, b"v")}
    with pytest.raises(ValueError, match="boundary"):
        memo.parse_multipart("multipart/form-data; boundary=" + "-" * 71, b"")
    nested_comment = b"(" * 8000
    with pytest.raises(ValueError, match="malformed"):
        memo.parse_multipart(ctype, b"--b\r\nContent-Disposition: form-data; name=\"a\" " + nested_comment
                             + b"\r\n\r\nv\r\n--b--\r\n")
    with pytest.raises(ValueError):
        memo.parse_multipart(ctype + " " + "(" * 4000, b"--b--\r\n")


def test_flat_parts_keep_binary_payloads_exactly():
    data = bytes(range(256)) * 8 + b"\r\n--testboundary1234\r\nnot-a-delimiter --testboundary123 mid-line"
    ctype, body = multipart({"audio": ("m.ogg", data), "durationMs": (None, b"7")})
    assert memo.parse_multipart(ctype, body) == {"audio": ("m.ogg", data), "durationMs": (None, b"7")}
    assert memo.parse_multipart(ctype, body.replace(b"\r\n", b"\r"))["durationMs"] == (None, b"7")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_ogg_decode_pins_the_demuxer():
    wav = memo.wav_header(32000) + b"\x00" * 32000  # valid WAV, but not Ogg
    with pytest.raises(ValueError):
        memo.ogg_to_wav(wav)


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
            reply = {"model": "served-4b", "choices": [{"message": {"content": "<think>plan</think>\n Polished. "}}]}
        else:
            reply = {"text": " hello memo ", "is_final": True}
        data = json.dumps(reply).encode()
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


def test_rewrite_goes_through_gateway_chat_with_target_alias(tmp_path, fake_gateway):
    cfg = config(tmp_path, router_url=fake_gateway)
    assert memo.rewrite_transcript(cfg, "Rewrite.", "llm.voice", "um buy milk") == ("Polished.", "served-4b")
    [(path, auth, body)] = FakeGateway.seen
    assert path == "/v1/chat/completions" and auth == "Bearer ask_synthetic"
    assert body == {"model": "llm.voice", "stream": False, "messages": [
        {"role": "system", "content": "Rewrite."}, {"role": "user", "content": "um buy milk"}]}


def test_gateway_denial_is_a_transcription_error(tmp_path, fake_gateway):
    FakeGateway.status = 403
    with pytest.raises(memo.TranscriptionError, match="403"):
        memo.transcribe_segment(config(tmp_path, router_url=fake_gateway), memo.wav_header(2) + b"\x00\x00")


@pytest.mark.parametrize("raw", [b"[]", b"null", b"\"text\"", b"[" * 100000])
def test_non_object_gateway_json_is_a_transcription_error(tmp_path, monkeypatch, raw):
    monkeypatch.setattr(memo, "_post_json", lambda *args: (200, raw))
    with pytest.raises(memo.TranscriptionError):
        memo.transcribe_segment(config(tmp_path), memo.wav_header(2) + b"\x00\x00")
    with pytest.raises(memo.TranscriptionError):
        memo.rewrite_transcript(config(tmp_path), "Rewrite.", "llm.voice", "text")


def test_cli_help_lists_the_config_option(capsys):
    from anvil_serving import cli as root
    for action in ("run", "validate"):
        try:
            code = root.main(["scribe", action, "--help"])
        except SystemExit as exc:
            code = exc.code
        assert code in (0, None)
        assert "--config PATH" in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_real_opus_memo_is_segmented_into_16k_mono_wav(tmp_path, fake_gateway):
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


# --- HTTP service -----------------------------------------------------------

class Rewriter:
    def __init__(self):
        self.calls, self.error = [], None

    def __call__(self, prompt, alias, text):
        self.calls.append((prompt, alias, text))
        if self.error is not None:
            raise self.error
        return f"[{alias}] {text}", alias + "-served"


def start(cfg, **kwargs):
    server = memo.make_server(cfg, transcribe=lambda ogg: f"heard {len(ogg)} bytes", **kwargs)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


@pytest.fixture
def service(tmp_path):
    rewriter = Rewriter()
    server, base = start(config(tmp_path, targets=TARGETS), rewrite=rewriter)
    yield base, tmp_path / "memos", rewriter
    server.shutdown()
    server.server_close()


def request(base, method, path, body=None, headers=None):
    headers = dict(headers or {})
    if isinstance(body, (dict, list)):
        body, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(base + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            raw = response.read()
            return response.status, dict(response.headers), json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read()
        return error.code, dict(error.headers), json.loads(raw) if raw else None


def record(base, size=100, **extra):
    ctype, body = multipart({"audio": ("m.ogg", b"x" * size), "durationMs": (None, b"2000"), **extra})
    return request(base, "POST", "/v1/memos", body, {"Content-Type": ctype, **AUTH})


def test_upload_stores_raw_memo_and_allows_studio_origin(service):
    base, store, rewriter = service
    status, headers, stored = record(base)
    assert status == 200 and stored["transcript"] == "heard 100 bytes" and stored["outputs"] == []
    assert stored["durationMs"] == 2000 and re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{6}", stored["id"])
    assert headers["Access-Control-Allow-Origin"] == STUDIO
    assert (store / (stored["id"] + ".ogg")).exists() and rewriter.calls == []
    assert json.loads((store / (stored["id"] + ".json")).read_text(encoding="utf-8")) == stored


def test_preview_transcribes_without_storing(service):
    base, store, _ = service
    before = set(store.iterdir())
    ctype, body = multipart({"audio": ("p.ogg", b"y" * 42)})
    status, _, data = request(base, "POST", "/v1/memos/preview", body, {"Content-Type": ctype, **AUTH})
    assert status == 200 and data == {"transcript": "heard 42 bytes"}
    assert set(store.iterdir()) == before


def test_refuses_unknown_login_origin_and_oversize(service):
    base, _, _ = service
    ctype, body = multipart({"audio": ("m.ogg", b"x")})
    assert request(base, "POST", "/v1/memos", body, {"Content-Type": ctype,
                                                     "Tailscale-User-Login": "stranger@example.test"})[0] == 403
    assert request(base, "GET", "/v1/memos")[0] == 403
    assert request(base, "GET", "/v1/memos/profiles", headers={**AUTH, "Origin": "https://evil.example"})[0] == 403
    assert request(base, "POST", "/v1/memos/preview", b"", {"Content-Type": ctype, **AUTH})[0] == 413
    # An announced oversize body is refused before any byte of it is read.
    assert raw_response(base, "/v1/memos", memo.ScribeConfig.max_upload_bytes + 1).startswith(b"HTTP/1.0 413")


def raw_response(base, path, length, ctype="multipart/form-data; boundary=b"):
    """Send only a request head announcing ``length`` body bytes; return the first response bytes."""
    import socket
    conn = socket.create_connection(("127.0.0.1", int(base.rsplit(":", 1)[1])), timeout=5)
    try:
        conn.sendall(("POST %s HTTP/1.1\r\nHost: x\r\nTailscale-User-Login: %s\r\nContent-Type: %s\r\n"
                      "Content-Length: %d\r\n\r\n" % (path, LOGIN, ctype, length)).encode())
        return conn.recv(64)
    finally:
        conn.close()


@pytest.mark.parametrize("target", [["voice"], {"id": "voice"}, 7])
def test_non_string_targets_are_refused_not_crashed(service, target):
    base, _, _ = service
    ctype, body = multipart({"audio": ("m.ogg", b"x" * 8)})
    _, _, stored = request(base, "POST", "/v1/memos", body, {"Content-Type": ctype, **AUTH})
    assert request(base, "POST", "/v1/memos/%s/process" % stored["id"],
                   {"profile": "note", "target": target}, AUTH)[0] == 400
    assert request(base, "POST", "/v1/memos/profiles",
                   {"label": "X", "prompt": "Y", "target": target}, AUTH)[0] == 400


@pytest.mark.parametrize("broken", ["_write_json", "_write_private"])
def test_a_failed_write_leaves_no_orphaned_audio(tmp_path, monkeypatch, broken):
    store = memo.MemoStore(tmp_path / "memos")
    real = memo._write_private

    def partial_then_fail(path, data):  # a disk-full audio write leaves a partial file behind
        real(path, data[:2])
        raise OSError("disk full")

    monkeypatch.setattr(memo, broken, partial_then_fail if broken == "_write_private"
                        else lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        store.create(b"audio", 1000)
    assert not list((tmp_path / "memos").glob("*.ogg"))


def test_an_unreadable_record_stays_listed_and_deletable(tmp_path):
    store = memo.MemoStore(tmp_path / "memos")
    good = store.create(b"audio", 1000)
    (tmp_path / "memos" / "20000101T000000Z-abcdef.json").write_text("{", encoding="utf-8")
    (tmp_path / "memos" / "20000101T000000Z-aaaaaa.json").write_text("[]", encoding="utf-8")
    (tmp_path / "memos" / "20000101T000000Z-bbbbbb.json").mkdir()
    (tmp_path / "memos" / "99999999T999999Z-cccccc.json").write_text("{", encoding="utf-8")  # impossible date
    memos, _ = store.page(10, None)
    # A directory with a record's name is not a memo: it is neither listed nor deletable.
    assert [m["id"] for m in memos] == ["99999999T999999Z-cccccc", good["id"], "20000101T000000Z-abcdef",
                                        "20000101T000000Z-aaaaaa"]
    assert memos[0]["createdAt"] is None and all(m.get("unreadable") for m in memos[2:])
    with pytest.raises(memo._Refusal):
        store.delete("20000101T000000Z-bbbbbb")
    memos = memos[1:]
    memos = [m for m in memos if m["id"] in (good["id"], "20000101T000000Z-abcdef")]
    assert memos[1]["unreadable"] is True and memos[1]["createdAt"] == "2000-01-01T00:00:00+00:00"
    store.delete("20000101T000000Z-abcdef")
    assert "20000101T000000Z-abcdef" not in [m["id"] for m in store.page(10, None)[0]]


def test_a_config_path_that_is_a_directory_fails_closed(tmp_path):
    (tmp_path / "scribe.toml").mkdir()  # what Docker leaves for a missing short-syntax bind
    with pytest.raises(memo.ConfigError, match="cannot read"):
        memo.load_config(str(tmp_path / "scribe.toml"), env=ENV)


def test_failed_transcription_keeps_audio_and_a_visible_record(tmp_path):
    def broken(ogg):
        raise memo.TranscriptionError("gateway returned HTTP 503")
    cfg = config(tmp_path)
    server = memo.make_server(cfg, transcribe=broken)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        ctype, body = multipart({"audio": ("m.ogg", b"z" * 10)})
        status, _, data = request(base, "POST", "/v1/memos", body, {"Content-Type": ctype, **AUTH})
        assert status == 502
        assert (cfg.data_dir / (data["id"] + ".ogg")).read_bytes() == b"z" * 10
        assert request(base, "GET", "/v1/memos/" + data["id"], headers=AUTH)[2]["transcript"] is None
        assert request(base, "POST", f"/v1/memos/{data['id']}/process", {"profile": "note"}, AUTH)[0] == 409
    finally:
        server.shutdown()
        server.server_close()


def test_preflight_and_health(service):
    base, _, _ = service
    status, headers, _ = request(base, "OPTIONS", "/v1/memos/profiles/custom-abc123",
                                 headers={"Origin": "http://127.0.0.1:4000"})
    assert status == 204 and headers["Access-Control-Allow-Methods"] == "GET, POST, PUT, DELETE"
    assert headers["Access-Control-Allow-Headers"] == "Content-Type"
    assert request(base, "OPTIONS", "/v1/memos", headers={"Origin": "https://evil.example"})[0] == 403
    assert request(base, "GET", "/healthz")[0] == 200


def test_targets_list_ids_labels_and_default_without_aliases(service):
    base, _, _ = service
    status, _, data = request(base, "GET", "/v1/memos/targets", headers=AUTH)
    assert status == 200 and data == {"targets": [
        {"id": "voice", "label": "Voice LLM", "detail": "small", "default": True},
        {"id": "primary", "label": "Primary", "detail": "large", "default": False}]}


def test_builtin_profiles_are_listed_and_read_only(service):
    base, _, _ = service
    status, _, data = request(base, "GET", "/v1/memos/profiles", headers=AUTH)
    builtin = [p for p in data["profiles"] if p["builtin"]]
    assert status == 200 and len(builtin) >= 6
    assert {"email", "technical-doc", "note", "message", "minutes", "tasks", "clarify", "summary"} <= {
        p["id"] for p in builtin}
    assert all(p["prompt"] and p["label"] and p["target"] is None for p in builtin)
    assert request(base, "PUT", "/v1/memos/profiles/email", {"label": "Mine"}, AUTH)[0] == 403
    assert request(base, "DELETE", "/v1/memos/profiles/email", headers=AUTH)[0] == 403


def test_email_prompt_pins_one_sign_off_and_no_invented_names():
    prompt = next(p for pid, _, _, p in memo.BUILTIN_PROFILES if pid == "email")
    assert "Subject: <short subject>\n\n<greeting>\n\n<body in short paragraphs>\n\n<sign-off>" in prompt
    assert "otherwise it is exactly 'Hi,'" in prompt
    assert "'Thanks,' if the speaker thanked the recipient, otherwise 'Best,'" in prompt
    assert "Nothing comes after the sign-off" in prompt and "[Your Name]" in prompt
    assert "for example" not in prompt  # a small model copies example greetings into every email


def test_custom_profile_lifecycle_persists_in_the_store(service, tmp_path):
    base, store, _ = service
    status, _, created = request(base, "POST", "/v1/memos/profiles",
                                 {"label": "Standup", "prompt": "Write a standup update.", "target": "primary"}, AUTH)
    profile = created["profile"]
    assert status == 200 and re.fullmatch(r"custom-[0-9a-f]{6}", profile["id"])
    assert profile == {"id": profile["id"], "label": "Standup", "prompt": "Write a standup update.",
                       "description": "", "target": "primary", "builtin": False}
    status, _, edited = request(base, "PUT", "/v1/memos/profiles/" + profile["id"],
                                {"description": "Daily", "target": None}, AUTH)
    assert status == 200 and edited["profile"]["description"] == "Daily" and edited["profile"]["target"] is None
    # A fresh service over the same store sees it: custom profiles are server-side state.
    other, other_base = start(config(tmp_path, targets=TARGETS))
    try:
        listed = request(other_base, "GET", "/v1/memos/profiles", headers=AUTH)[2]["profiles"]
        assert [p["label"] for p in listed if not p["builtin"]] == ["Standup"]
    finally:
        other.shutdown()
        other.server_close()
    assert request(base, "DELETE", "/v1/memos/profiles/" + profile["id"], headers=AUTH)[:1] == (204,)
    assert request(base, "DELETE", "/v1/memos/profiles/" + profile["id"], headers=AUTH)[0] == 404
    assert json.loads((store / "profiles.json").read_text(encoding="utf-8")) == {"profiles": []}


@pytest.mark.parametrize("body", [
    {"prompt": "no label"},
    {"label": "no prompt"},
    {"label": "x", "prompt": "y", "target": "nowhere"},
    {"label": "x", "prompt": "y", "builtin": True},
    {"label": "x" * 81, "prompt": "y"},
])
def test_invalid_custom_profiles_are_refused(service, body):
    base, _, _ = service
    assert request(base, "POST", "/v1/memos/profiles", body, AUTH)[0] == 400


def test_process_appends_outputs_and_keeps_raw(service):
    base, store, rewriter = service
    memo_id = record(base, size=7)[2]["id"]
    status, _, first = request(base, "POST", f"/v1/memos/{memo_id}/process", {"profile": "note"}, AUTH)
    output = first["output"]
    assert status == 200 and output["profile"] == "note" and output["profileLabel"] == "Clean note"
    assert output["target"] == "voice" and output["model"] == "llm.voice-served"
    assert output["text"] == "[llm.voice] heard 7 bytes" and isinstance(output["ms"], int)
    assert re.fullmatch(r"out-[0-9a-f]{8}", output["id"]) and output["createdAt"]
    second = request(base, "POST", f"/v1/memos/{memo_id}/process", {"profile": "summary", "target": "primary"},
                     AUTH)[2]["output"]
    assert second["target"] == "primary" and second["text"] == "[llm.primary] heard 7 bytes"
    stored = request(base, "GET", "/v1/memos/" + memo_id, headers=AUTH)[2]
    assert stored["transcript"] == "heard 7 bytes" and stored["outputs"] == [output, second]
    assert [call[1] for call in rewriter.calls] == ["llm.voice", "llm.primary"]


def test_profile_target_is_used_when_the_request_names_none(service):
    base, _, rewriter = service
    profile = request(base, "POST", "/v1/memos/profiles",
                      {"label": "Big", "prompt": "Use the big model.", "target": "primary"}, AUTH)[2]["profile"]
    memo_id = record(base)[2]["id"]
    output = request(base, "POST", f"/v1/memos/{memo_id}/process", {"profile": profile["id"]}, AUTH)[2]["output"]
    assert output["target"] == "primary" and rewriter.calls[-1][:2] == ("Use the big model.", "llm.primary")


@pytest.mark.parametrize("error,status", [
    (memo.TranscriptionError("gateway returned HTTP 503"), 502),
    (TimeoutError("timed out"), 504),
])
def test_failed_rewrite_appends_nothing(service, error, status):
    base, _, rewriter = service
    memo_id = record(base)[2]["id"]
    rewriter.error = error
    assert request(base, "POST", f"/v1/memos/{memo_id}/process", {"profile": "note"}, AUTH)[0] == status
    assert request(base, "GET", "/v1/memos/" + memo_id, headers=AUTH)[2]["outputs"] == []


@pytest.mark.parametrize("body,status", [
    ({"profile": "sonnet"}, 400), ({"profile": "note", "target": "cloud"}, 400), ({"model": "x"}, 400)])
def test_process_refuses_unknown_profile_target_and_fields(service, body, status):
    base, _, rewriter = service
    memo_id = record(base)[2]["id"]
    assert request(base, "POST", f"/v1/memos/{memo_id}/process", body, AUTH)[0] == status
    assert rewriter.calls == []
    assert request(base, "POST", "/v1/memos/20200101T000000Z-abcdef/process", {"profile": "note"}, AUTH)[0] == 404


def test_history_pages_newest_first_and_delete_removes_everything(service):
    base, store, _ = service
    ids = {record(base, size=n)[2]["id"] for n in (1, 2, 3)}
    seen, cursor = [], None
    while True:
        query = "?limit=2" + (f"&before={cursor}" if cursor else "")
        page = request(base, "GET", "/v1/memos" + query, headers=AUTH)[2]
        seen += [m["id"] for m in page["memos"]]
        cursor = page["next"]
        if cursor is None:
            break
    assert set(seen) == ids and seen == sorted(seen, reverse=True) and len(seen) == 3
    victim = seen[0]
    assert request(base, "DELETE", "/v1/memos/" + victim, headers=AUTH)[0] == 204
    assert not (store / (victim + ".json")).exists() and not (store / (victim + ".ogg")).exists()
    assert request(base, "GET", "/v1/memos/" + victim, headers=AUTH)[0] == 404
    assert request(base, "GET", "/v1/memos/..%2Fprofiles", headers=AUTH)[0] == 404


def test_prototype_records_without_outputs_still_list(service):
    base, store, _ = service
    (store / "20251231T235959Z-0a0b0c.json").write_text(json.dumps(
        {"id": "20251231T235959Z-0a0b0c", "createdAt": "2025-12-31T23:59:59+00:00", "durationMs": 1,
         "transcript": "old"}), encoding="utf-8")
    listed = request(base, "GET", "/v1/memos", headers=AUTH)[2]["memos"]
    assert listed[-1] == {"id": "20251231T235959Z-0a0b0c", "createdAt": "2025-12-31T23:59:59+00:00",
                          "durationMs": 1, "transcript": "old", "outputs": []}


def test_legacy_upload_mode_runs_one_profile(service):
    base, _, _ = service
    status, _, stored = record(base, size=4, mode=(None, b"tasks"))
    assert status == 200 and stored["transcript"] == "heard 4 bytes"
    assert [o["profile"] for o in stored["outputs"]] == ["tasks"]
    assert record(base, mode=(None, b"sonnet"))[0] == 400


def test_store_is_marked_and_refused_by_corpus_validation(service):
    _, store, _ = service
    assert (store / corpus.EVALUATION_EXCLUDED_MARKER).is_file()
    with pytest.raises(corpus.CorpusError, match="evaluation-excluded"):
        corpus._safe_audio_path(store / "cases.jsonl", "memo.wav")


def test_validate_reports_presence_without_secrets(tmp_path, capsys):
    path = write_manifest(tmp_path, BASE, '[scribe.targets.voice]\nmodel = "llm.voice"\n')
    import os as _os
    old = {k: _os.environ.get(k) for k in ENV}
    _os.environ.update(ENV)
    try:
        assert scribe_cli.main(["validate", "--config", path]) == 0
    finally:
        for k, v in old.items():
            _os.environ.pop(k, None) if v is None else _os.environ.__setitem__(k, v)
    out = capsys.readouterr().out
    report = json.loads(out.split("scribe validate: ", 1)[1])
    assert report["ok"] is True and report["logins"] == 2 and report["targets"] == ["voice"]
    assert report["builtin_profiles"] == 8
    for secret in ("ask_synthetic", LOGIN, "llm.voice"):
        assert secret not in out
    assert scribe_cli.main(["validate", "--config", str(tmp_path / "missing.toml")]) == 2


def test_malformed_content_length_is_refused_and_logged_safely(service, capfd):
    import http.client
    base, _, _ = service
    host, port = base.split("//", 1)[1].split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    conn.putrequest("POST", "/v1/memos")
    for name, value in {**AUTH, "Content-Type": "multipart/form-data; boundary=x", "Content-Length": "abc"}.items():
        conn.putheader(name, value)
    conn.endheaders()
    assert conn.getresponse().status == 413
    conn.close()
    assert request(base, "GET", "/healthz")[0] == 200  # the server keeps serving
    err = capfd.readouterr().err
    assert '"bytes_in": -1' in err and "Traceback" not in err


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_recordings_longer_than_the_bound_are_refused():
    ogg = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
         "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-f", "ogg", "pipe:1"],
        capture_output=True, check=True, timeout=60).stdout
    with pytest.raises(ValueError, match="longer than 2 seconds"):
        memo.ogg_to_wav(ogg, max_seconds=2)
    assert len(memo.ogg_to_wav(ogg, max_seconds=10)) > 44 + 4 * memo.BYTES_PER_SECOND


def test_recording_bound_is_configurable_and_bounded(tmp_path):
    assert memo.load_config(write_manifest(tmp_path, {**BASE, "max_recording_seconds": 300}), env=ENV
                            ).max_recording_seconds == 300
    with pytest.raises(memo.ConfigError, match="max_recording_seconds"):
        memo.load_config(write_manifest(tmp_path, {**BASE, "max_recording_seconds": 7200}), env=ENV)


def test_decodes_are_bounded_and_busy_uploads_keep_audio(tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow(ogg):
        started.set()
        release.wait(5)
        return "slow"
    monkeypatch.setattr(memo, "_UPLOAD_SLOT_WAIT_SECONDS", 0.2)
    cfg = config(tmp_path, max_concurrent_transcriptions=1)
    server = memo.make_server(cfg, transcribe=slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    ctype, body = multipart({"audio": ("p.ogg", b"y" * 8)})
    headers = {"Content-Type": ctype, **AUTH}
    first = threading.Thread(target=request, args=(base, "POST", "/v1/memos/preview", body, headers))
    try:
        first.start()
        assert started.wait(5)
        assert request(base, "POST", "/v1/memos/preview", body, headers)[0] == 503  # preview never waits
        status, _, data = request(base, "POST", "/v1/memos", body, headers)
        assert status == 503 and (cfg.data_dir / (data["id"] + ".ogg")).exists()  # audio kept
        assert request(base, "GET", "/v1/memos/" + data["id"], headers=AUTH)[2]["transcript"] is None
    finally:
        release.set()
        first.join(5)
        server.shutdown()
        server.server_close()
    assert memo.load_config(write_manifest(tmp_path, {**BASE, "max_concurrent_transcriptions": 4}), env=ENV
                            ).max_concurrent_transcriptions == 4


# --- review fixes: admission, store privacy, scaffold credentials ------------

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX ownership and modes")


@posix_only
def test_store_is_private_and_files_are_owner_only(tmp_path):
    store_dir = tmp_path / "memos"
    store_dir.mkdir(mode=0o755)
    os.chmod(store_dir, 0o755)
    server, base = start(config(tmp_path))
    try:
        memo_id = record(base)[2]["id"]
    finally:
        server.shutdown()
        server.server_close()
    assert (os.stat(store_dir).st_mode & 0o777) == 0o700
    for name in (memo_id + ".ogg", memo_id + ".json", corpus.EVALUATION_EXCLUDED_MARKER):
        assert (os.stat(store_dir / name).st_mode & 0o777) == 0o600, name


@posix_only
def test_store_owned_by_another_user_is_refused(tmp_path, monkeypatch):
    (tmp_path / "memos").mkdir()
    real = os.geteuid()
    monkeypatch.setattr(memo.os, "geteuid", lambda: real + 1)
    with pytest.raises(OSError, match="owned by the service user"):
        memo.make_server(config(tmp_path))


@posix_only
def test_reference_scaffold_loads_with_a_key_beside_it_and_logins_from_operator_env(tmp_path, monkeypatch):
    example = Path(__file__).resolve().parents[2] / "examples" / "primary-node" / "scribe.toml"
    home = tmp_path / "home"
    home.mkdir()
    (home / "scribe.toml").write_bytes(example.read_bytes())
    key = home / "scribe.key"
    key.write_text("ask_scaffold\n", encoding="utf-8")
    key.chmod(0o600)
    (home / ".env").write_text("ANVIL_SCRIBE_LOGINS=owner@example.test\n", encoding="utf-8")
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(home))
    monkeypatch.setenv("ANVIL_SERVING_NO_HOME_ENV", "1")
    monkeypatch.delenv("ANVIL_SCRIBE_LOGINS", raising=False)
    loaded = memo.load_config(str(home / "scribe.toml"))
    assert loaded.router_key == "ask_scaffold" and loaded.allowed_logins == {"owner@example.test"}
    key.unlink()
    with pytest.raises(memo.ConfigError, match="router_key_file"):
        memo.load_config(str(home / "scribe.toml"))


def test_connections_beyond_the_ceiling_are_dropped(tmp_path, monkeypatch):
    import socket
    monkeypatch.setattr(memo, "_MAX_CONNECTIONS", 1)
    monkeypatch.setattr(memo, "_SOCKET_TIMEOUT_SECONDS", 5)
    server, base = start(config(tmp_path))
    port = int(base.rsplit(":", 1)[1])
    idle = socket.create_connection(("127.0.0.1", port))
    try:
        import time as _time
        _time.sleep(0.2)  # the idle connection holds the only slot
        extra = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:  # dropped without a response: EOF, or a reset/abort depending on the OS
            extra.sendall(b"GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n")
            assert extra.recv(64) == b""
        except ConnectionError:
            pass
        extra.close()
    finally:
        idle.close()
    _time.sleep(0.2)
    try:
        assert request(base, "GET", "/healthz")[0] == 200  # the slot is free again
    finally:
        server.shutdown()
        server.server_close()


def test_buffered_uploads_are_admitted_before_any_body_is_read(tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow(ogg):
        started.set()
        release.wait(5)
        return "slow"
    monkeypatch.setattr(memo, "_MAX_BUFFERED_UPLOADS", 1)
    server = memo.make_server(config(tmp_path, max_concurrent_transcriptions=4), transcribe=slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    ctype, body = multipart({"audio": ("p.ogg", b"y" * 8)})
    first = threading.Thread(target=request, args=(base, "POST", "/v1/memos/preview", body,
                                                    {"Content-Type": ctype, **AUTH}))
    try:
        first.start()
        assert started.wait(5)
        assert request(base, "POST", "/v1/memos/preview", body, {"Content-Type": ctype, **AUTH})[0] == 503
        # Refused before reading: a head announcing a body that never comes still gets 503 at once.
        assert raw_response(base, "/v1/memos/preview", 1000).startswith(b"HTTP/1.0 503")
    finally:
        release.set()
        first.join(5)
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("path,ctype", [("/v1/memos/preview", "multipart/form-data; boundary=b"),
                                        ("/v1/memos/profiles", "application/json")])
def test_a_slowly_dripped_body_is_refused(tmp_path, monkeypatch, path, ctype):
    import socket
    import time as _time
    monkeypatch.setattr(memo, "_BODY_DEADLINE_SECONDS", 0.3)
    server, base = start(config(tmp_path))
    port = int(base.rsplit(":", 1)[1])
    conn = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        head = ("POST %s HTTP/1.1\r\nHost: x\r\nTailscale-User-Login: %s\r\n"
                "Content-Type: %s\r\nContent-Length: 100\r\n\r\n" % (path, LOGIN, ctype))
        conn.sendall(head.encode() + b"x" * 10)
        _time.sleep(0.5)
        conn.sendall(b"x" * 10)
        assert conn.recv(64).startswith(b"HTTP/1.0 408")
    finally:
        conn.close()
        server.shutdown()
        server.server_close()

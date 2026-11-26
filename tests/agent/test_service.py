"""Anvil Agent: glasses turns, the glasses event queue and the MCP tool (ADR-0049)."""
import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from anvil_serving.agent import cli as agent_cli
from anvil_serving.agent import service as agent

LOGIN = "owner@example.test"
STUDIO = "http://127.0.0.1:51358"
OWNER = {"Tailscale-User-Login": LOGIN, "Origin": STUDIO}
HERMES = agent.Backend("hermes-api", "Hermes", "http://127.0.0.1:9", "hermes-synthetic", "anvil-agent")


def multipart(fields):
    boundary = "testboundary123"
    parts = []
    for name, (filename, data) in fields.items():
        disposition = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else "")
        parts.append(f"--{boundary}\r\nContent-Disposition: {disposition}\r\n\r\n".encode() + data + b"\r\n")
    return f"multipart/form-data; boundary={boundary}", b"".join(parts) + f"--{boundary}--\r\n".encode()


def make_config(**overrides):
    values = dict(router_url="http://127.0.0.1:9", router_key="ask_synthetic", allowed_logins=frozenset({LOGIN}),
                  backends={"hermes": HERMES}, listen_port=0)
    values.update(overrides)
    return agent.AgentConfig(**values)


def serve(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class Upstream(BaseHTTPRequestHandler):
    """A JSON upstream whose replies a test scripts through ``self.server.reply``."""

    def log_message(self, *args):
        pass

    def _json(self, payload, status=200, headers=()):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.server.seen.append(("GET", self.path, dict(self.headers), None))
        self._json(*self.server.reply("GET", self.path))

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.seen.append(("POST", self.path, dict(self.headers), body))
        self._json(*self.server.reply("POST", self.path))


def upstream(reply):
    server = serve(Upstream)
    server.seen, server.reply = [], reply
    return server


# ---- config ----

def write_config(tmp_path, body):
    path = tmp_path / "agent.toml"
    path.write_text(body, encoding="utf-8")
    return str(path)


BASE = """
[agent]
router_url = "http://127.0.0.1:8000/v1"
router_key_env = "AGENT_KEY"
allowed_logins_env = "AGENT_LOGINS"

[agent.backends.hermes]
kind = "hermes-api"
url = "http://127.0.0.1:8642/"
key_env = "HERMES_KEY"
session = "glasses-session"

[agent.backends.pi]
kind = "pi-web"
label = "Pi"
url = "http://127.0.0.1:30141"
key_env = "PI_TOKEN"
session = "glasses session/1"
"""
ENV = {"AGENT_KEY": "ask_synthetic", "AGENT_LOGINS": f" {LOGIN} , other@example.test",
       "HERMES_KEY": "hermes-synthetic", "PI_TOKEN": "pi-synthetic"}


def test_load_config_resolves_references_in_declaration_order(tmp_path):
    config = agent.load_config(write_config(tmp_path, BASE), env=ENV)
    assert config.router_url == "http://127.0.0.1:8000"
    assert config.allowed_logins == {LOGIN, "other@example.test"}
    assert list(config.backends) == ["hermes", "pi"]
    assert config.backends["hermes"] == agent.Backend("hermes-api", "hermes", "http://127.0.0.1:8642",
                                                      "hermes-synthetic", "glasses-session")
    assert config.listen_port == agent.DEFAULT_PORT and config.turn_timeout_seconds == 150
    for secret in ("ask_synthetic", "hermes-synthetic", "pi-synthetic", LOGIN):
        assert secret not in repr(config)
    assert agent.config_summary(config)["backends"] == [
        {"id": "hermes", "kind": "hermes-api", "label": "hermes"}, {"id": "pi", "kind": "pi-web", "label": "Pi"}]


@pytest.mark.parametrize("edit, env_drop, message", [
    (("", ""), "HERMES_KEY", "agent.backends.hermes.key_env names HERMES_KEY"),
    (("", ""), "AGENT_LOGINS", "resolves to no logins"),
    (("", ""), "AGENT_KEY", "agent.router_key_env names AGENT_KEY"),
    (('router_url = "http://127.0.0.1:8000/v1"', 'router_url = "http://127.0.0.1:8000/v1"\nlisten_host = "0.0.0.0"'),
     None, "loopback"),
    (('kind = "pi-web"', 'kind = "shell"'), None, "kind must be one of"),
    (('session = "glasses session/1"', ''), None, "existing pi-web session"),
    (('key_env = "HERMES_KEY"', 'key_env = "HERMES_KEY"\nkey = "literal"'), None, "unknown agent.backends.hermes keys"),
    (('key_env = "PI_TOKEN"', 'key_env = "PI_TOKEN"\nkey_file = "/nope"'), None, "exactly one of key_env or key_file"),
    (('url = "http://127.0.0.1:8642/"', 'url = "ftp://host"'), None, "http(s) base URL"),
    (('[agent.backends.hermes]', '[agent.backends.Hermes]'), None, "lowercase id"),
    (('router_url', 'pool_size = 3\nrouter_url'), None, "unknown agent keys"),
])
def test_load_config_fails_closed(tmp_path, edit, env_drop, message):
    env = {k: v for k, v in ENV.items() if k != env_drop}
    with pytest.raises(agent.ConfigError, match=re.escape(message)):
        agent.load_config(write_config(tmp_path, BASE.replace(*edit, 1)), env=env)


def test_load_config_requires_a_backend_and_a_file(tmp_path):
    with pytest.raises(agent.ConfigError, match="from 1 through 8 backends"):
        agent.load_config(write_config(tmp_path, BASE.split("[agent.backends.hermes]")[0]), env=ENV)
    with pytest.raises(agent.ConfigError, match="not found"):
        agent.load_config(str(tmp_path / "missing.toml"), env=ENV)


def test_scaffold_template_validates(monkeypatch, capsys):
    template = os.path.join(os.path.dirname(agent.__file__), "..", "_scaffold_templates", "agent.toml")
    for name in ("ANVIL_AGENT_ROUTER_KEY", "ANVIL_AGENT_HERMES_KEY"):
        monkeypatch.setenv(name, "synthetic")
    monkeypatch.setenv("ANVIL_AGENT_LOGINS", LOGIN)
    assert agent_cli.main(["validate", "--config", template]) == 0
    out = capsys.readouterr().out
    assert '"backends": [{"id": "hermes"' in out and "synthetic" not in out and LOGIN not in out
    monkeypatch.delenv("ANVIL_AGENT_HERMES_KEY")
    assert agent_cli.main(["validate", "--config", template]) == 2
    assert "ANVIL_AGENT_HERMES_KEY" in capsys.readouterr().err


# ---- backends ----

def test_ask_hermes_sends_key_session_and_hud_hint():
    server = upstream(lambda method, path: ({"choices": [{"message": {"role": "assistant", "content": " Hi there. "},
                                                          "finish_reason": "stop"}]}, 200,
                                            [("X-Hermes-Session-Id", "glasses-session-tip")]))
    backend = agent.Backend("hermes-api", "Hermes", f"http://127.0.0.1:{server.server_port}", "test-key",
                            "glasses-session")
    try:
        assert agent.ask_hermes(backend, "what's next", 5) == ("Hi there.", "glasses-session-tip", "stop")
        method, path, headers, body = server.seen[0]
        assert (method, path) == ("POST", "/v1/chat/completions")
        assert headers["Authorization"] == "Bearer test-key"
        assert headers["X-Hermes-Session-Id"] == "glasses-session"
        assert body["messages"] == [{"role": "system", "content": agent.HUD_HINT},
                                    {"role": "user", "content": "what's next"}]
        assert "model" not in body and body["stream"] is False  # the profile's own model, no override
    finally:
        server.shutdown()


def test_backend_http_error_keeps_only_the_status():
    server = upstream(lambda method, path: ({"error": "secret detail"}, 401))
    backend = agent.Backend("hermes-api", "Hermes", f"http://127.0.0.1:{server.server_port}", "k", "s")
    try:
        with pytest.raises(agent.BackendHTTPError) as raised:
            agent.ask_hermes(backend, "hi", 5)
        assert raised.value.code == 401 and "secret" not in str(raised.value)
    finally:
        server.shutdown()


# The shipped pi-web mobile bridge allows only these query parameters per read and answers
# HTTP 400 to anything else (lib/mobile-pi-api.ts, pi-web 0.10.0). ``None`` means a safe id.
PI_BRIDGE_QUERY = {"state": {}, "context": {"before": None, "tail": "50"}, "command": {}}


def pi_bridge_rejects(path):
    """True when the real bridge would refuse this path's query string."""
    route, _, query = path.partition("?")
    allowed = PI_BRIDGE_QUERY[route.rsplit("/", 1)[-1]]
    pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
    names = [name for name, _ in pairs]
    return any(name not in allowed or names.count(name) != 1
               or (allowed[name] is not None and value != allowed[name]) for name, value in pairs)


def test_ask_pi_prompts_bridge_waits_and_reads_newest_assistant_message():
    states = {"n": 0}

    def reply(method, path):
        if pi_bridge_rejects(path):
            return ({"error": "bad request"}, 400)
        if method == "POST":
            return ({"success": True},)
        if path.endswith("/state"):
            states["n"] += 1  # 1: before the prompt, 2: streaming, 3+: done
            return ({"running": True, "state": {"isStreaming": states["n"] == 2,
                                                "messageCount": 2 if states["n"] == 1 else 4}},)
        return ({"context": {"messages": [
            {"role": "user", "content": [{"type": "text", "text": "old"}]},
            {"role": "assistant", "stopReason": "stop", "content": [
                {"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": "Pi says hi."}]}]}},)

    server = upstream(reply)
    session = "glasses session/1"
    backend = agent.Backend("pi-web", "Pi", f"http://127.0.0.1:{server.server_port}", "pi-test", session)
    try:
        assert agent.ask_pi(backend, "what now", 5, poll_seconds=0.01) == ("Pi says hi.", session, "stop")
        command = next(entry for entry in server.seen if entry[0] == "POST")
        assert command[1] == "/api/mobile-pi/sessions/glasses%20session%2F1/command"
        assert command[3]["type"] == "prompt" and command[3]["message"].endswith("what now")
        assert agent.PI_HINT in command[3]["message"]
        assert len(command[3]["requestId"]) == 36  # a UUID, as the bridge requires
        assert {entry[2]["Authorization"] for entry in server.seen} == {"Bearer pi-test"}
        assert states["n"] >= 3  # waited out the streaming state
        assert any(entry[1].endswith("/context") for entry in server.seen)
    finally:
        server.shutdown()


def test_root_cli_help_shows_the_agent_config_option(capsys):
    from anvil_serving import cli

    for action in ("run", "validate"):
        assert cli.main(["agent", action, "--help"]) == 0
        out = capsys.readouterr().out
        assert "--config" in out, action  # the leaf parser's help, not the command tree's
        assert "anvil-serving agent " + action in out


def test_fake_pi_bridge_enforces_the_shipped_query_rules():
    assert not pi_bridge_rejects("/api/mobile-pi/sessions/s/context")
    assert not pi_bridge_rejects("/api/mobile-pi/sessions/s/context?tail=50")
    assert pi_bridge_rejects("/api/mobile-pi/sessions/s/context?tail=4")
    assert pi_bridge_rejects("/api/mobile-pi/sessions/s/context?tail=50&tail=50")
    assert pi_bridge_rejects("/api/mobile-pi/sessions/s/state?x=1")


# ---- glasses ops ----

def test_parse_glasses_op():
    parse = agent.parse_glasses_op
    assert parse({"op": "tp", "content": " line one ", "v": 1}) == {"op": "TP", "title": "", "v": 1,
                                                                   "content": "line one"}
    assert parse({"op": "MR"})["mode"] == "minutes"
    assert parse({"op": "RC", "v": 0}) == {"op": "RC", "title": "", "v": 0, "mode": ""}
    assert parse({"op": "ASK", "content": "Ship it?"})["content"] == "Ship it?"
    for bad in ({"op": "VU"}, {"op": "NOTIFY"}, {"op": "TP", "v": 1}, {"op": "LS", "v": 2},
                {"op": "RC", "mode": "Rm -rf"}, {"op": "ASK", "content": "x" * 301}, "NOTIFY",
                {"op": "NOTIFY", "content": "x", "style": "loud"}):
        with pytest.raises(ValueError):
            parse(bad)


def test_parse_notify_maps_v01_body_to_notify_op():
    assert agent.parse_notify(b'{"body": " Stand-up in 5 ", "title": "Cal"}') == {
        "op": "NOTIFY", "title": "Cal", "style": "notify", "content": "Stand-up in 5"}
    assert agent.parse_notify(b'{"body": "buy milk", "kind": "task"}')["style"] == "task"
    for bad in (b"[]", b"not json", b'{"body": ""}', b'{"body": "x", "kind": "shell"}',
                b'{"body": "x", "title": 5}', json.dumps({"body": "x" * 2001}).encode()):
        with pytest.raises(ValueError):
            agent.parse_notify(bad)


def test_control_ops_expire_but_cards_persist():
    glasses = agent.Glasses()
    glasses.add_event({"op": "NOTIFY", "content": "card"})
    glasses.add_event({"op": "LS", "v": 1, "mode": ""}, ttl_s=0.05)
    assert [e["op"] for e in glasses.events_after(0, 0)] == ["NOTIFY", "LS"]
    time.sleep(0.1)
    fresh = glasses.events_after(0, 0)
    assert [e["op"] for e in fresh] == ["NOTIFY"]
    assert not any(k.startswith("_") for k in fresh[0])  # internal fields stay internal


def test_tool_reports_presence_honestly():
    glasses = agent.Glasses()
    assert json.loads(glasses.run_tool({"op": "STATUS"})[0]) == {"glassesAppOpen": False}
    queued = json.loads(glasses.run_tool({"op": "RC", "v": 1})[0])
    assert queued["status"] == "queued" and "expires" in queued["detail"]
    assert json.loads(glasses.run_tool({"op": "ASK", "content": "Now?"})[0])["status"] == "not_open"  # never blocks
    glasses.polls["last"] = time.monotonic()  # the client just polled
    sent = json.loads(glasses.run_tool({"op": "LS", "v": 1})[0])
    assert sent["status"] == "sent" and "press" in sent["detail"]
    text, is_error = glasses.run_tool({"op": "BU"})
    assert is_error and "op must be one of" in text
    assert glasses.run_tool({"op": "ASK", "content": "x", "timeoutMs": True})[1]


def test_ask_round_trip():
    glasses = agent.Glasses()
    glasses.polls["last"] = time.monotonic()

    def answer_when_queued():
        for _ in range(100):
            asks = [e for e in glasses.events_after(0, 0) if e["op"] == "ASK"]
            if asks and glasses.settle(asks[0]["askId"], {"status": "answered", "answer": "yes"}):
                return
            time.sleep(0.02)
    threading.Thread(target=answer_when_queued, daemon=True).start()
    result = json.loads(glasses.run_tool({"op": "ASK", "content": "Ship it?", "timeoutMs": 5000})[0])
    assert result == {"status": "answered", "answer": "yes"}
    assert glasses.asks == {}
    assert [e for e in glasses.events_after(0, 0) if e["op"] == "ASK"] == []  # gone once settled
    assert not glasses.settle("gone", {"status": "answered"})


def test_mcp_dispatch():
    glasses = agent.Glasses()
    rpc = lambda message: agent.mcp_response(glasses, message)  # noqa: E731
    init = rpc({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
    assert init["id"] == 0 and init["result"]["protocolVersion"] == "2025-03-26"
    assert "tools" in init["result"]["capabilities"]
    assert rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    tools = rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    assert [t["name"] for t in tools] == ["memomind_glasses"]
    call = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "memomind_glasses", "arguments": {"op": "NOTIFY", "content": "hi"}}})["result"]
    assert not call["isError"]
    assert glasses.events_after(0, 0)[-1]["content"] == "hi"
    assert rpc({"jsonrpc": "2.0", "id": 3, "method": "nope"})["error"]["code"] == -32601
    assert rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "other"}})["error"]["code"] == -32602
    assert rpc({"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": ["x"]})["error"]["code"] == -32602
    assert rpc({"id": 6})["error"]["code"] == -32600


def test_ids_are_monotonic_and_ring_is_bounded():
    glasses = agent.Glasses()
    ids = [glasses.add_event({"op": "NOTIFY", "content": f"n{i}"})["id"] for i in range(agent.MAX_EVENTS + 5)]
    assert ids == sorted(set(ids))
    assert len(glasses.events) == agent.MAX_EVENTS
    assert [e["content"] for e in glasses.events_after(ids[-3], 0)] == ["n53", "n54"]


def test_long_poll_wakes_on_new_event():
    glasses = agent.Glasses()
    cursor = glasses.add_event({"op": "NOTIFY", "content": "old"})["id"]
    threading.Timer(0.2, glasses.add_event, ({"op": "NOTIFY", "style": "task", "content": "new"},)).start()
    started = time.monotonic()
    fresh = glasses.events_after(cursor, 5)
    assert time.monotonic() - started < 3
    assert [(e["style"], e["content"]) for e in fresh] == [("task", "new")]
    assert glasses.events_after(fresh[-1]["id"], 0.1) == []  # times out empty


# ---- HTTP flow ----

class Client:
    def __init__(self, server):
        self.server, self.base = server, f"http://127.0.0.1:{server.server_port}"

    def request(self, method, path, body=None, headers=None):
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()


@pytest.fixture
def live():
    calls = {"ask": lambda agent_id, text: (f"echo: {text}", f"{agent_id}-session", "stop"),
             "transcribe": lambda ogg: f"heard {len(ogg)} bytes"}
    server = agent.make_server(make_config(), transcribe=lambda ogg: calls["transcribe"](ogg),
                               ask=lambda agent_id, text: calls["ask"](agent_id, text))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = Client(server)
    client.calls = calls
    yield client
    server.shutdown()
    server.server_close()


def test_health_and_unknown_paths(live):
    assert live.request("GET", "/healthz")[0] == 200
    assert live.request("GET", "/v1/memos")[0] == 404  # memo paths belong to Anvil Scribe
    assert live.request("POST", "/v1/memos", b"x", OWNER)[0] == 404


def test_preflight(live):
    for path in ("/v1/agent/turn", "/v1/hermes/turn", "/v1/glasses/notify", "/v1/glasses/answer",
                 "/v1/glasses/events", "/v1/agents"):
        status, headers, _ = live.request("OPTIONS", path, headers={"Origin": "http://127.0.0.1:4000"})
        assert status == 204, path
        assert headers["Access-Control-Allow-Methods"] == "GET, POST"
        assert headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:4000"
    assert live.request("OPTIONS", "/v1/agent/turn", headers={"Origin": "https://evil.example"})[0] == 403
    assert live.request("OPTIONS", "/mcp", headers={"Origin": "http://127.0.0.1:4000"})[0] == 403


def test_agents_lists_configured_backends_only(live):
    status, headers, data = live.request("GET", "/v1/agents", headers=OWNER)
    assert (status, json.loads(data)) == (200, {"agents": [{"id": "hermes", "label": "Hermes"}]})
    assert headers["Access-Control-Allow-Origin"] == STUDIO and headers["Cache-Control"] == "no-store"
    assert live.request("GET", "/v1/agents", headers={"Tailscale-User-Login": "stranger@example.test"})[0] == 403


def test_agent_turn_with_audio_text_and_hermes_alias(live):
    ctype, body = multipart({"agent": (None, b"hermes"), "audio": ("t.ogg", b"z" * 7)})
    status, headers, data = live.request("POST", "/v1/agent/turn", body, {"Content-Type": ctype, **OWNER})
    assert status == 200
    assert json.loads(data) == {"agent": "hermes", "transcript": "heard 7 bytes", "reply": "echo: heard 7 bytes",
                                "sessionId": "hermes-session", "status": "stop"}
    assert headers["Access-Control-Allow-Origin"] == STUDIO
    ctype, body = multipart({"text": (None, " typed question ".encode())})  # no agent: first configured
    status, _, data = live.request("POST", "/v1/agent/turn", body, {"Content-Type": ctype, **OWNER})
    assert (status, json.loads(data)["reply"]) == (200, "echo: typed question")
    status, _, data = live.request("POST", "/v1/hermes/turn", body, {"Content-Type": ctype, **OWNER})
    assert (status, json.loads(data)["agent"]) == (200, "hermes")
    ctype, body = multipart({"agent": (None, b"pi"), "text": (None, b"hi")})
    status, _, data = live.request("POST", "/v1/agent/turn", body, {"Content-Type": ctype, **OWNER})
    assert status == 400 and json.loads(data)["error"] == "agent 'pi' is not configured (available: hermes)"
    ctype, body = multipart({"durationMs": (None, b"1")})
    assert live.request("POST", "/v1/agent/turn", body, {"Content-Type": ctype, **OWNER})[0] == 400
    assert live.request("POST", "/v1/agent/turn", body, {
        "Content-Type": ctype, "Tailscale-User-Login": "stranger@example.test"})[0] == 403
    assert live.request("POST", "/v1/agent/turn", body, {
        "Content-Type": ctype, "Tailscale-User-Login": LOGIN, "Origin": "https://evil.example"})[0] == 403
    ctype, body = multipart({"text": (None, b"x" * (agent.MAX_TEXT_CHARS + 1))})
    assert live.request("POST", "/v1/agent/turn", body, {"Content-Type": ctype, **OWNER})[0] == 400
    assert live.request("POST", "/v1/agent/turn", b"", {"Content-Type": ctype, **OWNER})[0] == 413
    assert agent._length({"Content-Length": "²"}) == 0 and agent._length({"Content-Length": "12"}) == 12


@pytest.mark.parametrize("error, expected", [
    (ConnectionRefusedError("refused"), (502, "Hermes is unavailable")),
    (TimeoutError("slow"), (504, "Hermes did not answer in time")),
    (agent.BackendHTTPError(500), (502, "Hermes answered HTTP 500")),
    (KeyError("choices"), (502, "Hermes is unavailable")),
])
def test_agent_turn_reports_upstream_failures(live, error, expected):
    def down(agent_id, text):
        raise error
    live.calls["ask"] = down
    ctype, body = multipart({"text": (None, b"hi")})
    status, _, data = live.request("POST", "/v1/agent/turn", body, {"Content-Type": ctype, **OWNER})
    reply = json.loads(data)
    assert (status, reply["error"], reply["transcript"]) == (*expected, "hi")
    assert reply.get("unconfirmed", False) is (status == 504)


def test_speech_failures(live):
    ctype, body = multipart({"audio": ("t.ogg", b"silence")})
    live.calls["transcribe"] = lambda ogg: ""
    status, _, data = live.request("POST", "/v1/hermes/turn", body, {"Content-Type": ctype, **OWNER})
    assert (status, json.loads(data)) == (422, {"error": "no speech recognized", "transcript": ""})

    def broken(ogg):
        raise agent.TranscriptionError("gateway returned HTTP 403")
    live.calls["transcribe"] = broken
    status, _, data = live.request("POST", "/v1/hermes/turn", body, {"Content-Type": ctype, **OWNER})
    assert (status, json.loads(data)) == (502, {"error": "transcription failed"})


def test_notify_then_events(live):
    json_headers = {"Content-Type": "application/json", "Tailscale-User-Login": LOGIN}
    status, _, data = live.request("POST", "/v1/glasses/notify", b'{"body": "Build is green", "title": "CI"}',
                                   json_headers)
    assert status == 200
    event_id = json.loads(data)["id"]
    assert live.request("POST", "/v1/glasses/notify", b'{"body": ""}', json_headers)[0] == 400
    assert live.request("POST", "/v1/glasses/notify", b'{"body": "x"}', {
        "Content-Type": "application/json", "Tailscale-User-Login": "stranger@example.test"})[0] == 403
    status, headers, data = live.request("GET", "/v1/glasses/events?after=0&wait=0", headers=OWNER)
    assert status == 200
    assert [(e["id"], e["op"], e["title"], e["content"]) for e in json.loads(data)["events"]] == [
        (event_id, "NOTIFY", "CI", "Build is green")]
    assert headers["Access-Control-Allow-Origin"] == STUDIO
    assert json.loads(live.request("GET", f"/v1/glasses/events?after={event_id}&wait=0",
                                   headers=OWNER)[2])["events"] == []
    for query in ("after=x", "wait=nan"):
        assert live.request("GET", "/v1/glasses/events?" + query, headers=OWNER)[0] == 400
    assert live.request("GET", "/v1/glasses/events", headers={"Tailscale-User-Login": "stranger@example.test"})[0] == 403
    assert live.request("GET", "/v1/glasses/events", headers={
        "Tailscale-User-Login": LOGIN, "Origin": "https://evil.example"})[0] == 403


def test_answer_settles_a_waiting_ask_over_http(live):
    glasses = live.server.glasses
    glasses.polls["last"] = time.monotonic()
    result = {}
    waiter = threading.Thread(target=lambda: result.update(json.loads(glasses.run_tool(
        {"op": "ASK", "content": "Lunch at noon?", "timeoutMs": 5000})[0])))
    waiter.start()
    asks = []
    for _ in range(100):
        asks = [e for e in glasses.events_after(0, 0) if e["op"] == "ASK"]
        if asks:
            break
        time.sleep(0.02)
    assert asks and asks[0]["timeoutMs"] == 5000
    ctype, body = multipart({"askId": (None, asks[0]["askId"].encode()), "audio": ("a.ogg", b"q" * 3)})
    status, _, data = live.request("POST", "/v1/glasses/answer", body, {"Content-Type": ctype, **OWNER})
    assert (status, json.loads(data)) == (200, {"status": "answered", "transcript": "heard 3 bytes"})
    waiter.join(5)
    assert result == {"status": "answered", "answer": "heard 3 bytes"}
    ctype, body = multipart({"askId": (None, asks[0]["askId"].encode()), "declined": (None, b"1")})
    assert live.request("POST", "/v1/glasses/answer", body, {"Content-Type": ctype, **OWNER})[0] == 404  # settled
    ctype, body = multipart({"declined": (None, b"1")})
    assert live.request("POST", "/v1/glasses/answer", body, {"Content-Type": ctype, **OWNER})[0] == 400


def test_decline_over_http(live):
    glasses = live.server.glasses
    glasses.polls["last"] = time.monotonic()
    result = {}
    waiter = threading.Thread(target=lambda: result.update(json.loads(glasses.run_tool(
        {"op": "ASK", "content": "Call now?", "timeoutMs": 5000})[0])))
    waiter.start()
    for _ in range(100):
        asks = [e for e in glasses.events_after(0, 0) if e["op"] == "ASK"]
        if asks:
            break
        time.sleep(0.02)
    ctype, body = multipart({"askId": (None, asks[0]["askId"].encode()), "declined": (None, b"1")})
    status, _, data = live.request("POST", "/v1/glasses/answer", body, {"Content-Type": ctype, **OWNER})
    assert (status, json.loads(data)) == (200, {"status": "declined", "transcript": ""})
    waiter.join(5)
    assert result == {"status": "declined"}


def test_mcp_over_http(live):
    headers = {"Content-Type": "application/json", "Tailscale-User-Login": LOGIN}
    init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2025-06-18", "capabilities": {}}}).encode()
    status, _, data = live.request("POST", "/mcp", init, headers)
    assert (status, json.loads(data)["result"]["serverInfo"]["name"]) == (200, "memomind-glasses")
    note = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode()
    assert live.request("POST", "/mcp", note, headers)[0] == 202
    call = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "memomind_glasses", "arguments": {"op": "STATUS"}}}).encode()
    status, _, data = live.request("POST", "/mcp", call, headers)
    assert status == 200 and "glassesAppOpen" in json.loads(data)["result"]["content"][0]["text"]
    batch = json.dumps([json.loads(note), json.loads(call)]).encode()
    status, _, data = live.request("POST", "/mcp", batch, headers)
    assert status == 200 and [r["id"] for r in json.loads(data)] == [2]
    assert live.request("POST", "/mcp", b"{not json", headers)[0] == 400
    assert live.request("POST", "/mcp", call, {
        "Content-Type": "application/json", "Tailscale-User-Login": "stranger@example.test"})[0] == 403
    assert live.request("GET", "/mcp")[0] == 405


def test_logs_carry_no_content(live, capfd):
    ctype, body = multipart({"text": (None, b"private words")})
    live.request("POST", "/v1/agent/turn", body, {"Content-Type": ctype, **OWNER})
    err = capfd.readouterr().err
    assert '"path": "/v1/agent/turn"' in err
    assert "private words" not in err and LOGIN not in err and "echo" not in err


# ---- the real speech path, through a fake gateway ----

class FakeGateway(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.seen.append((self.path, self.headers["Authorization"], body))
        data = json.dumps({"text": " spoken words "}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_spoken_turn_transcribes_through_the_gateway(monkeypatch):
    """Also the contract check for the AgentConfig attributes scribe.transcribe_memo reads."""
    gateway = serve(FakeGateway)
    gateway.seen = []
    wav = b"RIFF" + b"\x00" * 4 + b"WAVEfmt " + b"\x00" * 20 + b"data" + b"\x00" * 4 + b"\x01\x02" * 8
    bounds = []
    monkeypatch.setattr("anvil_serving.scribe.service.ogg_to_wav",
                        lambda ogg, max_seconds: bounds.append(max_seconds) or wav)
    config = make_config(router_url=f"http://127.0.0.1:{gateway.server_port}", route="stt-main")
    server = agent.make_server(config, ask=lambda agent_id, text: ("ok", "s", "stop"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ctype, body = multipart({"audio": ("t.ogg", b"OggS-synthetic")})
        status, _, data = Client(server).request("POST", "/v1/agent/turn", body, {"Content-Type": ctype, **OWNER})
        assert (status, json.loads(data)["transcript"]) == (200, "spoken words")
        path, auth, payload = gateway.seen[0]
        assert (path, auth) == ("/v1/audio/transcriptions", "Bearer ask_synthetic")
        assert payload["route"] == "stt-main" and payload["purpose"] == "stt"
        assert base64.b64decode(payload["audio_b64"]).endswith(wav[-16:])
        assert bounds == [300]  # agent turns are short; the bound comes from AgentConfig
    finally:
        server.shutdown()
        server.server_close()
        gateway.shutdown()


def test_spoken_turn_gets_503_when_every_decode_slot_is_busy(monkeypatch):
    monkeypatch.setattr(agent, "_SLOT_WAIT_SECONDS", 0.1)
    release = threading.Event()

    def slow(ogg):
        release.wait(5)
        return "late words"
    server = agent.make_server(make_config(max_concurrent_transcriptions=1), transcribe=slow,
                               ask=lambda agent_id, text: ("ok", "s", "stop"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = Client(server)
    ctype, body = multipart({"audio": ("t.ogg", b"OggS-synthetic")})
    first = {}
    holder = threading.Thread(target=lambda: first.update(zip(("status", "headers", "data"), client.request(
        "POST", "/v1/agent/turn", body, {"Content-Type": ctype, **OWNER}))))
    try:
        holder.start()
        time.sleep(0.2)  # the first turn now holds the only slot
        status, _, data = client.request("POST", "/v1/agent/turn", body, {"Content-Type": ctype, **OWNER})
        assert (status, json.loads(data)) == (503, {"error": "busy"})
        release.set()
        holder.join(5)
        assert first["status"] == 200 and json.loads(first["data"])["transcript"] == "late words"
    finally:
        release.set()
        server.shutdown()
        server.server_close()

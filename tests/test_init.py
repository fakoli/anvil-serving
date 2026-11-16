"""Tests for `anvil-serving init` — generate a consistent
docker-compose.yml + serves.toml + router.toml + operator-topology.toml bring-up.
`nvidia-smi` is injected via `_run`, so these run with no GPU, no docker, and
no network.
"""
import json
import os

import pytest

from anvil_serving import init, deploy


def _run_missing(*a, **k):
    raise FileNotFoundError("nvidia-smi not found")


CSV = (
    "0, GPU-33333333-3333-3333-3333-333333333333, NVIDIA GeForce RTX 5090\n"
    "1, GPU-11111111-1111-1111-1111-111111111111, NVIDIA RTX PRO 6000 Blackwell\n"
)

CAPACITY_CSV = (
    "0, GPU-33333333-3333-3333-3333-333333333333, NVIDIA GeForce RTX 5090, 32607\n"
    "1, GPU-11111111-1111-1111-1111-111111111111, NVIDIA RTX PRO 6000 Blackwell, 97887\n"
)


def _run_ok(*a, **k):
    return CSV


def _run_capacity(*a, **k):
    return CAPACITY_CSV


def _run_tailnet(*a, **k):
    return "100.64.0.10\n"


def _scaffold_home(*args, **kwargs):
    return init.scaffold_home(*args, detect_host=False, **kwargs)


def _card(tmp_path, name, **fields):
    cards = tmp_path / "model-library" / "cards"
    cards.mkdir(parents=True, exist_ok=True)
    data = {"local_path": str(tmp_path / "models" / name), "id": name,
            "format": "safetensors", "sglang_loadable": True, "size_gb": 10.0}
    data.update(fields)
    (cards / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    return data


# ---- pick_model ------------------------------------------------------------------


# ---- run(): writes four files, mutually consistent -----------------------------


def test_invalid_topology_input_cannot_partially_rewrite_existing_files(tmp_path):
    out_dir = tmp_path / "onboard"
    out_dir.mkdir()
    originals = {}
    for name in ("docker-compose.yml", "router.toml", "operator-topology.toml"):
        path = out_dir / name
        path.write_text(f"original {name}\n", encoding="utf-8")
        originals[name] = path.read_text(encoding="utf-8")
    with pytest.raises(init.InitError):
        init.run(model="/w/model", out_dir=str(out_dir), port=0, _run=_run_missing)
    for name, expected in originals.items():
        assert (out_dir / name).read_text(encoding="utf-8") == expected
    assert list(out_dir.glob("*.anvil.bak.*")) == []


# ---- GPU pinning: UUID present / nvidia-smi absent (T007 wiring) ---------------


# ---- catalog-driven pick + thinking_default ------------------------------------


def test_init_catalog_thinking_default_disables_at_generation(tmp_path):
    catalog_dir = tmp_path / "model-library"
    _card(tmp_path, "thinky", local_path=str(tmp_path / "weights" / "thinky"), thinking_default=True)
    out_dir = tmp_path / "onboard"
    result = init.run(catalog_dir=str(catalog_dir), out_dir=str(out_dir), gpu="0", _run=_run_missing)
    compose = open(result["compose"], encoding="utf-8").read()
    assert "enable_thinking" in compose
    assert result["disable_thinking"] is True


# ---- CLI -------------------------------------------------------------------------


def test_cli_dispatches_init_and_rejects_unknown_onboard(tmp_path, monkeypatch, capsys):
    from anvil_serving import cli
    monkeypatch.setattr(deploy._gpus, "resolve_gpu", lambda spec, _run=None: (None, None))
    out1 = tmp_path / "a"
    assert cli.main(["init", "--single-model", "--model", "/w/model",
                     "--out-dir", str(out1)]) == 0
    assert os.path.isfile(out1 / "router.toml")
    assert cli.main(["onboard", "--single-model", "--model", "/w/model"]) == 2
    assert "unknown command: onboard" in capsys.readouterr().err


# ---- init --home: full operational config-set scaffold ---------------------------

# The reference-instance host values that MUST NOT ride onto a fresh machine.
_REAL_HOST_VALUES = (
    "GPU-11111111-1111-1111-1111-111111111111",
    "GPU-33333333-3333-3333-3333-333333333333",
    "100.64.0.10",
)
_EXPECTED_HOME_FILES = {
    "router.toml", "example.toml", "example-docker.toml",
    "host.toml", "serve-recipes.toml", "image-builds.toml",
    "anvil-router.deepseek-pi.toml", "anvil-router.live.toml",
    "anvil-router.qwen35-rollback.toml",
    "serves.toml", "services.toml", "serves.voice.toml", "serves.comfyui.toml",
    "docker-compose.yml", "docker-compose.voice-audio.yml", "docker-compose.voice-proxy.yml",
    "docker-compose.comfyui.yml",
    "Dockerfile.omni-small", "Dockerfile.comfyui", "install-comfyui-node.sh",
    "operator-topology.toml", ".env.example", "voice.toml", "edge.toml",
}


def test_scaffold_home_contains_no_legacy_hardware_or_live_role_names(tmp_path):
    _scaffold_home(out_dir=str(tmp_path))
    legacy = (
        "HEAVY_GPU_UUID",
        "FAST_GPU_UUID",
        "heavy-local",
        "fast-local",
        "ANVIL_HEAVY_LOCAL_KEY",
        "ANVIL_FAST_LOCAL_KEY",
        '--heavy-gpu-uuid',
        '--fast-gpu-uuid',
    )
    for path in tmp_path.iterdir():
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for token in legacy:
            assert token not in text, (path.name, token)


def test_scaffold_home_never_writes_secrets_only_env_example(tmp_path):
    _scaffold_home(out_dir=str(tmp_path))
    # Ships the template, never a populated `.env`; and the template holds no
    # filled-in secret values (keys present, values empty).
    assert not os.path.exists(tmp_path / ".env")
    env = (tmp_path / ".env.example").read_text(encoding="utf-8")
    for line in env.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key in {"COMPUTE_A_GPU_UUID", "COMPUTE_B_GPU_UUID"}:
            assert value.startswith("GPU-REPLACE-WITH-")
        else:
            assert value.strip() == "", f".env.example ships a non-empty secret: {line}"


def test_scaffold_home_is_idempotent_no_backup_on_first_run(tmp_path):
    result = _scaffold_home(out_dir=str(tmp_path))
    assert result["backed_up"] == []  # clean target: nothing to back up


def test_scaffold_home_injects_detected_host_values(tmp_path):
    result = init.scaffold_home(
        out_dir=str(tmp_path),
        _gpu_run=_run_capacity,
        _tailscale_run=_run_tailnet,
    )

    assert result["discovery"]["compute_a_gpu"]["source"] == "detected"
    compose = (tmp_path / "docker-compose.yml").read_text(encoding="utf-8")
    voice = (tmp_path / "docker-compose.voice-audio.yml").read_text(encoding="utf-8")
    assert _REAL_HOST_VALUES[0] in compose
    assert _REAL_HOST_VALUES[1] in compose
    assert _REAL_HOST_VALUES[2] in voice
    assert "GPU-REPLACE-WITH" not in compose
    assert "REPLACE-WITH-YOUR-TAILNET-IP" not in voice


def test_discover_host_rejects_non_tailnet_ip_override():
    with pytest.raises(init.InitError, match="100.64.0.0/10"):
        init.discover_host(tailnet_ip="192.168.1.10", probe=False)


def test_init_cli_config_home_backs_up_existing_operator_file(tmp_path, monkeypatch):
    home = tmp_path / "operator-home"
    home.mkdir()
    (home / "serves.toml").write_text("# operator hand edits\n", encoding="utf-8")
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(home))

    assert init.main(["--no-detect-host"]) == 0

    backup = home / "serves.toml.anvil.bak.1"
    assert backup.read_text(encoding="utf-8") == "# operator hand edits\n"


# ---- packaging: the set ships as package data and resolves like an installed tool ----


# ---- init: authenticated router by default (WP-04) ---------------------------------


@pytest.fixture
def token_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(home))
    monkeypatch.setenv("ANVIL_SERVING_NO_HOME_ENV", "1")
    monkeypatch.delenv("ANVIL_ROUTER_TOKEN", raising=False)
    return home


def _init_cli(capsys, *extra):
    from anvil_serving import cli
    assert cli.main(["init", "--no-detect-host", *extra]) == 0
    out = capsys.readouterr()
    return out.out + out.err


def _env_token(home):
    for line in (home / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("ANVIL_ROUTER_TOKEN="):
            return line.split("=", 1)[1]
    return None


def test_init_scaffold_router_declares_auth_env(token_home, capsys):
    _init_cli(capsys)
    import tomllib
    cfg = tomllib.loads((token_home / "router.toml").read_text(encoding="utf-8"))
    assert cfg["server"]["auth_env"] == "ANVIL_ROUTER_TOKEN"


def test_init_generates_private_token_without_printing_it(token_home, capsys):
    output = _init_cli(capsys)
    token = _env_token(token_home)
    assert token and len(token) >= 32
    assert (os.stat(token_home / ".env").st_mode & 0o777) == 0o600
    assert token not in output
    assert str(token_home / ".env") in output


def test_init_twice_keeps_token(token_home, capsys):
    _init_cli(capsys)
    first = _env_token(token_home)
    output = _init_cli(capsys)
    assert _env_token(token_home) == first
    assert first not in output
    assert (token_home / ".env").read_text(encoding="utf-8").count("ANVIL_ROUTER_TOKEN=") == 1


def test_init_respects_existing_environment_variable(token_home, monkeypatch, capsys):
    monkeypatch.setenv("ANVIL_ROUTER_TOKEN", "already-set-by-operator")
    output = _init_cli(capsys)
    assert not (token_home / ".env").exists()
    assert "already-set-by-operator" not in output


def test_init_appends_to_existing_env_preserving_lines_and_tightens_loose_mode(
        token_home, capsys):
    token_home.mkdir()
    env = token_home / ".env"
    env.write_text("OTHER=keep-me\n", encoding="utf-8")
    env.chmod(0o644)
    output = _init_cli(capsys)
    text = env.read_text(encoding="utf-8")
    assert text.startswith("OTHER=keep-me\n")
    assert _env_token(token_home) not in output
    assert "permissions set to 0600" in output
    assert (os.stat(env).st_mode & 0o777) == 0o600


def test_init_refuses_symlinked_env(token_home, tmp_path, capsys):
    from anvil_serving import cli
    token_home.mkdir()
    target = tmp_path / "elsewhere"
    target.write_text("", encoding="utf-8")
    (token_home / ".env").symlink_to(target)
    assert cli.main(["init", "--no-detect-host"]) == 2
    assert "symlink" in capsys.readouterr().err
    assert target.read_text(encoding="utf-8") == ""


def test_init_out_dir_outside_home_notes_where_router_reads(token_home, tmp_path, capsys):
    output = _init_cli(capsys, "--out-dir", str(tmp_path / "other"))
    assert str(token_home / ".env") in output
    assert _env_token(tmp_path / "other")


def test_scaffolded_router_refuses_without_token_and_serves_with_it(token_home, capsys):
    import http.client
    import threading
    from anvil_serving import envfile
    from anvil_serving.router.serve import build_server
    from tests.router.helpers import StaticBackend

    output = _init_cli(capsys)
    token = _env_token(token_home)
    assert token not in output
    env = {"ANVIL_ROUTER_TOKEN": envfile.read_dotenv(str(token_home / ".env"))["ANVIL_ROUTER_TOKEN"]}
    server = build_server(
        str(token_home / "router.toml"), port=0, env=env,
        backends={"primary-local": StaticBackend(["ok"]), "omni-local": StaticBackend(["ok"])},
    )
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    body = json.dumps({"model": "llm.primary", "messages": [{"role": "user", "content": "hi"}]})

    def post(headers):
        host, port = server.server_address[:2]
        conn = http.client.HTTPConnection(host, port, timeout=5)
        try:
            conn.request("POST", "/v1/chat/completions", body,
                         {"Content-Type": "application/json", **headers})
            return conn.getresponse().status
        finally:
            conn.close()

    try:
        assert post({}) == 401
        assert post({"Authorization": "Bearer wrong"}) == 401
        assert post({"Authorization": "Bearer " + token}) == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_init_never_overwrites_existing_token_in_env_file(token_home, capsys):
    token_home.mkdir()
    (token_home / ".env").write_text("ANVIL_ROUTER_TOKEN=operator-value\n", encoding="utf-8")
    (token_home / ".env").chmod(0o600)
    output = _init_cli(capsys)
    assert _env_token(token_home) == "operator-value"
    assert "operator-value" not in output


# ---- init --gateway-only: gateway in front of an existing server (WP-05) -----------


class _StubUpstream:
    """Stdlib OpenAI-compatible upstream: records the requests it receives."""

    def __init__(self):
        import http.server
        import threading
        seen = self.seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, payload):
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._send({"status": "ok", "data": [{"id": "stub-model"}]})

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                seen.append(json.loads(body))
                self._send({"id": "c1", "object": "chat.completion", "model": "stub-model",
                            "choices": [{"index": 0, "finish_reason": "stop",
                                         "message": {"role": "assistant", "content": "pong"}}],
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d/v1" % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def test_gateway_only_end_to_end_without_docker_or_gpu(token_home, capsys):
    import http.client
    import threading
    from anvil_serving import envfile
    from anvil_serving.router.serve import build_server

    upstream = _StubUpstream()
    try:
        output = _init_cli(capsys, "--gateway-only", "--upstream", upstream.url,
                           "--model", "stub-model")
        assert sorted(p.name for p in token_home.iterdir()) == [".env", "router.toml"]
        token = _env_token(token_home)
        assert token and token not in output
        server = build_server(
            str(token_home / "router.toml"), port=0,
            env={"ANVIL_ROUTER_TOKEN": envfile.read_dotenv(str(token_home / ".env"))["ANVIL_ROUTER_TOKEN"]})
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()

        def call(method, path, body=None, auth=True):
            host, port = server.server_address[:2]
            conn = http.client.HTTPConnection(host, port, timeout=5)
            headers = {"Content-Type": "application/json"}
            if auth:
                headers["Authorization"] = "Bearer " + token
            try:
                conn.request(method, path, json.dumps(body) if body else None, headers)
                resp = conn.getresponse()
                return resp.status, resp.read()
            finally:
                conn.close()

        try:
            status, raw = call("GET", "/v1/models")
            assert status == 200 and b"llm.primary" in raw
            chat = {"model": "llm.primary", "messages": [{"role": "user", "content": "ping"}]}
            status, raw = call("POST", "/v1/chat/completions", chat)
            assert status == 200 and b"pong" in raw
            assert upstream.seen and upstream.seen[0]["model"] == "stub-model"
            assert call("POST", "/v1/chat/completions", {**chat, "model": "nope"})[0] == 404
            assert call("POST", "/v1/chat/completions", chat, auth=False)[0] == 401
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        # second run: token kept, router.toml unchanged
        before = _env_token(token_home)
        output = _init_cli(capsys, "--gateway-only", "--upstream", upstream.url,
                           "--model", "stub-model")
        assert _env_token(token_home) == before and before not in output
    finally:
        upstream.close()


def test_gateway_only_options_alias_and_context_limit(token_home, capsys):
    import tomllib
    _init_cli(capsys, "--gateway-only", "--upstream", "https://example.test:8443/v1",
              "--model", "m", "--alias", "Llm.Fast", "--context-limit", "32768")
    cfg = tomllib.loads((token_home / "router.toml").read_text(encoding="utf-8"))
    (tier,) = cfg["router"]["tiers"]
    assert tier["context_limit"] == 32768 and tier["dialect"] == "openai"
    assert tier["privacy"] == "local" and tier["tool_support"] is True
    assert cfg["router"]["model_routes"] == {"llm.fast": tier["id"]}
    assert cfg["server"]["auth_env"] == "ANVIL_ROUTER_TOKEN"


@pytest.mark.parametrize("args,needle", [
    (["--upstream", "http://localhost:11434/v1", "--model", "m"], "127.0.0.1"),
    (["--upstream", "ftp://x/v1", "--model", "m"], "http"),
    (["--model", "m"], "--upstream"),
    (["--upstream", "http://127.0.0.1:1/v1"], "--model"),
    (["--upstream", "http://127.0.0.1:1/v1", "--model", "m", "--context-limit", "0"], "context"),
])
def test_gateway_only_rejects_bad_input(token_home, capsys, args, needle):
    from anvil_serving import cli
    assert cli.main(["init", "--gateway-only", *args]) == 2
    assert needle in capsys.readouterr().err
    assert not token_home.exists() or not (token_home / "router.toml").exists()


def test_bare_init_hints_gateway_only_when_no_gpu_detected(token_home, capsys):
    output = _init_cli(capsys)
    assert "--gateway-only" in output and "--single-model" in output

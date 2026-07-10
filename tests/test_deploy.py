"""Tests for `anvil_serving.deploy` — render a tuned docker-compose for one
local model serve. `nvidia-smi` / docker are injected, so these run with no
GPU, no docker, and no network.
"""


import pytest

from anvil_serving import deploy, serves

CSV = (
    "0, GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1, NVIDIA GeForce RTX 5090\n"
    "1, GPU-d0f446cf-1771-414c-e116-a39138798a8c, NVIDIA RTX PRO 6000 Blackwell\n"
)


def _run_ok(*a, **k):
    return CSV


def _run_missing(*a, **k):
    raise FileNotFoundError("nvidia-smi not found")


# ---- gpu_uuid / cuda_visible (genericity:T007) ---------------------------------

def test_deploy_gpu_index_resolves_to_uuid_env_block():
    out = deploy.render("/w/model", gpu=1, _run=_run_ok)
    assert "CUDA_VISIBLE_DEVICES: GPU-d0f446cf-1771-414c-e116-a39138798a8c" in out
    assert "CUDA_DEVICE_ORDER: PCI_BUS_ID" in out
    assert 'device_ids: ["GPU-d0f446cf-1771-414c-e116-a39138798a8c"]' in out


def test_deploy_gpu_uuid_spec_passthrough():
    uuid = "GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1"
    out = deploy.render("/w/model", gpu=uuid, _run=_run_ok)
    assert f"CUDA_VISIBLE_DEVICES: {uuid}" in out
    assert f'device_ids: ["{uuid}"]' in out


def test_deploy_nvidia_smi_absent_falls_back_to_integer_no_crash(capsys):
    out = deploy.render("/w/model", gpu=1, _run=_run_missing)
    assert 'device_ids: ["1"]' in out
    assert "CUDA_VISIBLE_DEVICES" not in out  # no unresolved env pin emitted
    err = capsys.readouterr().err
    assert "WARNING" in err and "nvidia-smi" in err


def test_deploy_cli_writes_compose(tmp_path, monkeypatch):
    out_path = tmp_path / "compose.yml"
    monkeypatch.setattr(deploy._gpus, "resolve_gpu", lambda spec, _run=None: (None, None))
    # --no-manifest: without it main() falls back to --manifest-out's default
    # ("./serves.toml"), which would write into the pytest-invoking CWD — a
    # real side effect this test must not have.
    deploy.main(["--model", "/w/model", "--gpu", "0", "--out", str(out_path), "--no-manifest"])
    assert out_path.exists()
    assert "sglang.launch_server" in out_path.read_text(encoding="utf-8")


def test_serves_render_help_uses_canonical_usage(capsys):
    with pytest.raises(SystemExit) as exc:
        serves.main(["render", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage: anvil-serving serves render" in out
    assert "--model" in out
    assert "--manifest-out" in out


def test_serves_render_matches_deploy_cli_output(tmp_path, monkeypatch, capsys):
    deploy_out = tmp_path / "deploy-compose.yml"
    render_out = tmp_path / "render-compose.yml"
    deploy_manifest = tmp_path / "deploy-serves.toml"
    render_manifest = tmp_path / "render-serves.toml"
    monkeypatch.setattr(deploy._gpus, "resolve_gpu", lambda spec, _run=None: (None, None))

    deploy.main([
        "--model", "/w/model",
        "--out", str(deploy_out),
        "--manifest-out", str(deploy_manifest),
        "--served-name", "local-test",
    ])
    capsys.readouterr()
    serves.main([
        "render",
        "--model", "/w/model",
        "--out", str(render_out),
        "--manifest-out", str(render_manifest),
        "--served-name", "local-test",
    ])
    capsys.readouterr()

    assert deploy_out.read_text(encoding="utf-8") == render_out.read_text(encoding="utf-8")
    deploy_text = deploy_manifest.read_text(encoding="utf-8").replace(str(deploy_out).replace("\\", "/"), "<compose>")
    render_text = render_manifest.read_text(encoding="utf-8").replace(str(render_out).replace("\\", "/"), "<compose>")
    assert deploy_text == render_text


# ---- loopback default / --expose-lan (genericity:T008) -------------------------

def test_deploy_loopback_default_publishes_127_0_0_1():
    out = deploy.render("/w/model", gpu=0, port=30000, _run=_run_missing)
    assert 'ports: ["127.0.0.1:30000:30000"]' in out


def test_deploy_expose_lan_publishes_0_0_0_0_and_warns(capsys):
    out = deploy.render("/w/model", gpu=0, port=30000, bind="0.0.0.0", _run=_run_missing)
    assert 'ports: ["0.0.0.0:30000:30000"]' in out
    err = capsys.readouterr().err
    assert "WARNING" in err and "SECURITY.md" in err


def test_deploy_loopback_default_prints_no_public_bind_warning(capsys):
    deploy.render("/w/model", gpu=0, port=30000, _run=_run_missing)
    err = capsys.readouterr().err
    assert "network" not in err  # only the nvidia-smi warning, no public-bind warning


def test_deploy_cli_expose_lan_flag(tmp_path, monkeypatch):
    out_path = tmp_path / "compose.yml"
    monkeypatch.setattr(deploy._gpus, "resolve_gpu", lambda spec, _run=None: (None, None))
    deploy.main(["--model", "/w/model", "--out", str(out_path), "--expose-lan", "--no-manifest"])
    assert '0.0.0.0:30000:30000' in out_path.read_text(encoding="utf-8")


def test_deploy_cli_bind_flag_overrides(tmp_path, monkeypatch):
    out_path = tmp_path / "compose.yml"
    monkeypatch.setattr(deploy._gpus, "resolve_gpu", lambda spec, _run=None: (None, None))
    deploy.main(["--model", "/w/model", "--out", str(out_path), "--bind", "192.168.1.5", "--no-manifest"])
    assert '192.168.1.5:30000:30000' in out_path.read_text(encoding="utf-8")


# ---- emits serves.toml entry + router-tier stub (genericity:T009) --------------

def test_deploy_render_serve_entry_matches_compose_fields():
    entry = deploy.render_serve_entry(
        "heavy", "sglang", 30000, "qwen35-awq-local", "docker compose -f x.yml up -d sglang")
    assert 'name = "heavy"' in entry
    assert 'container = "sglang"' in entry
    assert 'port = 30000' in entry
    assert 'model = "qwen35-awq-local"' in entry
    assert 'engine = "sglang"' in entry


def test_deploy_append_serve_entry_creates_manifest(tmp_path):
    manifest = tmp_path / "serves.toml"
    ok = deploy.append_serve_entry(
        str(manifest), "heavy", "sglang", 30000, "qwen35-awq-local", "docker compose up -d sglang")
    assert ok is True
    parsed = deploy._serves.load_manifest(str(manifest))
    assert len(parsed) == 1
    assert parsed[0]["name"] == "heavy"
    assert parsed[0]["container"] == "sglang"
    assert parsed[0]["port"] == 30000
    assert parsed[0]["model"] == "qwen35-awq-local"
    assert parsed[0]["engine"] == "sglang"


def test_deploy_append_serve_entry_appends_to_existing(tmp_path):
    manifest = tmp_path / "serves.toml"
    deploy.append_serve_entry(str(manifest), "heavy", "sglang", 30000, "m1", "up1")
    deploy.append_serve_entry(str(manifest), "fast", "vllm", 30001, "m2", "up2")
    parsed = deploy._serves.load_manifest(str(manifest))
    assert {s["name"] for s in parsed} == {"heavy", "fast"}


def test_deploy_append_serve_entry_no_duplicate_on_rerun(tmp_path, capsys):
    manifest = tmp_path / "serves.toml"
    deploy.append_serve_entry(str(manifest), "heavy", "sglang", 30000, "m1", "up1")
    ok = deploy.append_serve_entry(str(manifest), "heavy", "sglang", 30000, "m1", "up1")
    assert ok is False
    parsed = deploy._serves.load_manifest(str(manifest))
    assert len(parsed) == 1
    assert "already present" in capsys.readouterr().err


def test_deploy_render_tier_stub_model_and_port_match_serve():
    stub = deploy.render_tier_stub("heavy-local", "qwen35-awq-local", 30000)
    assert 'model         = "qwen35-awq-local"' in stub
    assert 'base_url      = "http://127.0.0.1:30000/v1"' in stub
    assert 'privacy       = "local"' in stub
    assert 'dialect       = "openai"' in stub


def test_deploy_cli_emits_manifest_and_tier_stub(tmp_path, monkeypatch, capsys):
    out_path = tmp_path / "compose.yml"
    manifest_path = tmp_path / "serves.toml"
    monkeypatch.setattr(deploy._gpus, "resolve_gpu", lambda spec, _run=None: (None, None))
    deploy.main(["--model", "/w/model", "--out", str(out_path), "--port", "30000",
                "--served-name", "qwen35-awq-local", "--tier-id", "heavy-local",
                "--manifest-out", str(manifest_path)])
    parsed = deploy._serves.load_manifest(str(manifest_path))
    assert len(parsed) == 1
    assert parsed[0] == {
        "name": "heavy-local", "container": "sglang", "port": 30000,
        "model": "qwen35-awq-local", "served_name": "qwen35-awq-local",
        "engine": "sglang", "health": "/health",
        "up": ["docker", "compose", "-f", str(out_path).replace("\\", "/"), "up", "-d", "sglang"],
    }
    out = capsys.readouterr().out
    assert "router.tiers" in out
    assert 'model         = "qwen35-awq-local"' in out
    assert "http://127.0.0.1:30000/v1" in out


def test_deploy_cli_no_manifest_skips_emission(tmp_path, monkeypatch):
    out_path = tmp_path / "compose.yml"
    manifest_path = tmp_path / "serves.toml"
    monkeypatch.setattr(deploy._gpus, "resolve_gpu", lambda spec, _run=None: (None, None))
    deploy.main(["--model", "/w/model", "--out", str(out_path),
                "--manifest-out", str(manifest_path), "--no-manifest"])
    assert not manifest_path.exists()


# ---- vLLM engine branch (genericity:T010) ---------------------------------------

def test_deploy_engine_vllm_renders_ipc_host_and_v2_runner_off():
    out = deploy.render("/w/model", gpu=0, engine="vllm", served_name="gpt-oss-20b",
                        port=30001, _run=_run_missing)
    assert "ipc: host" in out
    assert 'VLLM_USE_V2_MODEL_RUNNER: "0"' in out
    assert "vllm-gpt-oss-20b" in out  # container name derived from served-name


def test_deploy_engine_vllm_argv_matches_multiplexer_build_cmd():
    out = deploy.render("/w/model", gpu=0, engine="vllm", served_name="gpt-oss-20b",
                        port=30001, gpu_mem_util=0.9, context=65536, _run=_run_missing)
    entry = {"name": "gpt-oss-20b", "model_path": "/models/local", "port": 30001,
             "engine": "vllm",
             "args": ["--gpu-memory-utilization", "0.9", "--max-model-len", "65536",
                      "--reasoning-parser", "qwen3", "--enable-auto-tool-choice",
                      "--tool-call-parser", "qwen3_coder"]}
    from anvil_serving import multiplexer
    argv = multiplexer.build_cmd(entry)
    for tok in argv[1:]:
        assert tok in out or tok.replace('"', "'") in out or ("'" + tok + "'") in out or tok in out


def test_deploy_engine_vllm_gpu_pinning_env_matches_sglang_pattern():
    out = deploy.render("/w/model", gpu=1, engine="vllm", served_name="m", port=30001, _run=_run_ok)
    assert "CUDA_DEVICE_ORDER: PCI_BUS_ID" in out
    assert "CUDA_VISIBLE_DEVICES: GPU-d0f446cf-1771-414c-e116-a39138798a8c" in out


def test_deploy_engine_sglang_default_unchanged():
    out_default = deploy.render("/w/model", gpu=0, _run=_run_missing)
    out_explicit = deploy.render("/w/model", gpu=0, engine="sglang", _run=_run_missing)
    assert out_default == out_explicit
    assert "sglang.launch_server" in out_default
    assert "container_name: sglang" in out_default


def test_deploy_infer_engine_defaults_sglang_when_no_config(tmp_path):
    assert deploy._infer_engine(str(tmp_path)) == "sglang"


def test_deploy_infer_engine_nvfp4_prefers_vllm(tmp_path):
    (tmp_path / "config.json").write_text(
        '{"quantization_config": {"quant_method": "modelopt", "format": "nvfp4"}}',
        encoding="utf-8")
    assert deploy._infer_engine(str(tmp_path)) == "vllm"


def test_deploy_infer_engine_awq_stays_sglang(tmp_path):
    (tmp_path / "config.json").write_text(
        '{"quantization_config": {"quant_method": "awq"}}', encoding="utf-8")
    assert deploy._infer_engine(str(tmp_path)) == "sglang"


def test_deploy_cli_engine_vllm_end_to_end(tmp_path, monkeypatch):
    out_path = tmp_path / "compose.yml"
    manifest_path = tmp_path / "serves.toml"
    monkeypatch.setattr(deploy._gpus, "resolve_gpu", lambda spec, _run=None: (None, None))
    deploy.main(["--model", "/w/model", "--out", str(out_path), "--engine", "vllm",
                "--served-name", "gpt-oss-20b", "--port", "30001",
                "--manifest-out", str(manifest_path)])
    parsed = deploy._serves.load_manifest(str(manifest_path))
    assert parsed[0]["container"] == "vllm-gpt-oss-20b"
    assert parsed[0]["engine"] == "vllm"
    assert parsed[0]["up"][-1] == "vllm"  # `up -d vllm` (the compose SERVICE key)


# ---- thinking-disable at generation time (genericity:T011) ---------------------

def test_deploy_thinking_disable_sglang_injects_chat_template_kwargs():
    out = deploy.render("/w/model", gpu=0, disable_thinking=True, _run=_run_missing)
    assert "--chat-template-kwargs" in out
    assert '"enable_thinking": false' in out


def test_deploy_thinking_disable_vllm_injects_chat_template_kwargs():
    out = deploy.render("/w/model", gpu=0, engine="vllm", disable_thinking=True, _run=_run_missing)
    assert "--chat-template-kwargs" in out
    assert '"enable_thinking": false' in out


def test_deploy_non_thinking_model_renders_unchanged():
    out_plain = deploy.render("/w/model", gpu=0, _run=_run_missing)
    out_explicit_false = deploy.render("/w/model", gpu=0, disable_thinking=False, _run=_run_missing)
    assert out_plain == out_explicit_false
    assert "chat-template-kwargs" not in out_plain


def test_deploy_read_thinking_default_true(tmp_path):
    facts = tmp_path / "card.json"
    facts.write_text('{"thinking_default": true}', encoding="utf-8")
    assert deploy.read_thinking_default(str(facts)) is True


def test_deploy_read_thinking_default_false_when_absent():
    assert deploy.read_thinking_default(None) is False
    assert deploy.read_thinking_default("/does/not/exist.json") is False


def test_deploy_cli_disable_thinking_flag_forces_regardless_of_catalog(tmp_path, monkeypatch):
    out_path = tmp_path / "compose.yml"
    monkeypatch.setattr(deploy._gpus, "resolve_gpu", lambda spec, _run=None: (None, None))
    deploy.main(["--model", "/w/model", "--out", str(out_path),
                "--disable-thinking", "--no-manifest"])
    assert "enable_thinking" in out_path.read_text(encoding="utf-8")


def test_deploy_cli_model_facts_thinking_default_auto_disables(tmp_path, monkeypatch):
    out_path = tmp_path / "compose.yml"
    facts = tmp_path / "card.json"
    facts.write_text('{"thinking_default": true}', encoding="utf-8")
    monkeypatch.setattr(deploy._gpus, "resolve_gpu", lambda spec, _run=None: (None, None))
    deploy.main(["--model", "/w/model", "--out", str(out_path),
                "--model-facts", str(facts), "--no-manifest"])
    assert "enable_thinking" in out_path.read_text(encoding="utf-8")


def test_deploy_cli_no_facts_no_flag_stays_unchanged(tmp_path, monkeypatch):
    out_path = tmp_path / "compose.yml"
    monkeypatch.setattr(deploy._gpus, "resolve_gpu", lambda spec, _run=None: (None, None))
    deploy.main(["--model", "/w/model", "--out", str(out_path), "--no-manifest"])
    assert "enable_thinking" not in out_path.read_text(encoding="utf-8")

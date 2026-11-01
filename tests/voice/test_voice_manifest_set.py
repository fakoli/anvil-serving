from anvil_serving.voice.serves.stt import STTServe, STTServeConfig


def test_managed_voice_admission_loads_colocated_manifest_set(tmp_path, monkeypatch):
    (tmp_path / "serves.toml").write_text('[[gpu_roles]]\nid = "aux"\nvram_mib = 100\n[[serve]]\nname = "omni"\ncontainer = "omni"\nruntime = "docker"\nport = 30003\nmodel = "omni"\nengine = "vllm"\ngpu_role = "aux"\nvram_mib = 80\nresidency = "resident"\nup = "echo omni"\n')
    voice_manifest = tmp_path / "serves.voice.toml"; voice_manifest.write_text('[[serve]]\nname = "stt"\ncontainer = "stt"\nruntime = "docker"\nport = 30010\nmodel = "stt"\nengine = "audio"\ngpu_role = "aux"\nvram_mib = 30\nresidency = "resident"\nup = "echo stt"\n')
    seen = {}
    def fake_cmd_up(serves, names, **kwargs): seen["names"] = names; seen["serves"] = {serve["name"] for serve in serves}; return 0
    monkeypatch.setattr("anvil_serving.serves.cmd_up", fake_cmd_up)
    serve = STTServe(STTServeConfig(base_url="http://127.0.0.1:30010/v1", model="stt", manifest_path=str(voice_manifest)))
    assert serve.bring_up() == 0 and seen == {"names": ["stt"], "serves": {"omni", "stt"}}

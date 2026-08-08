from anvil_serving.voice.serves.stt import STTServe, STTServeConfig


def test_managed_voice_admission_loads_colocated_manifest_set(tmp_path, monkeypatch):
    (tmp_path / "serves.toml").write_text(
        """
[[gpu_roles]]
id = "aux"
vram_mib = 100

[[serve]]
name = "omni"
container = "omni"
runtime = "docker"
port = 30003
model = "omni"
engine = "vllm"
gpu_role = "aux"
vram_mib = 80
residency = "resident"
up = "echo omni"
""",
        encoding="utf-8",
    )
    voice_manifest = tmp_path / "serves.voice.toml"
    voice_manifest.write_text(
        """
[[serve]]
name = "stt"
container = "stt"
runtime = "docker"
port = 30010
model = "stt"
engine = "audio"
gpu_role = "aux"
vram_mib = 30
residency = "resident"
up = "echo stt"
""",
        encoding="utf-8",
    )
    seen = {}

    def fake_cmd_up(serves, names, **kwargs):
        seen["names"] = names
        seen["serves"] = {serve["name"] for serve in serves}
        return 0

    monkeypatch.setattr("anvil_serving.serves.cmd_up", fake_cmd_up)
    serve = STTServe(
        STTServeConfig(
            base_url="http://127.0.0.1:30010/v1",
            model="stt",
            manifest_path=str(voice_manifest),
        )
    )

    assert serve.bring_up() == 0
    assert seen == {"names": ["stt"], "serves": {"omni", "stt"}}

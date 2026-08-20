"""Declared split/exclusive serving-profile selection tests."""
import signal
import textwrap

import pytest

from anvil_serving import serves


def _write(path, text):
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return str(path)


def _manifest(tmp_path):
    return _write(tmp_path / "serves.toml", """
        [[gpu_roles]]
        id = "dark-compute-a"
        vram_mib = 97887
        reserve_mib = 3072

        [[gpu_roles]]
        id = "dark-compute-b"
        vram_mib = 97887
        reserve_mib = 3072

        [[serve]]
        name = "qwen"
        container = "qwen"
        runtime = "docker"
        port = 30002
        model = "qwen"
        engine = "vllm"
        gpu_role = "dark-compute-a"
        vram_mib = 90000
        residency = "resident"
        groups = ["qwen-thinkingcap"]

        [[serve]]
        name = "thinkingcap"
        container = "thinkingcap"
        runtime = "docker"
        port = 39031
        model = "thinkingcap"
        engine = "vllm"
        gpu_role = "dark-compute-b"
        vram_mib = 90000
        residency = "resident"
        groups = ["qwen-thinkingcap"]

        [[serve]]
        name = "deepseek"
        container = "deepseek"
        runtime = "docker"
        port = 39062
        model = "deepseek"
        engine = "vllm"
        gpu_roles = ["dark-compute-a", "dark-compute-b"]
        vram_mib = 90000
        residency = "on-demand"
        operating_mode = "dual-gpu-exclusive"
        tensor_parallel_size = 2
    """)


def _profiles(tmp_path):
    return _write(tmp_path / "serve-profiles.toml", """
        schema = "anvil-serving/serve-profiles/v1"

        [[profile]]
        id = "deepseek-tp2"
        mode = "dual-gpu-exclusive"
        exclusive_target = "deepseek"
        restore_group = "qwen-thinkingcap"
        startup_timeout = 1200

        [[profile]]
        id = "qwen-thinkingcap-split"
        mode = "split"
        exclusive_target = "deepseek"
        restore_group = "qwen-thinkingcap"
    """)


def test_load_serve_profiles_requires_complete_unique_rows(tmp_path):
    profiles = serves.load_serve_profiles(_profiles(tmp_path))
    assert [profile["id"] for profile in profiles] == [
        "deepseek-tp2", "qwen-thinkingcap-split",
    ]
    assert profiles[0]["startup_timeout"] == 1200
    assert profiles[1]["startup_timeout"] == serves.LIFECYCLE_READINESS_TIMEOUT_SECONDS
    assert all(
        profile["poll_interval"] == serves.LIFECYCLE_READINESS_POLL_SECONDS
        for profile in profiles
    )
    bad = _write(tmp_path / "bad.toml", """
        schema = "anvil-serving/serve-profiles/v1"
        [[profile]]
        id = "missing-target"
        mode = "split"
        restore_group = "split"
    """)
    with pytest.raises(serves.ServeProfileError, match="exclusive_target"):
        serves.load_serve_profiles(bad)


def test_profile_list_is_read_only_and_uses_operator_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path / "home"))
    assert serves.main([
        "profile", "list", "--manifest", _manifest(tmp_path),
        "--profiles", _profiles(tmp_path),
    ]) == 0
    output = capsys.readouterr().out
    assert "deepseek-tp2" in output
    assert "qwen-thinkingcap-split" in output


def test_profile_apply_delegates_to_the_existing_mode_transaction(tmp_path, monkeypatch, capsys):
    profiles = serves.load_serve_profiles(_profiles(tmp_path))
    loaded = serves.load_manifest(_manifest(tmp_path))
    monkeypatch.setattr(
        serves,
        "docker_states",
        lambda *_args, **_kwargs: {"qwen": "running", "thinkingcap": "running", "deepseek": "absent"},
    )
    seen = []
    monkeypatch.setattr(
        serves,
        "cmd_mode",
        lambda _serves, action, target, group, **kwargs: seen.append((
            action,
            target,
            group,
            kwargs["confirm"],
            kwargs["readiness_timeout"],
            kwargs["readiness_poll"],
        )) or 0,
    )
    assert serves.cmd_profile(
        loaded, profiles, "apply", "deepseek-tp2", confirm=True,
    ) == 0
    assert seen == [(
        "enter",
        "deepseek",
        "qwen-thinkingcap",
        True,
        1200,
        serves.LIFECYCLE_READINESS_POLL_SECONDS,
    )]
    assert "serving profile deepseek-tp2: enter" in capsys.readouterr().out


def test_split_profile_refuses_to_claim_an_unknown_existing_split(tmp_path, monkeypatch):
    profiles = serves.load_serve_profiles(_profiles(tmp_path))
    loaded = serves.load_manifest(_manifest(tmp_path))
    monkeypatch.setattr(
        serves,
        "docker_states",
        lambda *_args, **_kwargs: {"qwen": "absent", "thinkingcap": "absent", "deepseek": "absent"},
    )
    with pytest.raises(serves.ServeProfileError, match="already in split mode"):
        serves.cmd_profile(loaded, profiles, "apply", "qwen-thinkingcap-split")


def test_profile_apply_defers_first_sigint_until_transaction_finishes(
    tmp_path, monkeypatch, capsys,
):
    profiles = serves.load_serve_profiles(_profiles(tmp_path))
    loaded = serves.load_manifest(_manifest(tmp_path))
    monkeypatch.setattr(
        serves,
        "docker_states",
        lambda *_args, **_kwargs: {
            "qwen": "running", "thinkingcap": "running", "deepseek": "absent",
        },
    )
    installed = {}
    monkeypatch.setattr(signal, "getsignal", lambda _sig: signal.default_int_handler)

    def set_handler(_sig, handler):
        installed["handler"] = handler

    monkeypatch.setattr(signal, "signal", set_handler)

    def interrupted_mode(*_args, **_kwargs):
        installed["handler"](signal.SIGINT, None)
        return 0

    monkeypatch.setattr(serves, "cmd_mode", interrupted_mode)
    assert serves.cmd_profile(
        loaded, profiles, "apply", "deepseek-tp2", confirm=True,
    ) == 0
    assert "first Ctrl-C deferred" in capsys.readouterr().err

"""Dynamic desktop-secondary inputs fail closed before lifecycle work."""
from types import SimpleNamespace
import http.client

import pytest

from anvil_serving import desktop, desktop_modes
from tests.test_desktop_modes import dynamic_secondary, setup


def test_dynamic_profile_normalizes_alias_and_rejects_invalid_port(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    dynamic_secondary(monkeypatch, tmp_path)
    config = tmp_path / "desktop-modes.toml"
    text = config.read_text().replace('="llm.secondary"', '=" 3B.Fast "')
    config.write_text(text)
    assert desktop_modes._profile()["secondary_router_alias"] == "3b.fast"
    config.write_text(text.replace("router.example.test", "router.example.test:99999"))
    with pytest.raises(ValueError, match="router URL"):
        desktop_modes._profile()


def test_dynamic_router_http_exception_is_unavailable(monkeypatch):
    monkeypatch.setattr(desktop_modes.envfile, "read_dotenv", lambda _: {"TOKEN": "synthetic"})
    monkeypatch.setattr(desktop_modes.urllib.request, "build_opener", lambda *_: SimpleNamespace(
        open=lambda *_args, **_kwargs: (_ for _ in ()).throw(http.client.HTTPException("bad response"))))
    with pytest.raises(RuntimeError, match="unavailable"):
        desktop_modes._dynamic_secondary({
            "secondary_router_token_file": "client.env", "secondary_router_token_env": "TOKEN",
            "secondary_router_alias": "llm.secondary", "secondary_router_url": "https://router.example.test",
            "dynamic_models": [],
        })


def test_recipe_binding_rejects_public_gpu_placeholder(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    dynamic_secondary(monkeypatch, tmp_path)
    target = desktop_modes._profile()["dynamic_models"][0]
    target["up"][-1:-1] = ["--gpu-device", "GPU-REPLACE-WITH-HOST-DISCOVERED-UUID"]
    with pytest.raises(ValueError, match="cannot be loaded"):
        desktop_modes._recipe_start_binding(target)


def test_recipe_loader_keeps_child_diagnostic_visible(monkeypatch):
    seen = {}
    monkeypatch.setenv("DOCKER_HOST", "unsafe")
    monkeypatch.setattr(desktop_modes.subprocess, "run", lambda argv, **kwargs: seen.update(kwargs) or SimpleNamespace(returncode=17))
    with pytest.raises(RuntimeError, match="exit 17"):
        desktop_modes._load_recipe({"model": "selector-a", "registry": "/registry", "gpu_device": None},
                                   {"container": "target"}, 1)
    assert "capture_output" not in seen and seen["env"]["DOCKER_CONTEXT"] == desktop.CONTEXT
    assert "DOCKER_HOST" not in seen["env"]

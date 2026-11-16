"""Generic names are the defaults; old owner-derived names still work, with a warning."""
import warnings
from pathlib import Path

import pytest

from anvil_serving import multiplexer
from anvil_serving import init
from anvil_serving.legacy_names import LEGACY_NAMES, LegacyNameWarning
from anvil_serving.voice.bridge import ForwardingBridgeService, TCPBridgeRoute

ROUTE = [TCPBridgeRoute("stt", "127.0.0.1", 30110, "100.64.0.10", 30110)]
REPO = Path(__file__).resolve().parent.parent


def _none(_name):
    return False


def test_fresh_scaffold_uses_generic_names(tmp_path):
    init.scaffold_home(out_dir=str(tmp_path), detect_host=False)
    for path in tmp_path.iterdir():
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            for old in LEGACY_NAMES:
                if old != "mini":
                    assert old not in text, (path.name, old)
    assert "anvil-models" in (tmp_path / "docker-compose.yml").read_text(encoding="utf-8")


def test_example_compose_projects_use_generic_names():
    for name in ("experiment", "q36", "flexibility"):
        text = (REPO / f"examples/primary-node/docker-compose.{name}.yml").read_text(encoding="utf-8")
        want = "anvil-flexibility" if name == "flexibility" else "anvil-experiment"
        assert f"name: {want}" in text
    heavy = (REPO / "examples/primary-node/docker-compose.heavy.yml").read_text(encoding="utf-8")
    assert "anvil-models:/models:ro" in heavy


def test_default_registry_volume_is_generic(monkeypatch):
    monkeypatch.delenv("ANVIL_MODELS_VOLUME", raising=False)
    assert multiplexer.default_models_volume(_none) == "anvil-models"


def test_legacy_volume_host_keeps_working_with_warning(monkeypatch):
    monkeypatch.delenv("ANVIL_MODELS_VOLUME", raising=False)
    with pytest.warns(LegacyNameWarning, match="anvil-models.*MIGRATION-GENERIC-NAMES"):
        assert multiplexer.default_models_volume(lambda n: n == "fakoli-models") == "fakoli-models"


def test_docker_unreachable_fails_loudly_not_new_volume(monkeypatch):
    import subprocess
    monkeypatch.delenv("ANVIL_MODELS_VOLUME", raising=False)

    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "Cannot connect to the Docker daemon")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError, match="ANVIL_MODELS_VOLUME"):
        multiplexer.default_models_volume()


def test_docker_volume_not_found_is_false(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, "", "Error response from daemon: get x: no such volume"))
    assert multiplexer._docker_volume_exists("x") is False


def test_registry_file_warns_once_for_many_legacy_rows(tmp_path):
    row = ('{"name": "%s", "model_path": "/models/m", "est_weight_gb": 1, '
           '"gpu": 0, "port": 1, "volume": "fakoli-models"}')
    reg = tmp_path / "reg.json"
    reg.write_text("[" + ",".join(row % n for n in ("a", "b", "c")) + "]")
    with pytest.warns(LegacyNameWarning) as rec:
        multiplexer.load_registry(str(reg))
    assert len(rec) == 1


def test_env_override_wins_and_legacy_env_warns(monkeypatch):
    monkeypatch.setenv("ANVIL_MODELS_VOLUME", "my-weights")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert multiplexer.default_models_volume(_none) == "my-weights"
    monkeypatch.setenv("ANVIL_MODELS_VOLUME", "fakoli-models")
    with pytest.warns(LegacyNameWarning):
        assert multiplexer.default_models_volume(_none) == "fakoli-models"


def test_registry_file_with_legacy_volume_warns_but_loads(tmp_path):
    reg = tmp_path / "reg.json"
    reg.write_text('[{"name": "m", "model_path": "/models/m", "est_weight_gb": 1, '
                   '"gpu": 0, "port": 1, "volume": "fakoli-models"}]')
    with pytest.warns(LegacyNameWarning):
        rows = multiplexer.load_registry(str(reg))
    assert rows[0]["volume"] == "fakoli-models"
    assert "fakoli-models:/models:ro" in " ".join(multiplexer.docker_run_cmd(rows[0]))


def test_bridge_owner_default_is_companion_without_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        ForwardingBridgeService(ROUTE, serve=lambda *a, **k: None)


def test_bridge_legacy_owner_still_accepted_with_warning():
    with pytest.warns(LegacyNameWarning, match="'companion'"):
        ForwardingBridgeService(ROUTE, owner="mini", serve=lambda *a, **k: None)


def test_bridge_rejects_other_owners_including_other_legacy_names():
    for owner in ("other", "fakoli-models"):
        with pytest.raises(ValueError, match="owner must be"):
            ForwardingBridgeService(ROUTE, owner=owner, serve=lambda *a, **k: None)

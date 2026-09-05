"""Native serve/recipe bindings delegate lifecycle to service_runtime."""

from __future__ import annotations

from anvil_serving import serves


def _service_manifest(tmp_path, *, service="mlx-primary-service", model="mlx-primary"):
    path = tmp_path / "services.toml"
    path.write_text(
        """
schema = "anvil-services/v1"
[[service]]
id = "{service}"
resource = "native-model"
manager = "launchd"
engine = "mlx-lm"
model = "{model}"
label = "dev.anvil.mlx-primary"
owner_uid = 0
definition = "mlx-primary.plist"
definition_sha256 = "{digest}"
memory_mib = 4096
""".strip().format(service=service, model=model, digest="a" * 64),
        encoding="utf-8",
    )
    return path


def _native_manifest(tmp_path, extra=""):
    _service_manifest(tmp_path)
    path = tmp_path / "serves.toml"
    path.write_text(
        """
[[serve]]
name = "mlx-primary"
runtime = "native"
port = 30000
model = "mlx-primary"
engine = "mlx-lm"
service = "mlx-primary-service"
services_manifest = "services.toml"
""".strip() + extra,
        encoding="utf-8",
    )
    return path


def test_native_serve_requires_declared_service_and_refuses_gpu_reservation(tmp_path):
    path = _native_manifest(tmp_path)
    entry = serves.load_manifest(path)[0]
    assert entry["runtime"] == "native"
    assert entry["service"] == "mlx-primary-service"
    assert entry["services_manifest"] == str(tmp_path / "services.toml")

    reserved = _native_manifest(tmp_path, "\ngpu_role = \"gpu-0\"\nvram_mib = 1024\n")
    try:
        serves.load_manifest(reserved)
    except ValueError as exc:
        assert "GPU reservation" in str(exc)
    else:
        raise AssertionError("native service binding accepted a Docker GPU reservation")


def test_native_serve_lifecycle_uses_shared_dispatcher(monkeypatch, tmp_path, capsys):
    entry = serves.load_manifest(_native_manifest(tmp_path))[0]
    calls = []

    def execute(action, service=None, **kwargs):
        calls.append((action, service, kwargs))
        return {"action": action, "service": service, "applied": False, "lines": ["supervisor log"]}

    monkeypatch.setattr(serves, "_service_execute", execute)

    assert serves.cmd_up([entry], [entry["name"]], dry_run=True) == 0
    assert serves.cmd_down([entry], [entry["name"]], dry_run=True) == 0
    assert serves.cmd_status([entry], [entry["name"]]) == 0
    assert serves.cmd_logs([entry], [entry["name"]], tail="7") == 0

    assert [(action, service) for action, service, _ in calls] == [
        ("up", "mlx-primary-service"),
        ("down", "mlx-primary-service"),
        ("status", "mlx-primary-service"),
        ("logs", "mlx-primary-service"),
    ]
    assert all(call[2]["manifest"] == str(tmp_path / "services.toml") for call in calls)
    assert calls[0][2]["dry_run"] is True
    assert calls[1][2]["dry_run"] is True
    assert calls[2][2]["dry_run"] is True
    assert calls[3][2]["tail"] == 7
    assert all(kwargs["expected_model"] == "mlx-primary" for _, _, kwargs in calls)
    assert all(kwargs["expected_engine"] == "mlx-lm" for _, _, kwargs in calls)
    assert "supervisor log" in capsys.readouterr().out


def test_native_recipe_lifecycle_uses_declared_service(monkeypatch, tmp_path):
    from anvil_serving import models

    _service_manifest(tmp_path, model="mlx/model")
    registry = tmp_path / "recipes.toml"
    registry.write_text(
        """
schema = "anvil-serving.serve-recipes/v1"
[[recipe]]
model = "mlx/model"
status = "supported"
[recipe.serve]
runtime = "native"
service = "mlx-primary-service"
services_manifest = "services.toml"
model = "mlx/model"
engine = "mlx-lm"
""".strip(),
        encoding="utf-8",
    )
    calls = []

    def execute(action, service=None, **kwargs):
        calls.append((action, service, kwargs))
        return {"action": action, "service": service, "applied": False, "lines": ["native recipe log"]}

    monkeypatch.setattr(models, "_service_execute", execute)

    assert models._recipe_main(["load", "mlx/model", "--registry", str(registry), "--dry-run"]) == 0
    assert models._recipe_main(["status", "mlx/model", "--registry", str(registry)]) == 0
    assert models._recipe_main(["logs", "mlx/model", "--registry", str(registry), "--tail", "7"]) == 0
    assert models._recipe_main(["unload", "mlx/model", "--registry", str(registry), "--dry-run"]) == 0

    assert [(action, service) for action, service, _ in calls] == [
        ("up", "mlx-primary-service"),
        ("status", "mlx-primary-service"),
        ("logs", "mlx-primary-service"),
        ("down", "mlx-primary-service"),
    ]
    assert all(kwargs["manifest"] == str(tmp_path / "services.toml") for _, _, kwargs in calls)
    assert calls[2][2]["tail"] == 7
    assert all(kwargs["expected_model"] == "mlx/model" for _, _, kwargs in calls)
    assert all(kwargs["expected_engine"] == "mlx-lm" for _, _, kwargs in calls)


def test_native_serve_refuses_router_transition_controls(tmp_path):
    path = _native_manifest(tmp_path, "\nrouter_tier = \"primary\"\n")
    try:
        serves.load_manifest(path)
    except ValueError as exc:
        assert "GPU reservation or exclusive-mode fields" in str(exc)
    else:
        raise AssertionError("native service binding accepted a router transition control")

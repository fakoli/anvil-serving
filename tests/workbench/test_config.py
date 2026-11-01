"""Project-root configuration normalization and rejection coverage."""

from __future__ import annotations

from copy import deepcopy

import pytest

from anvil_serving.workbench_app.config import validate_config


def _project(tmp_path, **extra):
    project = {
        "id": "product",
        "label": "Product",
        "resource_id": "serve-a",
        "checkout": str(tmp_path / "checkout"),
        "anvil_binary": str(tmp_path / "anvil"),
        "runner_root": str(tmp_path / "runners"),
    }
    project.update(extra)
    return project


def _config(tmp_path, **project_extra):
    return {
        "state_path": str(tmp_path / "workbench.sqlite"),
        "projects": [_project(tmp_path, **project_extra)],
    }


def _root(root_id, path, **extra):
    root = {
        "id": root_id,
        "label": root_id.title(),
        "owner_id": "workspace-owner",
        "runtime_id": "workspace-runtime",
        "task_access": "read-write",
        "path": str(path),
    }
    root.update(extra)
    return root


def test_legacy_project_normalizes_to_one_stable_primary_root_idempotently(tmp_path):
    config = _config(tmp_path)
    before = {key: config["projects"][0][key] for key in (
        "id", "resource_id", "checkout", "anvil_binary", "runner_root"
    )}

    normalized = validate_config(config)
    project = normalized["projects"][0]
    assert {key: project[key] for key in before} == before
    assert project["primary_root_id"] == "primary"
    assert project["roots"] == [{
        "id": "primary",
        "label": "Product",
        "owner_id": "local-owner",
        "runtime_id": "local-runtime",
        "task_access": "read-write",
        "path": str(tmp_path / "checkout"),
    }]

    stable = deepcopy(normalized)
    assert validate_config(normalized) == stable


def test_host_pi_optional_private_bridge_fields_are_all_or_nothing(tmp_path):
    config = _config(tmp_path)
    host_pi = {
        "id": "host-pi", "resource_id": "host-pi", "origin": "https://workbench.example.test",
        "version": "0.9.0", "runtime_sha256": "a" * 64,
    }
    config["host_pi"] = host_pi
    assert validate_config(config)["host_pi"] == host_pi  # legacy embedded host Pi

    token_ref = f"file:{tmp_path / 'host-pi.token'}"
    configured = _config(tmp_path)
    configured["host_pi"] = host_pi | {
        "token_ref": token_ref, "parent_origin": "https://workbench.example.test",
        "bridge_base_url": "http://127.0.0.1:30123",
    }
    assert validate_config(configured)["host_pi"]["bridge_base_url"] == "http://127.0.0.1:30123"

    for change in (
        {"token_ref": "HOST_PI_TOKEN"},
        {"token_ref": "bad ref", "parent_origin": "https://workbench.example.test", "bridge_base_url": "http://127.0.0.1:30123"},
        {"token_ref": "file:relative.token", "parent_origin": "https://workbench.example.test", "bridge_base_url": "http://127.0.0.1:30123"},
        {"token_ref": "file:C:relative.token", "parent_origin": "https://workbench.example.test", "bridge_base_url": "http://127.0.0.1:30123"},
        {"token_ref": "file:", "parent_origin": "https://workbench.example.test", "bridge_base_url": "http://127.0.0.1:30123"},
        {"token_ref": "HOST_PI_TOKEN", "parent_origin": "https://workbench.example.test/path", "bridge_base_url": "http://127.0.0.1:30123"},
        {"token_ref": "HOST_PI_TOKEN", "parent_origin": "https://workbench.example.test", "bridge_base_url": "http://127.0.0.1:30123/path"},
        {"token_ref": "HOST_PI_TOKEN", "parent_origin": "https://workbench.example.test", "bridge_base_url": "http://localhost:30123"},
    ):
        invalid = _config(tmp_path)
        invalid["host_pi"] = host_pi | change
        with pytest.raises(ValueError):
            validate_config(invalid)


def test_project_roots_reject_case_aliases_and_share_canonical_scope_bound(tmp_path):
    aliases = _config(tmp_path, primary_root_id="app", roots=[
        _root("app", tmp_path / "checkout"), _root("App", tmp_path / "secondary"),
    ])
    with pytest.raises(ValueError, match="Duplicate"):
        validate_config(aliases)
    root = _root("app", tmp_path / "checkout", expected_files=[f"file-{n}.py" for n in range(256)])
    assert len(validate_config(_config(tmp_path, primary_root_id="app", roots=[root]))["projects"][0]["roots"][0]["expected_files"]) == 256
    root["expected_files"].append("one-too-many.py")
    with pytest.raises(ValueError, match="bounded"):
        validate_config(_config(tmp_path, primary_root_id="app", roots=[root]))

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


def test_explicit_roots_require_checkout_primary_and_disjoint_paths(tmp_path):
    checkout = tmp_path / "checkout"
    secondary = tmp_path / "reference"
    config = _config(
        tmp_path,
        primary_root_id="reference",
        roots=[_root("product", checkout), _root("reference", secondary, task_access="read-only")],
    )

    project = validate_config(config)["projects"][0]
    assert [root["id"] for root in project["roots"]] == ["product", "reference"]
    assert project["primary_root_id"] == "reference"

    conflicting = _config(
        tmp_path,
        primary_root_id="product",
        roots=[_root("product", secondary)],
    )
    duplicate = _config(
        tmp_path,
        primary_root_id="product",
        roots=[_root("product", checkout), _root("product", secondary)],
    )
    overlapping = _config(
        tmp_path,
        primary_root_id="product",
        roots=[_root("product", checkout), _root("nested", checkout / "nested")],
    )
    for candidate in (conflicting, duplicate, overlapping):
        with pytest.raises(ValueError):
            validate_config(candidate)


def test_project_roots_require_one_primary_and_stay_bounded(tmp_path):
    checkout = tmp_path / "checkout"
    missing_primary = _config(tmp_path, roots=[_root("product", checkout)])
    stray_primary = _config(tmp_path, primary_root_id="product")
    too_many = _config(
        tmp_path,
        primary_root_id="product",
        roots=[_root("product", checkout)] + [
            _root(f"root-{index}", tmp_path / f"root-{index}")
            for index in range(16)
        ],
    )

    for candidate in (missing_primary, stray_primary, too_many):
        with pytest.raises(ValueError):
            validate_config(candidate)


@pytest.mark.parametrize(
    "root",
    [
        lambda tmp_path: _root("product", tmp_path / "other"),
        lambda tmp_path: _root("product", tmp_path / "checkout", owner_id="bad owner"),
        lambda tmp_path: _root("product", tmp_path / "checkout", runtime_id="bad runtime"),
        lambda tmp_path: _root("product", tmp_path / "checkout", task_access="context-only"),
        lambda tmp_path: _root("product", "/"),
        lambda tmp_path: _root("product", str(tmp_path / "checkout" / ".." / "escape")),
    ],
)
def test_project_root_rejects_conflicts_or_unsafe_declarations(tmp_path, root):
    config = _config(tmp_path, primary_root_id="product", roots=[root(tmp_path)])

    with pytest.raises(ValueError):
        validate_config(config)

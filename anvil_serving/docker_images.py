"""Bounded Docker image inspection and exact-ID removal.

The public surface deliberately accepts one immutable image identity at a time.
It never exposes prune, tag-only, force, volume, container, or build-cache
semantics.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tomllib

from . import paths


DEFAULT_TIMEOUT_SECONDS = 30
MAX_CONFIG_BYTES = 4 * 1024 * 1024
_FULL_IMAGE_ID_RE = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")
_DIGEST_REFERENCE_RE = re.compile(r"^[^\s@-][^\s@]*@sha256:([0-9a-f]{64})$")
_DOCKER_ID_RE = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")
_IMAGE_KEY_RE = re.compile(r"(^|_)image($|_)")
_YAML_IMAGE_RE = re.compile(
    r"^\s*(?:-\s*)?(?:image|rollback_image|inspector_image)\s*:\s*"
    r"(?P<value>[^#]+?)\s*$",
    re.IGNORECASE,
)
_SIZE_RE = re.compile(r"^(?P<value>[0-9]+(?:\.[0-9]+)?)(?P<unit>[kMGT]?B)$")
_SIZE_FACTORS = {
    "B": 1,
    "kB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
}


class DockerImageCleanupError(ValueError):
    """The requested cleanup cannot be proved safe."""


def normalize_immutable_image_reference(value: str) -> str:
    """Validate one full image ID or repository-qualified sha256 digest."""
    candidate = str(value or "").strip()
    match = _FULL_IMAGE_ID_RE.fullmatch(candidate)
    if match:
        return "sha256:" + match.group(1)
    if _DIGEST_REFERENCE_RE.fullmatch(candidate):
        return candidate
    raise DockerImageCleanupError(
        "image must be one full immutable sha256 image ID or repository@sha256 digest; "
        "tags and abbreviated IDs are refused"
    )


def _docker_run(args, *, runner, timeout=DEFAULT_TIMEOUT_SECONDS):
    try:
        return runner(
            ["docker", *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DockerImageCleanupError("docker command unavailable: %s" % exc) from exc


def _docker_json(args, *, runner, allow_missing=False):
    result = _docker_run(args, runner=runner)
    if result is None:
        raise DockerImageCleanupError("docker command returned no result")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "docker command failed").strip()
        if allow_missing and "no such image" in detail.lower():
            return None
        raise DockerImageCleanupError(detail)
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise DockerImageCleanupError("docker returned invalid JSON") from exc
    return payload


def _normalize_docker_id(value):
    match = _DOCKER_ID_RE.fullmatch(str(value or "").strip())
    if not match:
        raise DockerImageCleanupError("docker returned an invalid immutable image ID")
    return "sha256:" + match.group(1)


def _inspect_one_image(reference, *, runner, allow_missing=False):
    payload = _docker_json(
        ["image", "inspect", reference], runner=runner, allow_missing=allow_missing
    )
    if payload is None:
        return None
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise DockerImageCleanupError("docker image inspect did not resolve exactly one image")
    row = payload[0]
    image_id = _normalize_docker_id(row.get("Id"))
    rootfs = row.get("RootFS") or {}
    layers = rootfs.get("Layers") or []
    if not isinstance(layers, list) or any(not isinstance(item, str) for item in layers):
        raise DockerImageCleanupError("docker image inspect returned invalid layer metadata")
    size = row.get("Size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise DockerImageCleanupError("docker image inspect returned invalid size metadata")
    return {
        "image_id": image_id,
        "repo_tags": sorted(str(item) for item in (row.get("RepoTags") or [])),
        "repo_digests": sorted(str(item) for item in (row.get("RepoDigests") or [])),
        "parent_id": str(row.get("Parent") or ""),
        "layers": list(layers),
        "size_bytes": size,
    }


def _container_references(image_id, *, runner):
    listed = _docker_run(
        ["container", "ls", "--all", "--quiet", "--no-trunc"], runner=runner
    )
    if listed.returncode != 0:
        raise DockerImageCleanupError(
            (listed.stderr or "docker container inventory failed").strip()
        )
    container_ids = sorted(
        {line.strip() for line in (listed.stdout or "").splitlines() if line.strip()}
    )
    if any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in container_ids):
        raise DockerImageCleanupError("docker returned an invalid container ID")
    if not container_ids:
        return []
    rows = _docker_json(["container", "inspect", *container_ids], runner=runner)
    if not isinstance(rows, list) or len(rows) != len(container_ids):
        raise DockerImageCleanupError("docker container inspect returned incomplete inventory")
    references = []
    for row in rows:
        if not isinstance(row, dict):
            raise DockerImageCleanupError("docker container inspect returned invalid metadata")
        referenced = _normalize_docker_id(row.get("Image"))
        if referenced != image_id:
            continue
        state = row.get("State") or {}
        references.append({
            "container_id": str(row.get("Id") or ""),
            "name": str(row.get("Name") or "").lstrip("/"),
            "state": str(state.get("Status") or "unknown"),
        })
    return sorted(references, key=lambda item: (item["name"], item["container_id"]))


def _all_image_rows(*, runner):
    listed = _docker_run(
        ["image", "ls", "--all", "--quiet", "--no-trunc"], runner=runner
    )
    if listed.returncode != 0:
        raise DockerImageCleanupError((listed.stderr or "docker image inventory failed").strip())
    ids = sorted({line.strip() for line in (listed.stdout or "").splitlines() if line.strip()})
    normalized = [_normalize_docker_id(item) for item in ids]
    if not normalized:
        return []
    rows = _docker_json(["image", "inspect", *normalized], runner=runner)
    if not isinstance(rows, list):
        raise DockerImageCleanupError("docker image inventory returned invalid metadata")
    parsed = []
    for row in rows:
        if not isinstance(row, dict):
            raise DockerImageCleanupError("docker image inventory returned invalid metadata")
        rootfs = row.get("RootFS") or {}
        layers = rootfs.get("Layers") or []
        if not isinstance(layers, list):
            raise DockerImageCleanupError("docker image inventory returned invalid layer metadata")
        parsed.append({
            "image_id": _normalize_docker_id(row.get("Id")),
            "parent_id": str(row.get("Parent") or ""),
            "layers": list(layers),
            "repo_tags": sorted(str(item) for item in (row.get("RepoTags") or [])),
            "repo_digests": sorted(str(item) for item in (row.get("RepoDigests") or [])),
        })
    return parsed


def _dependent_images(target, images):
    target_layers = target["layers"]
    result = []
    for row in images:
        if row["image_id"] == target["image_id"]:
            continue
        parent = row["parent_id"]
        normalized_parent = None
        if parent:
            try:
                normalized_parent = _normalize_docker_id(parent)
            except DockerImageCleanupError:
                normalized_parent = None
        layers = row["layers"]
        layer_child = bool(
            target_layers
            and len(layers) > len(target_layers)
            and layers[:len(target_layers)] == target_layers
        )
        if normalized_parent == target["image_id"] or layer_child:
            result.append({
                "image_id": row["image_id"],
                "repo_tags": row["repo_tags"],
                "repo_digests": row["repo_digests"],
            })
    return sorted(result, key=lambda item: item["image_id"])


def _walk_image_values(node, key_path=()):
    if isinstance(node, dict):
        for key, value in node.items():
            path = (*key_path, str(key))
            if isinstance(value, str) and _IMAGE_KEY_RE.search(str(key).lower()):
                yield ".".join(path), value.strip()
            else:
                yield from _walk_image_values(value, path)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_image_values(value, (*key_path, str(index)))


def _yaml_image_values(text):
    for line_number, line in enumerate(text.splitlines(), 1):
        match = _YAML_IMAGE_RE.match(line)
        if not match:
            continue
        value = match.group("value").strip().strip("\"'")
        if value:
            yield "line:%d" % line_number, value


def _path_is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _config_image_values(config_home):
    root = Path(config_home)
    references = []
    errors = []
    if not root.exists():
        return references, errors
    if not root.is_dir() or root.is_symlink():
        return references, ["operator config home is not a plain directory"]
    for current, dirs, filenames in os.walk(root, followlinks=False):
        audited_dirs = []
        for name in sorted(dirs):
            if name == ".git":
                continue
            directory = Path(current) / name
            relative = directory.relative_to(root).as_posix()
            try:
                if _path_is_link_like(directory):
                    errors.append("%s: symlinked directory is not audited" % relative)
                    continue
            except OSError as exc:
                errors.append("%s: %s" % (relative, exc))
                continue
            audited_dirs.append(name)
        dirs[:] = audited_dirs
        for filename in sorted(filenames):
            path = Path(current) / filename
            suffix = path.suffix.lower()
            if suffix not in {".toml", ".json", ".yaml", ".yml"}:
                continue
            relative = path.relative_to(root).as_posix()
            try:
                if path.is_symlink():
                    raise OSError("symlink is not audited")
                size = path.stat().st_size
                if size > MAX_CONFIG_BYTES:
                    raise OSError("file exceeds %d bytes" % MAX_CONFIG_BYTES)
                raw = path.read_bytes()
                if suffix == ".toml":
                    values = list(_walk_image_values(tomllib.loads(raw.decode("utf-8"))))
                elif suffix == ".json":
                    values = list(_walk_image_values(json.loads(raw.decode("utf-8"))))
                else:
                    values = list(_yaml_image_values(raw.decode("utf-8")))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
                errors.append("%s: %s" % (relative, exc))
                continue
            for field, value in values:
                references.append({"path": relative, "field": field, "value": value})
    return references, errors


def _configured_references(target, *, config_home):
    references, errors = _config_image_values(config_home)
    identities = {
        target["image_id"],
        target["image_id"].removeprefix("sha256:"),
        *target["repo_tags"],
        *target["repo_digests"],
    }
    digest_tokens = {item.rsplit("@", 1)[-1] for item in target["repo_digests"]}
    digest_tokens.add(target["image_id"])
    matched = []
    for reference in references:
        value = reference["value"]
        first_token = value.split(None, 1)[0] if value else value
        if value in identities or first_token in identities or any(
            token and token in value for token in digest_tokens
        ):
            matched.append(reference)
    return matched, errors


def _parse_size(value):
    match = _SIZE_RE.fullmatch(str(value or "").strip())
    if not match:
        raise DockerImageCleanupError("docker system df returned an invalid size")
    return int(float(match.group("value")) * _SIZE_FACTORS[match.group("unit")])


def _reclaim_estimate(image_id, *, runner):
    result = _docker_run(["system", "df", "--verbose"], runner=runner)
    if result.returncode != 0:
        raise DockerImageCleanupError((result.stderr or "docker system df failed").strip())
    short_id = image_id.removeprefix("sha256:")[:12]
    in_images = False
    for line in (result.stdout or "").splitlines():
        stripped = line.strip()
        if stripped == "Images space usage:":
            in_images = True
            continue
        if in_images and stripped == "Containers space usage:":
            break
        if not in_images or short_id not in line:
            continue
        columns = re.split(r"\s{2,}", stripped)
        if len(columns) < 8 or columns[2] != short_id:
            continue
        return {
            "estimated_reclaimable_bytes": _parse_size(columns[-2]),
            "estimate_source": "docker system df --verbose unique size",
            "estimate_display_value": columns[-2],
        }
    raise DockerImageCleanupError("target image is absent from docker system df inventory")


def inspect_docker_image_removal(image, *, config_home=None, runner=subprocess.run):
    """Return the complete fail-closed audit for one immutable Docker image."""
    immutable = normalize_immutable_image_reference(image)
    target = _inspect_one_image(immutable, runner=runner)
    images = _all_image_rows(runner=runner)
    containers = _container_references(target["image_id"], runner=runner)
    home = os.path.abspath(os.path.expanduser(config_home or paths.config_home()))
    configured, config_errors = _configured_references(target, config_home=home)
    children = _dependent_images(target, images)
    estimate = _reclaim_estimate(target["image_id"], runner=runner)
    blockers = []
    if containers:
        blockers.append("referenced by running or stopped container")
    if configured:
        blockers.append("referenced by declared recipe, manifest, or rollback configuration")
    if children:
        blockers.append("required by dependent child image")
    if config_errors:
        blockers.append("operator configuration audit was incomplete")
    fingerprint = {
        key: target[key]
        for key in ("image_id", "repo_tags", "repo_digests", "parent_id", "layers", "size_bytes")
    }
    return {
        "schema": "docker-image-removal/v1",
        "requested_identity": immutable,
        "target": target,
        "fingerprint": fingerprint,
        "operator_config_home": home,
        "references": {
            "containers": containers,
            "configured": configured,
            "dependent_images": children,
            "config_audit_errors": config_errors,
        },
        **estimate,
        "blocked_reasons": blockers,
        "removable": not blockers,
        "recovery": "rebuild or pull the same immutable image again",
    }


def remove_docker_image(
    image, *, confirm=False, dry_run=False, config_home=None, runner=subprocess.run,
):
    """Preview or remove one twice-verified immutable Docker image."""
    first = inspect_docker_image_removal(image, config_home=config_home, runner=runner)
    result = {
        "schema": first["schema"],
        "applied": False,
        "dry_run": bool(dry_run),
        "outcome": "blocked" if first["blocked_reasons"] else "preview",
        "inspection": first,
    }
    if first["blocked_reasons"] or dry_run or not confirm:
        return result
    second = inspect_docker_image_removal(image, config_home=config_home, runner=runner)
    result["verification"] = second
    if second["blocked_reasons"]:
        result["outcome"] = "blocked"
        return result
    if second["fingerprint"] != first["fingerprint"]:
        result["outcome"] = "identity-drift"
        return result
    removed = _docker_run(
        ["image", "rm", "--no-prune", first["target"]["image_id"]],
        runner=runner,
        timeout=120,
    )
    if removed.returncode != 0:
        result.update({
            "outcome": "failed",
            "error": (removed.stderr or removed.stdout or "docker image removal failed").strip(),
        })
        return result
    try:
        final = _inspect_one_image(
            first["target"]["image_id"], runner=runner, allow_missing=True
        )
    except DockerImageCleanupError as exc:
        result.update({
            "outcome": "failed",
            "removal_attempted": True,
            "error": "post-removal verification failed: %s" % exc,
        })
        return result
    if final is not None:
        result.update({
            "outcome": "failed",
            "error": "Docker still resolves the removed immutable image ID",
            "final_target": final,
        })
        return result
    result.update({
        "applied": True,
        "outcome": "removed",
        "removed_image_id": first["target"]["image_id"],
        "reclaimed_bytes_estimate": first["estimated_reclaimable_bytes"],
        "recovery": first["recovery"],
    })
    return result

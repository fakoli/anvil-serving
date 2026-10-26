"""Guarded, non-serving preparation for the pinned Flare qualification package."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid

from . import paths, recipe_memory, serve_recipes
from .service_runtime.contracts import ServiceError
from .service_runtime.operations import _lock as _service_lock

MODEL = "local-inference-lab/Qwen3.8-Flash-Next-FLARE"
REPO_FOLDER = "models--local-inference-lab--Qwen3.8-Flash-Next-FLARE"
REVISION = "431b302155d53e296aa51633a997b4b5fd92e751"
FILENAME = "qwen3.8-flash-next-tp1-c16-mtp5.safetensors"
IMAGE = (
    "ghcr.io/local-inference-lab/flare:release-2193455-k7-pf4096-20261022"
    "@sha256:67124437a73011d63c2b25c939b9c48581eba90e51c6e565d4c808d161d10fc6"
)
PUBLISHER_NAMESPACE = "local-inference-lab"
PUBLISHER_FINGERPRINT = "937c0143f6856d3ee8ad164f62e3d804bd825d7a8750d0accb9a5849f703a8c1"
HOME_VOLUME = "qwen38-flash-next-flare-home"
HOME_TARGET = "/home/flare"
RAM_BYTES = 8 * 1024**3
HOST_RESERVE_MIB = 16 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
_OWNERSHIP_LABEL = "anvil-serving.flare-package-preparation"
_OPERATION_LABEL = "anvil-serving.flare-package-preparation-operation"
_STEP_LABEL = "anvil-serving.flare-package-preparation-step"
_DOWNLOAD_TIMEOUT_SECONDS = 6 * 60 * 60
_INSPECT_TIMEOUT_SECONDS = 180
_CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_PATH_RE = re.compile(
    r"^/home/flare/\.cache/huggingface/hub/"
    + re.escape(REPO_FOLDER)
    + r"/(?:snapshots/"
    + REVISION
    + r"/"
    + re.escape(FILENAME)
    + r"|flare-packages/"
    + REVISION
    + r"/"
    + re.escape(FILENAME)
    + r"/"
    + re.escape(FILENAME)
    + r")$"
)


class PreparationError(ValueError):
    """A bounded preparation refusal or execution failure."""


def _recipe_from_registry(path: str):
    try:
        payload = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        recipe = serve_recipes.find_recipe(payload, MODEL)
    except (OSError, tomllib.TOMLDecodeError, serve_recipes.RecipeError) as exc:
        raise PreparationError("cannot read the selected serve-recipe registry") from exc
    if recipe is None:
        raise PreparationError("the pinned Flare candidate is absent from the selected registry")
    try:
        serve_recipes.validate_recipe(recipe, require_loadable=True)
    except serve_recipes.RecipeError as exc:
        raise PreparationError("the pinned Flare recipe is invalid") from exc
    return recipe


def _publisher_pin(recipe: dict) -> tuple[str, str]:
    tokens = _recipe_flag_tokens(recipe)
    revisions = []
    publisher = fingerprint = None
    for index, token in enumerate(tokens):
        if token == "--revision":
            revisions.append(tokens[index + 1] if index + 1 < len(tokens) else None)
        elif token.startswith("--revision="):
            revisions.append(token.partition("=")[2])
        elif token == "--dangerously-approve-key":
            fingerprint = tokens[index + 1] if index + 1 < len(tokens) else None
        elif token.startswith("--dangerously-approve-key="):
            fingerprint = token.partition("=")[2]
        elif token == "--dangerously-accept-publisher":
            publisher = tokens[index + 1] if index + 1 < len(tokens) else None
        elif token.startswith("--dangerously-accept-publisher="):
            publisher = token.partition("=")[2]
    if revisions != [REVISION]:
        raise PreparationError("recipe must select the pinned Flare revision exactly once")
    if publisher != PUBLISHER_NAMESPACE or fingerprint != PUBLISHER_FINGERPRINT:
        raise PreparationError("recipe publisher approval pins do not match the qualification identity")
    return publisher, fingerprint


def _recipe_flag_tokens(recipe: dict) -> list[str]:
    flags = (recipe.get("serve") or {}).get("flags")
    if not isinstance(flags, list) or any(not isinstance(flag, str) for flag in flags):
        raise PreparationError("recipe serving flags are invalid")
    tokens = []
    for flag in flags:
        try:
            row = shlex.split(flag)
        except ValueError as exc:
            raise PreparationError("recipe serving flags cannot be parsed safely") from exc
        if (not row or len(row) > 2 or not row[0].startswith("--")
                or any("\x00" in token for token in row)
                or (len(row) == 2 and row[1].startswith("-"))):
            raise PreparationError("recipe serving flags cannot add positional loader arguments")
        name = row[0].split("=", 1)[0]
        if name in {"--model", "--model-path", "--model-env", "--filename", "--package",
                    "--repository", "--repo", "--cache-dir", "--cache-path"}:
            raise PreparationError("recipe serving flags override the pinned Flare package selection")
        tokens.extend(row)
    return tokens


def _paths_overlap(first: str, second: str) -> bool:
    try:
        return os.path.commonpath((first, second)) in {first, second}
    except ValueError:
        return False


def validate_recipe_identity(recipe: dict) -> dict:
    """Refuse recipe drift before any Docker interaction."""
    download = recipe.get("download") or {}
    serve = recipe.get("serve") or {}
    if recipe.get("model") != MODEL:
        raise PreparationError("recipe model does not match the pinned Flare repository")
    if download.get("repo") != MODEL or download.get("revision") != REVISION:
        raise PreparationError("recipe repository and revision must match the immutable qualification pin")
    if serve.get("engine") != "flare-2193455651794642" or serve.get("image") != IMAGE:
        raise PreparationError("recipe engine and image must match the immutable Flare pins")
    if serve.get("entrypoint") != ["/usr/local/bin/flare", "run"]:
        raise PreparationError("recipe loader must use the pinned native Flare run entrypoint")
    if any(serve.get(key) is not None for key in ("model_path", "model_flag", "model_env")):
        raise PreparationError("recipe must use its default model selection without loader overrides")
    cache_mount = download.get("cache_mount", "/root/.cache/huggingface")
    if not isinstance(cache_mount, str) or _paths_overlap(cache_mount, HOME_TARGET):
        raise PreparationError("recipe download cache path overlaps the persistent Flare home")
    volumes = serve.get("named_volumes")
    if not isinstance(volumes, list):
        raise PreparationError("recipe has no persistent Flare home volume")
    home_mounts = []
    for spec in volumes:
        try:
            volume_name, target, read_only = serve_recipes._parse_named_volume(spec)
        except (serve_recipes.RecipeError, TypeError) as exc:
            raise PreparationError("recipe contains an invalid named volume") from exc
        if _paths_overlap(target, HOME_TARGET):
            if target != HOME_TARGET or read_only:
                raise PreparationError("recipe has an overlapping or read-only Flare home mount")
            home_mounts.append(volume_name)
    if home_mounts != [HOME_VOLUME]:
        raise PreparationError("recipe must mount the exact persistent Flare home volume")
    tokens = _recipe_flag_tokens(recipe)
    _publisher_pin(recipe)
    # Ensure the same managed loader builder sees the candidate repository as its sole default model.
    try:
        load_argv = serve_recipes.docker_run_argv(recipe)
    except serve_recipes.RecipeError as exc:
        raise PreparationError("recipe does not produce a valid managed Flare loader command") from exc
    image_index = load_argv.index(IMAGE)
    if load_argv[image_index + 1:image_index + 3] != ["run", MODEL]:
        raise PreparationError("managed loader does not select the pinned repository by default")
    if load_argv[image_index + 3:] != tokens:
        raise PreparationError("managed loader flags differ from the pinned recipe flags")
    return {
        "model": MODEL,
        "revision": REVISION,
        "filename": FILENAME,
        "image": IMAGE,
        "persistent_home_volume": HOME_VOLUME,
        "publisher_namespace": PUBLISHER_NAMESPACE,
        "publisher_fingerprint": PUBLISHER_FINGERPRINT,
        "recipe_digest": serve_recipes.recipe_digest(recipe),
    }


def build_container_argv(
    action: str,
    *,
    operation_id: str,
    step: str,
    cidfile: str,
    package_path: str | None = None,
    publisher_profile_path: str | None = None,
) -> list[str]:
    """Build a create argv; all Docker options precede the immutable image reference."""
    if action not in {"download", "inspect"} or step not in {"download", "inspect"}:
        raise PreparationError("unsupported Flare preparation action")
    if not re.fullmatch(r"[0-9a-f]{32}", operation_id):
        raise PreparationError("managed preparation operation identity is invalid")
    if not os.path.isabs(cidfile) or "," in cidfile:
        raise PreparationError("managed container ID file path is invalid")
    if action == "inspect" and (not isinstance(package_path, str) or not _safe_package_path(package_path)):
        raise PreparationError("download returned a package path outside the pinned Flare cache")
    if publisher_profile_path is not None and (
        not os.path.isabs(publisher_profile_path) or "," in publisher_profile_path
    ):
        raise PreparationError("public publisher profile mount path is invalid")
    network = "bridge" if action == "download" else "none"
    home_mount = f"type=volume,source={HOME_VOLUME},target={HOME_TARGET}"
    if action == "inspect":
        home_mount += ",readonly"
    argv = [
        "docker", "create", "--cidfile", cidfile,
        "--label", f"{_OWNERSHIP_LABEL}=v1",
        "--label", f"{_OPERATION_LABEL}={operation_id}",
        "--label", f"{_STEP_LABEL}={step}",
        "--memory", str(RAM_BYTES), "--memory-swap", str(RAM_BYTES),
        "--pids-limit", "256", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--runtime", "runc",
        "--network", network, "--mount", home_mount,
    ]
    if publisher_profile_path is not None:
        argv.extend([
            "--mount",
            f"type=bind,source={publisher_profile_path},target=/run/anvil-flare-publishers.json,readonly",
        ])
    argv.extend(["--entrypoint", "/usr/local/bin/flare", IMAGE])
    if action == "download":
        argv.extend(["download", MODEL, "--revision", REVISION, "--filename", FILENAME])
    else:
        argv.extend(["inspect", "--json"])
        if publisher_profile_path is not None:
            argv.extend(["--publisher-keys", "/run/anvil-flare-publishers.json"])
        argv.append(package_path)
    return argv


def _safe_package_path(value: str) -> bool:
    return len(value) <= 4096 and bool(_PATH_RE.fullmatch(value))


def _validated_publisher_profile(path: str | None) -> bytes | None:
    if path is None:
        return None
    try:
        profile_path = Path(path).expanduser().resolve(strict=True)
        if not profile_path.is_file() or profile_path.stat().st_size > 65536:
            raise PreparationError("public publisher profile must be a regular file no larger than 64 KiB")
        with profile_path.open("rb") as handle:
            data = handle.read(65537)
        if len(data) > 65536:
            raise PreparationError("public publisher profile exceeds 64 KiB")
        profiles = json.loads(data)
    except PreparationError:
        raise
    except (OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreparationError("cannot read the supplied public publisher profile") from exc
    if not isinstance(profiles, list) or not profiles:
        raise PreparationError("public publisher profile is not a non-empty Flare publisher list")
    matched = False
    for profile in profiles:
        if not isinstance(profile, dict) or set(profile) - {"name", "namespace", "public_key", "development"}:
            raise PreparationError("public publisher profile has an unsupported record")
        name, namespace, key = profile.get("name"), profile.get("namespace"), profile.get("public_key")
        development = profile.get("development", False)
        if (not isinstance(name, str) or not name or len(name) > 256
                or any(ord(character) < 32 or ord(character) == 127 for character in name)
                or not isinstance(namespace, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", namespace)
                or not isinstance(key, list) or len(key) != 32
                or any(type(byte) is not int or not 0 <= byte <= 255 for byte in key)
                or type(development) is not bool):
            raise PreparationError("public publisher profile contains an invalid identity")
        fingerprint = hashlib.sha256(bytes(key)).hexdigest()
        if namespace == PUBLISHER_NAMESPACE and fingerprint == PUBLISHER_FINGERPRINT and not development:
            matched = True
    if not matched:
        raise PreparationError("public publisher profile does not contain the exact pinned namespace and fingerprint")
    return data


def _run_bounded(argv: list[str], *, timeout: int, runner=subprocess.run) -> tuple[int, bytes, bytes]:
    """Run without a shell and cap retained stdout/stderr before parsing or displaying."""
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            result = runner(argv, stdout=stdout_file, stderr=stderr_file, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PreparationError("bounded Docker operation failed or timed out") from exc
        if result is None or not isinstance(getattr(result, "returncode", None), int):
            raise PreparationError("Docker returned no bounded operation result")
        stdout_file.seek(0, os.SEEK_END)
        stdout_size = stdout_file.tell()
        stderr_file.seek(0, os.SEEK_END)
        stderr_size = stderr_file.tell()
        if stdout_size > MAX_OUTPUT_BYTES or stderr_size > MAX_OUTPUT_BYTES:
            raise PreparationError("Docker operation exceeded the bounded output limit")
        stdout_file.seek(0)
        stderr_file.seek(0)
        return result.returncode, stdout_file.read(), stderr_file.read()


def _read_cidfile(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise PreparationError("Docker container ID custody file is unreadable") from exc
    if not _CONTAINER_ID_RE.fullmatch(value):
        raise PreparationError("Docker container ID custody file is invalid")
    return value


def _inspect_projection(
    container_id: str, operation_id: str, step: str, *,
    publisher_profile_mounted: bool = False, runner=subprocess.run,
) -> dict:
    code, output, _stderr = _run_bounded(
        ["docker", "inspect", "--type", "container", "--format", "{{json .}}", container_id],
        timeout=30, runner=runner,
    )
    if code:
        raise PreparationError("cannot verify managed Flare helper container ownership")
    try:
        row = json.loads(output)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreparationError("Docker returned invalid helper container inspection JSON") from exc
    if not isinstance(row, dict) or row.get("Id") != container_id:
        raise PreparationError("Docker helper container identity does not match its custody ID")
    config = row.get("Config")
    host_config = row.get("HostConfig")
    state = row.get("State")
    if not isinstance(config, dict) or not isinstance(host_config, dict) or not isinstance(state, dict):
        raise PreparationError("Docker helper inspection is missing required runtime sections")
    labels = config.get("Labels")
    if not isinstance(labels, dict):
        raise PreparationError("Docker helper ownership labels are missing")
    if (labels.get(_OWNERSHIP_LABEL) != "v1"
            or labels.get(_OPERATION_LABEL) != operation_id
            or labels.get(_STEP_LABEL) != step):
        raise PreparationError("Docker helper container labels do not prove operation ownership")
    if config.get("Image") != IMAGE:
        raise PreparationError("Docker helper container image reference differs from the pinned digest")
    image_id = row.get("Image")
    if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise PreparationError("Docker helper image identity is not a full immutable ID")
    expected_network = "bridge" if step == "download" else "none"
    if (host_config.get("Memory") != RAM_BYTES or host_config.get("MemorySwap") != RAM_BYTES
            or host_config.get("Runtime") != "runc"
            or host_config.get("NetworkMode") != expected_network
            or host_config.get("DeviceRequests") not in (None, [])
            or host_config.get("Devices") not in (None, [])):
        raise PreparationError("Docker helper resource binding differs from the no-GPU preparation plan")
    mounts = row.get("Mounts")
    if not isinstance(mounts, list):
        raise PreparationError("Docker helper mount inspection is incomplete")
    home_mounts = [
        mount for mount in mounts
        if isinstance(mount, dict) and mount.get("Destination") == HOME_TARGET
    ]
    if (len(home_mounts) != 1 or home_mounts[0].get("Name") != HOME_VOLUME
            or home_mounts[0].get("RW") is not (step == "download")):
        raise PreparationError("Docker helper Flare home mount does not match the cache preservation plan")
    other_mounts = [mount for mount in mounts if mount not in home_mounts]
    if publisher_profile_mounted:
        if (len(other_mounts) != 1 or other_mounts[0].get("Destination") != "/run/anvil-flare-publishers.json"
                or other_mounts[0].get("RW") is not False):
            raise PreparationError("Docker helper publisher profile mount is not read-only and exact")
    elif other_mounts:
        raise PreparationError("Docker helper has an undeclared mount")
    return {
        "helper_image_id": image_id,
        "memory_limit_bytes": host_config["Memory"],
        "ram_plus_swap_limit_bytes": host_config["MemorySwap"],
        "runtime": host_config["Runtime"],
        "gpu_devices": "none",
        "network": expected_network,
        "flare_home_mount_read_only": not home_mounts[0]["RW"],
        "observed_running": state.get("Running") is True,
        "container_state": state.get("Status"),
        "exit_code": state.get("ExitCode"),
    }


def _find_owned_container(operation_id: str, step: str, *, runner=subprocess.run) -> str | None:
    code, output, _stderr = _run_bounded(
        [
            "docker", "container", "ls", "--all", "--quiet", "--no-trunc",
            "--filter", f"label={_OWNERSHIP_LABEL}=v1",
            "--filter", f"label={_OPERATION_LABEL}={operation_id}",
            "--filter", f"label={_STEP_LABEL}={step}",
        ],
        timeout=30, runner=runner,
    )
    if code:
        raise PreparationError("cannot reconcile a timed-out managed container by ownership labels")
    try:
        identifiers = [line.strip() for line in output.decode("ascii").splitlines() if line.strip()]
    except UnicodeDecodeError as exc:
        raise PreparationError("Docker returned non-ASCII ownership-filtered container IDs") from exc
    if any(not _CONTAINER_ID_RE.fullmatch(identifier) for identifier in identifiers):
        raise PreparationError("Docker returned an invalid ownership-filtered container ID")
    if len(identifiers) > 1:
        raise PreparationError("multiple containers claim one preparation operation identity")
    return identifiers[0] if identifiers else None


def _refuse_preexisting_helpers(*, runner=subprocess.run) -> None:
    """Do not admit a new cache writer while an earlier labeled helper may remain."""
    code, output, _stderr = _run_bounded(
        [
            "docker", "container", "ls", "--all", "--quiet", "--no-trunc",
            "--filter", f"label={_OWNERSHIP_LABEL}=v1",
        ],
        timeout=30,
        runner=runner,
    )
    if code:
        raise PreparationError("cannot check for an earlier Flare package preparation helper")
    try:
        identifiers = [line.strip() for line in output.decode("ascii").splitlines() if line.strip()]
    except UnicodeDecodeError as exc:
        raise PreparationError("Docker returned non-ASCII prior helper IDs") from exc
    if any(not _CONTAINER_ID_RE.fullmatch(identifier) for identifier in identifiers):
        raise PreparationError("Docker returned an invalid prior Flare helper ID")
    if identifiers:
        raise PreparationError(
            "an earlier labeled Flare preparation helper remains; reconcile it before retrying"
        )


def _wait_for_owned_container(operation_id: str, step: str, *, runner=subprocess.run,
                              clock=time.monotonic, sleep=time.sleep) -> str | None:
    """Reconcile create timeouts briefly so a late daemon response cannot orphan a cache writer."""
    deadline = clock() + 5
    while True:
        container_id = _find_owned_container(operation_id, step, runner=runner)
        if container_id is not None or clock() >= deadline:
            return container_id
        sleep(0.1)


def _remove_owned_container(
    container_id: str, operation_id: str, step: str, *,
    publisher_profile_mounted: bool = False, runner=subprocess.run,
) -> None:
    _inspect_projection(
        container_id, operation_id, step,
        publisher_profile_mounted=publisher_profile_mounted, runner=runner,
    )
    code, _stdout, stderr = _run_bounded(
        ["docker", "rm", "--force", container_id], timeout=30, runner=runner
    )
    if code:
        raise PreparationError("owned Flare preparation container could not be removed safely")


@contextmanager
def _preparation_lock(lock_path: Path | None = None):
    path = lock_path or Path(paths.config_path("locks", "flare-package-prepare-" + HOME_VOLUME))
    try:
        with _service_lock(path):
            yield
    except ServiceError as exc:
        raise PreparationError("another Flare package preparation holds the shared-home lock") from exc


def _execute_container(
    action: str,
    operation_id: str,
    *,
    package_path: str | None = None,
    publisher_data: bytes | None = None,
    runner=subprocess.run,
    clock=time.monotonic,
    sleep=time.sleep,
) -> tuple[bytes, dict]:
    step = action
    cid = None
    profile_mounted = publisher_data is not None
    with tempfile.TemporaryDirectory(prefix="anvil-flare-prepare-") as temporary_directory:
        temporary_path = Path(temporary_directory)
        cidfile = temporary_path / "container-id"
        profile_path = None
        if publisher_data is not None:
            profile_path = temporary_path / "publisher.json"
            profile_path.write_bytes(publisher_data)
            profile_path.chmod(0o644)
        argv = build_container_argv(
            action, operation_id=operation_id, step=step, cidfile=str(cidfile),
            package_path=package_path,
            publisher_profile_path=str(profile_path) if profile_path is not None else None,
        )
        try:
            code, create_output, _create_error = _run_bounded(argv, timeout=180, runner=runner)
            cid = _read_cidfile(cidfile)
            try:
                created_id = create_output.decode("ascii").strip()
            except UnicodeDecodeError as exc:
                raise PreparationError("Docker returned an invalid created-container ID") from exc
            if (code or cid is None or not _CONTAINER_ID_RE.fullmatch(created_id)
                    or created_id != cid):
                raise PreparationError("Docker did not create one ID-matched Flare preparation container")
            initial = _inspect_projection(
                cid, operation_id, step,
                publisher_profile_mounted=profile_mounted, runner=runner,
            )
            start_code, _started, _start_error = _run_bounded(
                ["docker", "start", cid], timeout=30, runner=runner
            )
            if start_code:
                raise PreparationError("managed Flare preparation container failed to start")
            deadline = clock() + (_DOWNLOAD_TIMEOUT_SECONDS if action == "download" else _INSPECT_TIMEOUT_SECONDS)
            observed_running = False
            projection = initial
            while clock() < deadline:
                projection = _inspect_projection(
                    cid, operation_id, step,
                    publisher_profile_mounted=profile_mounted, runner=runner,
                )
                observed_running = observed_running or projection["observed_running"]
                if projection["container_state"] in {"exited", "dead"}:
                    break
                if projection["container_state"] not in {"created", "running"}:
                    raise PreparationError("managed Flare preparation container entered an unknown state")
                sleep(0.25)
            else:
                raise PreparationError("managed Flare preparation container exceeded its time bound")
            code, logs, _log_error = _run_bounded(
                ["docker", "logs", "--tail", "10000", cid], timeout=30, runner=runner
            )
            if code:
                raise PreparationError("cannot retrieve bounded Flare preparation output")
            if projection["exit_code"] != 0:
                if action == "inspect":
                    detail = "\n".join(
                        stream.decode("utf-8", errors="replace")
                        for stream in (logs, _log_error)
                    )
                    match = re.search(
                        r"cannot verify signing key [0-9a-f]{64}: no embedded, remembered, or --publisher-keys profile names it \(publisher [A-Za-z0-9_.-]+ at https://[^\s)]+\)",
                        detail,
                    )
                    if match:
                        raise PreparationError(match.group(0))
                raise PreparationError("managed Flare preparation did not complete successfully under observed bindings")
            return logs, projection
        finally:
            if cid is None:
                cid = _read_cidfile(cidfile)
            if cid is None:
                cid = _wait_for_owned_container(operation_id, step, runner=runner,
                                                clock=clock, sleep=sleep)
            if cid is not None:
                _remove_owned_container(
                    cid, operation_id, step,
                    publisher_profile_mounted=profile_mounted,
                    runner=runner,
                )


def _docker_pull(image: str, *, runner=subprocess.run):
    code, _stdout, _stderr = _run_bounded(["docker", "pull", image], timeout=1800, runner=runner)
    if code:
        raise PreparationError("pinned Flare image acquisition failed")


def _package_path(output: bytes) -> str:
    try:
        candidates = [line.strip() for line in output.decode("utf-8").splitlines() if _safe_package_path(line.strip())]
    except UnicodeDecodeError as exc:
        raise PreparationError("Flare download returned invalid bounded output") from exc
    if len(candidates) != 1:
        raise PreparationError("Flare download did not return one exact pinned package path")
    return candidates[0]


def _inspect_document(output: bytes) -> dict:
    try:
        text = output.decode("utf-8")
        start = text.index("{")
        document, _end = json.JSONDecoder().raw_decode(text[start:])
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise PreparationError("Flare inspect returned invalid bounded JSON") from exc
    if not isinstance(document, dict):
        raise PreparationError("Flare inspect returned an invalid package document")
    return document


def _inspect_receipt(output: bytes, *, package_path: str, identity: dict, helper: dict,
                     host_admission: dict) -> dict:
    document = _inspect_document(output)
    package = document.get("package")
    publisher = package.get("publisher") if isinstance(package, dict) else None
    if not isinstance(package, dict) or not isinstance(publisher, dict):
        raise PreparationError("Flare inspect did not return a verified package identity")
    if package.get("signatures_verified") is not True:
        raise PreparationError("Flare inspect did not verify the signed package index")
    if (publisher.get("namespace") != identity["publisher_namespace"]
            or publisher.get("fingerprint") != identity["publisher_fingerprint"]):
        raise PreparationError("verified package identity does not match the pinned publisher")
    if package.get("official_source") != f"https://huggingface.co/{MODEL}":
        raise PreparationError("verified package source does not match the pinned repository")
    bytes_count = package.get("bytes")
    if type(bytes_count) is not int or bytes_count <= 0:
        raise PreparationError("verified package identity has invalid primary byte count")
    companions = package.get("companions")
    if not isinstance(companions, list):
        raise PreparationError("verified package identity has invalid companion inventory")
    return {
        "schema": "anvil-serving.flare-package-preparation/v1",
        **identity,
        "package_path": package_path,
        "publisher": {"namespace": identity["publisher_namespace"],
                      "fingerprint": identity["publisher_fingerprint"]},
        "signed_index_verification": "verified_by_flare_inspect",
        "primary_bytes": bytes_count,
        "companions": companions,
        "payload_digest_coverage": "not_checked_by_flare_inspect",
        "loader_validation": "not_run",
        "cache_mutation": "downloaded_into_existing_persistent_flare_home; no cache deletion",
        "host_admission": host_admission,
        "helper_binding": helper,
    }


def prepare(recipe: dict, *, dry_run: bool, confirm: bool, publisher_profile: str | None = None,
            runner=subprocess.run, memory_check=recipe_memory.check_host,
            lock_path: Path | None = None, clock=time.monotonic, sleep=time.sleep) -> dict:
    identity = validate_recipe_identity(recipe)
    profile_data = _validated_publisher_profile(publisher_profile)
    if profile_data is not None and "," in tempfile.gettempdir():
        raise PreparationError("temporary profile mount path cannot be represented safely by Docker")
    admission_plan = {
        "decision": "not_run_dry_run" if dry_run else "pending_confirmation",
        "ram_limit_mib": 8 * 1024,
        "ram_plus_swap_limit_mib": 8 * 1024,
        "host_memory_reserve_mib": HOST_RESERVE_MIB,
    }
    plan = {
        "plan": "flare-package-prepare/v1",
        **identity,
        "container_resources": {
            "ram_limit_bytes": RAM_BYTES,
            "ram_plus_swap_limit_bytes": RAM_BYTES,
            "runtime": "runc",
            "gpu_devices": "none",
            "host_memory_reserve_mib": HOST_RESERVE_MIB,
        },
        "host_admission": admission_plan,
        "steps": [
            ["docker", "pull", IMAGE],
            ["docker", "create", "<bounded-no-GPU-download-container>", IMAGE,
             "download", MODEL, "--revision", REVISION, "--filename", FILENAME],
            ["docker", "create", "<read-only-network-none-inspect-container>", IMAGE,
             "inspect", "--json", "<exact-revision-package-path>"],
        ],
        "publisher_profile": (
            "provided_and_fingerprint_checked" if profile_data is not None
            else "native_image_or_persistent_trust_store"
        ),
        "dry_run": dry_run,
    }
    if dry_run:
        return plan
    if not confirm:
        raise PreparationError("preparation changes the persistent package cache; pass --confirm or --dry-run")
    with _preparation_lock(lock_path):
        _refuse_preexisting_helpers(runner=runner)
        try:
            memory_check({
                "memory_limit_mib": 8 * 1024,
                "memory_swap_limit_mib": 8 * 1024,
                "host_memory_reserve_mib": HOST_RESERVE_MIB,
            })
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            raise PreparationError("host memory admission refused bounded Flare preparation") from exc
        host_admission = {**admission_plan, "decision": "passed", "check": "recipe_memory.check_host"}
        _docker_pull(IMAGE, runner=runner)
        operation_id = uuid.uuid4().hex
        download_output, download_binding = _execute_container(
            "download", operation_id, runner=runner, clock=clock, sleep=sleep,
        )
        path = _package_path(download_output)
        inspect_output, inspect_binding = _execute_container(
            "inspect", operation_id, package_path=path, publisher_data=profile_data,
            runner=runner, clock=clock, sleep=sleep,
        )
        return _inspect_receipt(
            inspect_output, package_path=path, identity=identity,
            helper={"download": download_binding, "inspect": inspect_binding},
            host_admission=host_admission,
        )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="anvil-serving models recipes prepare",
        description="Download and verify the pinned Flare package without starting a serving process.",
    )
    parser.add_argument("model", help="exact qualification recipe model ID")
    parser.add_argument("--registry", required=True, help="serve-recipe registry containing the pinned candidate")
    parser.add_argument(
        "--publisher-keys",
        help=("optional public Flare publisher.json profile; must contain the recipe's exact "
              "namespace and fingerprint"),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="validate identity and print the offline plan")
    mode.add_argument("--confirm", action="store_true", help="download into the persistent Flare package cache")
    args = parser.parse_args(argv)
    if args.model != MODEL:
        parser.error("prepare is limited to the exact pinned Flare qualification recipe")
    try:
        recipe = _recipe_from_registry(args.registry)
        receipt = prepare(
            recipe, dry_run=args.dry_run, confirm=args.confirm,
            publisher_profile=args.publisher_keys,
        )
    except (PreparationError, serve_recipes.RecipeError) as exc:
        print(f"Flare package preparation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, indent=2, sort_keys=True))
    if not args.dry_run and args.confirm:
        return 0
    return 0 if args.dry_run else 3

"""serve_recipes.py — shared GENERATE + READ helpers for serve recipes.

A *serve recipe* is a reproducible record of HOW to serve a model on our hardware:
the exact `docker run` (engine / image / env / flags / quant / port), the hardware
it ran on, the MEASURED numbers, and the workloads it fits. The registry
(`configs/serve-recipes.toml`) is a list of `[[recipe]]` tables.

Stdlib-only, on purpose:
  * READ    via `tomllib` (the stdlib TOML reader) — `load_registry` / `find_recipe`.
  * WRITE   by HAND — `tomllib` has NO writer, so `format_recipe` / `append_recipe`
            emit `[[recipe]]` blocks as text. Round-trip-safe: the text parses back
            through `tomllib` to the same dict.
  * CAPTURE from a running serve via `subprocess` (`docker inspect`, `nvidia-smi`),
            behind an injectable `_run` seam so tests never touch real docker / nvidia.

This module is imported by BOTH the GENERATE path (`benchmark --recipe-out`, which
records a recipe as a side effect of benchmarking a serve) and the READ path
(`models recipes list|show`, which replays a recorded recipe). No new CLI verb.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
import tomllib

from . import network_policy

# Env-var prefixes that are part of a reproducible serve (not incidental docker noise).
_SERVE_ENV_PREFIXES = ("VLLM_", "FLASHINFER_", "CUDA_")

# Where model weights are mounted in the container (a named volume, never a C:/ bind
# mount — see the cold-load gotchas). Reconstructed commands reuse this exactly.
HFCACHE_MOUNT = "-v vllm-hfcache:/root/.cache/huggingface"
REGISTRY_SCHEMA = "anvil-serving.serve-recipes/v1"
_CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class RecipeError(ValueError):
    """A recipe or recipe registry is invalid for a requested operation."""


def _model_network_policy(serve):
    """Normalize a recipe policy while keeping every model workload offline."""
    try:
        policy, reason = network_policy.normalize_policy(
            serve, context="recipe.serve"
        )
    except network_policy.NetworkPolicyError as exc:
        raise RecipeError(str(exc)) from None
    if policy != "deny":
        raise RecipeError(
            "model recipes cannot declare network_egress='allow'; outbound access "
            "belongs only to capability, media, or voice gateway infrastructure"
        )
    return policy, reason


def _parse_named_volume(spec: str) -> tuple[str, str, bool]:
    """Validate and split one portable ``NAME:/absolute/target[:ro]`` mount."""
    if not isinstance(spec, str) or any(ch in spec for ch in ("\n", "\r", "\x00")):
        raise RecipeError(
            "recipe.serve.named_volumes entries must be NAME:/absolute/target[:ro]"
        )
    if re.match(r"^[A-Za-z]:[\\/]", spec):
        raise RecipeError(
            "recipe.serve.named_volumes sources must be named Docker volumes"
        )
    parts = spec.split(":")
    if len(parts) not in {2, 3} or (len(parts) == 3 and parts[2] != "ro"):
        raise RecipeError(
            "recipe.serve.named_volumes entries must be NAME:/absolute/target[:ro]"
        )
    source, target = parts[:2]
    if not _CONTAINER_NAME_RE.fullmatch(source):
        raise RecipeError(
            "recipe.serve.named_volumes sources must be named Docker volumes"
        )
    target_parts = target.split("/")
    if (
        not target.startswith("/")
        or "\\" in target
        or any(ch.isspace() for ch in target)
        or any(not part or part in {".", ".."} for part in target_parts[1:])
    ):
        raise RecipeError(
            "recipe.serve.named_volumes targets must be normalized absolute POSIX paths"
        )
    return source, target, len(parts) == 3


# --------------------------------------------------------------------------- #
# READ — parse the registry (tomllib) and look up a recipe.
# --------------------------------------------------------------------------- #
def load_registry(path) -> dict:
    """Parse a serve-recipe registry file (read-only, via `tomllib`)."""
    with open(path, "rb") as f:
        registry = tomllib.load(f)
    validate_registry(registry)
    return registry


def validate_registry(registry: dict) -> None:
    """Validate the bounded registry envelope before any selector walks it."""
    if not isinstance(registry, dict):
        raise RecipeError("serve-recipe registry must be a TOML document")
    schema = registry.get("schema")
    if schema is not None and (not isinstance(schema, str) or not schema.strip()):
        raise RecipeError("serve-recipe registry schema must be a non-empty string")
    recipes = registry.get("recipe", [])
    if not isinstance(recipes, list) or not all(isinstance(item, dict) for item in recipes):
        raise RecipeError("registry.recipe must be an array of recipe tables")
    for recipe in recipes:
        validate_recipe(recipe)


def registry_digest(path) -> str | None:
    """Return a streaming digest for state-drift detection, or None if absent."""
    try:
        handle = open(path, "rb")
    except FileNotFoundError:
        return None
    digest = hashlib.sha256()
    with handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def registry_lock(path):
    """Hold a non-blocking cross-platform sidecar lock for one registry mutation."""
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    lock_path = os.path.join(parent, ".serve-recipes.lock")
    handle = open(lock_path, "a+b")
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RecipeError(
                    "serve-recipe registry is being modified by another process"
                ) from exc
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RecipeError(
                    "serve-recipe registry is being modified by another process"
                ) from exc
        locked = True
        yield
    finally:
        if locked:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def find_recipe(registry: dict, model: str) -> dict | None:
    """Return the recipe for `model`, matched by exact `model` first, then basename.

    So `"gpt-oss-120b"` matches a recorded `"openai/gpt-oss-120b"`, and vice-versa.
    """
    index = find_recipe_index(registry, model)
    if index is None:
        return None
    return registry["recipe"][index]


def find_recipe_index(registry: dict, model: str) -> int | None:
    """Return the unique recipe index selected by exact id or unambiguous basename.

    A basename is convenient for operators, but silently selecting the first of two
    matching models is unsafe for create/update/delete/load.  Exact duplicate model
    ids and ambiguous basenames therefore fail closed with a clear error.
    """
    recipes = registry.get("recipe") or []
    exact = [i for i, recipe in enumerate(recipes) if recipe.get("model") == model]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise RecipeError("registry has duplicate model id %r" % model)
    basename = model.rsplit("/", 1)[-1]
    matches = [
        i for i, recipe in enumerate(recipes)
        if str(recipe.get("model", "")).rsplit("/", 1)[-1] == basename
    ]
    if len(matches) <= 1:
        return matches[0] if matches else None
    choices = ", ".join(str(recipes[i].get("model", "")) for i in matches)
    raise RecipeError("recipe selector %r is ambiguous; use one of: %s" % (model, choices))


# --------------------------------------------------------------------------- #
# WRITE — hand-format a recipe dict as a `[[recipe]]` TOML block (round-trip safe).
# `tomllib` is read-only, so we emit TOML text ourselves.
# --------------------------------------------------------------------------- #
_TOML_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _toml_string(s: str) -> str:
    """Encode a Python str as a TOML basic string (with the mandatory escapes)."""
    out = []
    for ch in s:
        if ch in _TOML_ESCAPES:
            out.append(_TOML_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append("\\u%04X" % ord(ch))
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _toml_scalar(v) -> str:
    """Encode a single TOML scalar. bool BEFORE int (bool is an int subclass)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)  # always contains '.'/'e' -> parses back as a TOML float
    if isinstance(v, str):
        return _toml_string(v)
    raise RecipeError("unsupported TOML scalar type: %s" % type(v).__name__)


def _toml_key(key) -> str:
    if not isinstance(key, str) or not key:
        raise RecipeError("TOML keys must be non-empty strings")
    return key if _BARE_KEY_RE.fullmatch(key) else _toml_string(key)


def _emit_kv(key: str, value, lines: list[str], indent: str) -> None:
    if isinstance(value, list):
        if len(value) <= 1:
            inner = ", ".join(_toml_scalar(x) for x in value)
            lines.append("%s%s = [%s]" % (indent, _toml_key(key), inner))
        else:
            lines.append("%s%s = [" % (indent, _toml_key(key)))
            for x in value:
                lines.append("%s  %s," % (indent, _toml_scalar(x)))
            lines.append("%s]" % indent)
    else:
        lines.append("%s%s = %s" % (indent, _toml_key(key), _toml_scalar(value)))


def _emit_table(table: dict, header_path: str, lines: list[str], indent: str) -> None:
    """Emit a table's scalar/array keys, then recurse into sub-tables.

    The header for `header_path` is emitted by the caller; sub-tables are opened
    with the dotted path `header_path.<key>` and one extra level of indent.
    """
    for k, v in table.items():
        if not isinstance(v, dict):
            _emit_kv(k, v, lines, indent)
    for k, v in table.items():
        if isinstance(v, dict):
            sub_indent = indent + "  "
            component = _toml_key(k)
            lines.append("")
            lines.append("%s[%s.%s]" % (sub_indent, header_path, component))
            _emit_table(v, "%s.%s" % (header_path, component), lines, sub_indent)


def format_recipe(recipe: dict) -> str:
    """Render a recipe dict as a `[[recipe]]` TOML block (trailing newline included).

    Round-trip safe: `tomllib.loads("schema='x'\\n" + format_recipe(r))["recipe"][0]`
    equals `r`.
    """
    lines = ["[[recipe]]"]
    _emit_table(recipe, "recipe", lines, "")
    return "\n".join(lines) + "\n"


def append_recipe(path, recipe: dict) -> None:
    """Append a blank line + the formatted `[[recipe]]` block to the registry file."""
    with registry_lock(path):
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n" + format_recipe(recipe))


def load_recipe_file(path) -> dict:
    """Read one ``[[recipe]]`` TOML block from ``path`` for create/update.

    Requiring exactly one recipe makes the mutation target explicit and keeps a
    hand-authored or benchmark-derived recipe file unambiguous before editing.
    """
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RecipeError("cannot read recipe file %s: %s" % (path, exc)) from exc
    recipes = data.get("recipe")
    if not isinstance(recipes, list) or len(recipes) != 1 or not isinstance(recipes[0], dict):
        raise RecipeError("recipe file %s must contain exactly one [[recipe]] table" % path)
    return recipes[0]


def validate_recipe(recipe: dict, *, require_loadable: bool = False) -> None:
    """Validate the stable recipe contract without inventing model-specific flags."""
    if not isinstance(recipe, dict):
        raise RecipeError("recipe must be a TOML table")
    model = recipe.get("model")
    if not isinstance(model, str) or not model.strip():
        raise RecipeError("recipe.model must be a non-empty string")
    for key in (
        "serve", "hardware", "measured", "fit", "download", "sources",
        "bakeoff", "activation",
    ):
        if key in recipe and not isinstance(recipe[key], dict):
            raise RecipeError("recipe.%s must be a table" % key)
    serve = recipe.get("serve") or {}
    if "args" in serve:
        raise RecipeError(
            "recipe.serve.args is unsupported; use recipe.serve.flags as an array of strings"
        )
    _model_network_policy(serve)
    activation = recipe.get("activation") or {}
    for role, config in activation.items():
        if not isinstance(role, str) or not role.strip():
            raise RecipeError("recipe.activation role names must be non-empty strings")
        if not isinstance(config, dict):
            raise RecipeError("recipe.activation.%s must be a table" % role)
        plan = config.get("plan")
        if not isinstance(plan, str) or not plan.strip():
            raise RecipeError("recipe.activation.%s.plan must be a non-empty string" % role)
        direction = config.get("direction")
        if direction not in {"promote", "rollback"}:
            raise RecipeError(
                "recipe.activation.%s.direction must be 'promote' or 'rollback'" % role
            )
        compose_service = config.get("compose_service")
        if not isinstance(compose_service, str) or not compose_service.strip():
            raise RecipeError(
                "recipe.activation.%s.compose_service must be a non-empty string" % role
            )
    if not require_loadable:
        return
    serve = recipe.get("serve")
    if not isinstance(serve, dict):
        raise RecipeError("recipe.serve is required to load a recipe")
    image = serve.get("image")
    if not isinstance(image, str) or not image.strip():
        raise RecipeError("recipe.serve.image is required to load a recipe")
    if image.lstrip().startswith("-") or "\x00" in image:
        raise RecipeError("recipe.serve.image must be a Docker image reference, not an option")
    model_path = serve.get("model_path")
    if model_path is not None:
        path_parts = model_path.split("/") if isinstance(model_path, str) else []
        if (
            not isinstance(model_path, str)
            or not model_path.startswith("/")
            or "\\" in model_path
            or any(ch in model_path for ch in ("\n", "\r", "\x00"))
            or any(ch.isspace() for ch in model_path)
            or any(not part or part in {".", ".."} for part in path_parts[1:])
        ):
            raise RecipeError(
                "recipe.serve.model_path must be a normalized absolute POSIX path"
            )
        if serve.get("model_env"):
            raise RecipeError(
                "recipe.serve.model_path is incompatible with recipe.serve.model_env"
            )
    if "\x00" in model:
        raise RecipeError("recipe.model must not contain NUL bytes")
    port = serve.get("port")
    if port is not None and (isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535):
        raise RecipeError("recipe.serve.port must be an integer from 1 to 65535")
    startup_timeout = serve.get("startup_timeout_seconds")
    if startup_timeout is not None and (
        isinstance(startup_timeout, bool)
        or not isinstance(startup_timeout, int)
        or not 1 <= startup_timeout <= 86400
    ):
        raise RecipeError(
            "recipe.serve.startup_timeout_seconds must be an integer from 1 to 86400"
        )
    download = recipe.get("download") or {}
    require_complete_cache = download.get("require_complete_cache")
    if require_complete_cache is not None and not isinstance(
        require_complete_cache, bool
    ):
        raise RecipeError(
            "recipe.download.require_complete_cache must be a boolean"
        )
    if require_complete_cache:
        for key in ("repo", "revision"):
            value = download.get(key)
            if not isinstance(value, str) or not value or value.startswith("-"):
                raise RecipeError(
                    "recipe.download.%s is required for complete-cache loads" % key
                )
        volume = download.get("volume", "vllm-hfcache")
        if (
            not isinstance(volume, str)
            or not volume
            or volume.startswith("-")
            or any(separator in volume for separator in ("/", "\\", ":"))
        ):
            raise RecipeError(
                "recipe.download.volume must be a named Docker volume for complete-cache loads"
            )
    for key in ("env", "flags"):
        value = serve.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise RecipeError("recipe.serve.%s must be an array of strings" % key)
    entrypoint = serve.get("entrypoint")
    if entrypoint is not None:
        if (
            not isinstance(entrypoint, list)
            or not entrypoint
            or not all(isinstance(item, str) and item and "\n" not in item and "\r" not in item and "\x00" not in item for item in entrypoint)
            or entrypoint[0].startswith("-")
        ):
            raise RecipeError(
                "recipe.serve.entrypoint must be a non-empty argv array with an executable first item"
            )
    model_flag = serve.get("model_flag")
    if model_flag is not None:
        if (
            entrypoint is None
            or not isinstance(model_flag, str)
            or not model_flag.startswith("-")
            or any(ch.isspace() for ch in model_flag)
            or "\x00" in model_flag
        ):
            raise RecipeError(
                "recipe.serve.model_flag must be a single option and requires recipe.serve.entrypoint"
            )
    model_env = serve.get("model_env")
    if model_env is not None and (
        entrypoint is None
        or model_flag is not None
        or not isinstance(model_env, str)
        or not _ENV_KEY_RE.fullmatch(model_env)
    ):
        raise RecipeError(
            "recipe.serve.model_env must be an environment variable name, requires "
            "recipe.serve.entrypoint, and cannot be combined with model_flag"
        )
    named_volumes = serve.get("named_volumes", [])
    if not isinstance(named_volumes, list):
        raise RecipeError("recipe.serve.named_volumes must be an array of strings")
    parsed_volumes = [_parse_named_volume(spec) for spec in named_volumes]
    sources = [source for source, _, _ in parsed_volumes]
    targets = [target for _, target, _ in parsed_volumes]
    if len(sources) != len(set(sources)) or len(targets) != len(set(targets)):
        raise RecipeError(
            "recipe.serve.named_volumes must not repeat a source or target"
        )
    if "/root/.cache/huggingface" in targets:
        raise RecipeError(
            "recipe.serve.named_volumes cannot shadow the managed model-cache mount"
        )
    ipc = serve.get("ipc")
    if ipc is not None and ipc not in {"host", "none", "private", "shareable"}:
        raise RecipeError("recipe.serve.ipc must be one of host, none, private, or shareable")
    shm_size = serve.get("shm_size")
    if (
        shm_size is not None
        and (
            not isinstance(shm_size, str)
            or not re.fullmatch(r"[1-9][0-9]*(?:[kKmMgGtT][bB]?)?", shm_size)
        )
    ):
        raise RecipeError("recipe.serve.shm_size must be a positive Docker size such as 16gb")
    ulimits = serve.get("ulimits", [])
    if (
        not isinstance(ulimits, list)
        or not all(isinstance(item, str) and item and "\n" not in item and "\r" not in item and "\x00" not in item for item in ulimits)
    ):
        raise RecipeError("recipe.serve.ulimits must be an array of non-empty Docker ulimit strings")


def format_registry(registry: dict) -> str:
    """Render a mutable registry while preserving every supported data value.

    Registry rewrites are intentionally limited to root scalar/array metadata plus
    ``[[recipe]]`` records.  Refusing unknown root tables is safer than silently
    discarding operator-owned TOML that this stdlib-only writer cannot preserve.
    """
    if not isinstance(registry, dict):
        raise RecipeError("registry must be a TOML table")
    recipes = registry.get("recipe", [])
    if not isinstance(recipes, list) or not all(isinstance(recipe, dict) for recipe in recipes):
        raise RecipeError("registry.recipe must be an array of recipe tables")
    lines: list[str] = []
    for key, value in registry.items():
        if key == "recipe":
            continue
        if isinstance(value, dict):
            raise RecipeError("cannot rewrite registry with unsupported root table %r" % key)
        _emit_kv(key, value, lines, "")
    if not lines:
        lines.append("schema = %s" % _toml_string(REGISTRY_SCHEMA))
    for recipe in recipes:
        validate_recipe(recipe)
        lines.append("")
        lines.extend(format_recipe(recipe).rstrip("\n").splitlines())
    return "\n".join(lines) + "\n"


def write_registry(path, registry: dict) -> None:
    """Atomically rewrite a registry after its caller has made an explicit backup."""
    rendered = format_registry(registry)
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".serve-recipes-", suffix=".toml", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def create_recipe(registry: dict, recipe: dict) -> dict:
    """Return a new registry with ``recipe`` appended, rejecting duplicate ids."""
    validate_recipe(recipe)
    if find_recipe_index(registry, recipe["model"]) is not None:
        raise RecipeError("a recipe for %r already exists; use update" % recipe["model"])
    result = dict(registry)
    result["recipe"] = [*list(registry.get("recipe") or []), dict(recipe)]
    result.setdefault("schema", REGISTRY_SCHEMA)
    return result


def update_recipe(registry: dict, selector: str, replacement: dict) -> tuple[dict, dict]:
    """Return ``(updated_registry, previous_recipe)`` for one selected recipe."""
    validate_recipe(replacement)
    index = find_recipe_index(registry, selector)
    if index is None:
        raise RecipeError("no serve recipe for %r" % selector)
    existing = list(registry.get("recipe") or [])
    duplicate = find_recipe_index(registry, replacement["model"])
    if duplicate is not None and duplicate != index:
        raise RecipeError("a recipe for %r already exists" % replacement["model"])
    previous = existing[index]
    existing[index] = dict(replacement)
    result = dict(registry)
    result["recipe"] = existing
    return result, previous


def delete_recipe(registry: dict, selector: str) -> tuple[dict, dict]:
    """Return ``(updated_registry, deleted_recipe)`` for one selected recipe."""
    index = find_recipe_index(registry, selector)
    if index is None:
        raise RecipeError("no serve recipe for %r" % selector)
    recipes = list(registry.get("recipe") or [])
    deleted = recipes.pop(index)
    result = dict(registry)
    result["recipe"] = recipes
    return result, deleted


# --------------------------------------------------------------------------- #
# READ (reproduce) — rebuild the exact `docker run` from a recorded recipe.
# --------------------------------------------------------------------------- #
def docker_run_argv(
    recipe: dict,
    *,
    container: str | None = None,
    gpu_device: str | None = None,
) -> list[str]:
    """Build the argv for a loopback-bound recipe load without shell interpolation."""
    validate_recipe(recipe, require_loadable=True)
    if container is not None and not _CONTAINER_NAME_RE.fullmatch(container):
        raise RecipeError("container name must use only letters, digits, '.', '_', or '-'")
    if gpu_device is not None and (
        not isinstance(gpu_device, str)
        or not gpu_device
        or gpu_device.startswith("-")
        or "\x00" in gpu_device
        or any(character.isspace() for character in gpu_device)
        or any(not item or item.startswith("-") for item in gpu_device.split(","))
        or len(set(gpu_device.split(","))) != len(gpu_device.split(","))
    ):
        raise RecipeError(
            "gpu_device must be one or more distinct comma-separated UUIDs or "
            "indices without whitespace"
        )
    serve = recipe["serve"]
    hw = recipe.get("hardware") or {}
    argv = ["docker", "run", "-d"]
    if container:
        argv += ["--name", container]
    gpu_uuid = gpu_device or hw.get("gpu_uuid")
    if gpu_uuid is not None and (not isinstance(gpu_uuid, str) or "\x00" in gpu_uuid):
        raise RecipeError("recipe.hardware.gpu_uuid must be a string without NUL bytes")
    if gpu_uuid:
        gpu_request = "device=%s" % gpu_uuid
        # Docker's --gpus option parses a CSV capability request. A multi-device
        # value therefore needs literal inner quotes even when subprocess passes
        # argv directly; shell-only quotes are stripped too early.
        if "," in gpu_uuid:
            gpu_request = '"%s"' % gpu_request
    else:
        gpu_request = "all"
    argv += ["--gpus", gpu_request]
    argv += [
        "--label",
        "io.anvil-serving.managed-by=models-recipes",
        "--label",
        "io.anvil-serving.recipe.model=%s" % recipe["model"],
    ]
    egress_policy, _egress_reason = _model_network_policy(serve)
    argv += [
        "--label",
        "%s=%s" % (network_policy.EGRESS_LABEL, egress_policy),
    ]
    argv += ["--network", network_policy.MODEL_EGRESS_DENY_NETWORK]
    revision = (recipe.get("download") or {}).get("revision")
    if revision:
        argv += [
            "--label",
            "io.anvil-serving.recipe.revision=%s" % revision,
        ]
    declared_serve_env = list(serve.get("env", []))
    model_env = serve.get("model_env")
    if model_env:
        if any(env.startswith(model_env + "=") for env in declared_serve_env):
            raise RecipeError(
                "recipe.serve.env must not override recipe.serve.model_env %s"
                % model_env
            )
        declared_serve_env.append("%s=%s" % (model_env, recipe["model"]))
    download = recipe.get("download") or {}
    if download.get("require_complete_cache") is True:
        for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
            matches = [env for env in declared_serve_env if env.startswith(name + "=")]
            if matches and matches != [name + "=1"]:
                raise RecipeError(
                    "recipe.serve.env %s must be 1 for complete-cache loads" % name
                )
            if not matches:
                declared_serve_env.append(name + "=1")
    declared_visible_devices = [
        env.split("=", 1)[1]
        for env in declared_serve_env
        if isinstance(env, str) and env.startswith("CUDA_VISIBLE_DEVICES=")
    ]
    selected_devices = gpu_uuid.split(",") if gpu_uuid else []
    visible_device_indices = (
        declared_visible_devices[0].split(",")
        if len(declared_visible_devices) == 1
        else []
    )
    valid_index_pin = (
        serve.get("allow_cuda_visible_devices_index") is True
        and bool(visible_device_indices)
        and all(item.isdigit() for item in visible_device_indices)
        and len(set(visible_device_indices)) == len(visible_device_indices)
    )
    allow_index_pin = valid_index_pin and (
        (bool(selected_devices) and len(visible_device_indices) == len(selected_devices))
        # Recipe inspection reconstructs the portable container-relative
        # declaration before a host GPU selection exists. A real load supplies
        # a container name and must still provide an exact matching device set.
        or (not selected_devices and container is None)
    )
    if (
        serve.get("allow_cuda_visible_devices_index") is True
        and declared_visible_devices
        and not allow_index_pin
    ):
        raise RecipeError(
            "recipe.serve.env CUDA_VISIBLE_DEVICES numeric index count must match "
            "the selected GPU count"
        )
    serve_env = [
        env
        for env in declared_serve_env
        if gpu_device is None
        or allow_index_pin
        or not env.startswith("CUDA_VISIBLE_DEVICES=")
    ]
    visible_devices = [
        env.split("=", 1)[1]
        for env in serve_env
        if isinstance(env, str) and env.startswith("CUDA_VISIBLE_DEVICES=")
    ]
    if gpu_uuid and visible_devices and visible_devices != [gpu_uuid] and not allow_index_pin:
        raise RecipeError(
            "recipe.serve.env CUDA_VISIBLE_DEVICES must match recipe.hardware.gpu_uuid"
        )
    if gpu_uuid and not visible_devices:
        serve_env.insert(0, "CUDA_VISIBLE_DEVICES=%s" % gpu_uuid)
    for env in serve_env:
        if not _ENV_NAME_RE.match(env) or "\n" in env or "\r" in env or "\x00" in env:
            raise RecipeError("recipe.serve.env entries must be NAME=value without newlines")
        argv += ["-e", env]
    cache_volume = download.get("volume", "vllm-hfcache")
    argv += ["-v", "%s:/root/.cache/huggingface" % cache_volume]
    for spec in serve.get("named_volumes", []):
        source, target, read_only = _parse_named_volume(spec)
        mount = "type=volume,source=%s,target=%s" % (source, target)
        if read_only:
            mount += ",readonly"
        argv += ["--mount", mount]
    port = serve.get("port")
    if port is not None:
        argv += ["-p", "127.0.0.1:%s:%s" % (port, port)]
    if serve.get("ipc"):
        argv += ["--ipc", serve["ipc"]]
    if serve.get("shm_size"):
        argv += ["--shm-size", serve["shm_size"]]
    for ulimit in serve.get("ulimits", []):
        argv += ["--ulimit", ulimit]
    entrypoint = serve.get("entrypoint")
    if entrypoint:
        argv += ["--entrypoint", entrypoint[0]]
    argv += [serve["image"]]
    if entrypoint:
        argv += entrypoint[1:]
    model_flag = serve.get("model_flag")
    if model_flag:
        argv.append(model_flag)
    if not model_env:
        argv.append(serve.get("model_path", recipe["model"]))
    for flag in serve.get("flags", []):
        try:
            tokens = shlex.split(flag)
        except ValueError as exc:
            raise RecipeError("cannot parse recipe.serve.flags entry %r: %s" % (flag, exc)) from exc
        if any("\x00" in token for token in tokens):
            raise RecipeError("recipe.serve.flags entries must not contain NUL bytes")
        argv.extend(tokens)
    return argv


def reconstruct_docker_run(recipe: dict) -> str:
    """Reconstruct the reproducible `docker run` for a recipe.

    The default image ENTRYPOINT is `vllm serve`, so the model is positional after
    the image. ``serve.model_path`` can select an immutable container-side snapshot
    while ``recipe.model`` remains the stable registry identity. Recipes can instead
    supply ``serve.entrypoint`` with ``model_flag`` for images that require
    ``--model MODEL``, or ``model_env`` for launchers whose environment selects the
    model and which must receive no model argument.
    """
    return shlex.join(docker_run_argv(recipe))


def uses_native_kv_offload(recipe: dict) -> bool:
    """Whether this recipe positively requests a non-zero native KV offload region."""
    serve = recipe.get("serve") or {}
    for item in serve.get("env", []):
        if not isinstance(item, str) or "=" not in item:
            continue
        key, value = item.split("=", 1)
        if key == "KV_OFFLOADING_SIZE":
            try:
                if float(value) > 0:
                    return True
            except ValueError:
                continue
    tokens = []
    for flag in serve.get("flags", []):
        try:
            tokens.extend(shlex.split(flag))
        except (TypeError, ValueError):
            continue
    for index, token in enumerate(tokens):
        if token.startswith("--kv-offloading-size="):
            value = token.split("=", 1)[1]
        elif token == "--kv-offloading-size" and index + 1 < len(tokens):
            value = tokens[index + 1]
        else:
            continue
        try:
            if float(value) > 0:
                return True
        except ValueError:
            continue
    return False


def load_recipe(
    recipe: dict,
    container: str,
    *,
    gpu_device: str | None = None,
    _run=subprocess.run,
) -> tuple[list[str], int]:
    """Start a named recipe container once and return its exact argv and exit code."""
    argv = docker_run_argv(recipe, container=container, gpu_device=gpu_device)
    _model_network_policy(recipe.get("serve") or {})
    try:
        network_policy.ensure_model_egress_network(_run=_run)
    except network_policy.NetworkPolicyError as exc:
        raise RecipeError(str(exc)) from None
    try:
        completed = _run(argv, check=False)
    except OSError:
        return argv, 127
    return argv, int(getattr(completed, "returncode", completed))


# --------------------------------------------------------------------------- #
# CAPTURE (generate) — read a live serve's config back off docker / nvidia-smi.
# `_run` is injected so tests exercise this against FAKE JSON, never real docker.
# --------------------------------------------------------------------------- #
def _engine_from_image(image: str) -> str | None:
    low = (image or "").lower()
    if "sglang" in low:
        return "sglang"
    if "vllm" in low:
        return "vllm"
    return None


def _group_flags(tokens: list[str]) -> list[str]:
    """Group `[--flag, value]` token pairs into `"--flag value"` strings.

    Bare boolean flags (`--enable-auto-tool-choice`) and `--k=v` forms pass through.
    This makes a captured recipe read like the hand-authored registry rows.
    """
    grouped: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if (
            isinstance(tok, str)
            and tok.startswith("-")
            and "=" not in tok
            and i + 1 < n
            and isinstance(tokens[i + 1], str)
            and not tokens[i + 1].startswith("-")
        ):
            grouped.append("%s %s" % (tok, tokens[i + 1]))
            i += 2
        else:
            grouped.append(tok)
            i += 1
    return grouped


def _gpu_uuid_from_inspect(inspect: dict, env_map: dict) -> str | None:
    host = inspect.get("HostConfig") or {}
    for req in host.get("DeviceRequests") or []:
        for dev in (req or {}).get("DeviceIDs") or []:
            if isinstance(dev, str) and dev:
                return dev
    cvd = env_map.get("CUDA_VISIBLE_DEVICES")
    if cvd:
        return cvd.split(",")[0].strip() or None
    return None


def _port_from_inspect(inspect: dict) -> int | None:
    host = inspect.get("HostConfig") or {}
    for key in (host.get("PortBindings") or {}):
        try:
            return int(str(key).split("/")[0])
        except (ValueError, TypeError):
            continue
    return None


def capture_from_container(name, *, _run=subprocess.run) -> dict:
    """Capture a live serve's reproducible config from `docker inspect <name>`.

    Returns `{"serve": {engine,image,env,flags,port}, "hardware": {gpu_uuid}}` —
    the reproducible half of a recipe. `_run` is injected for hermetic tests.
    """
    proc = _run(
        ["docker", "inspect", name],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(proc.stdout)
    inspect = data[0] if isinstance(data, list) else data
    config = inspect.get("Config") or {}

    env_map: dict = {}
    for entry in config.get("Env") or []:
        if isinstance(entry, str) and "=" in entry:
            k, v = entry.split("=", 1)
            env_map[k] = v
    serve_env = [
        "%s=%s" % (k, env_map[k])
        for k in env_map
        if k.startswith(_SERVE_ENV_PREFIXES)
    ]

    # Args = [maybe 'serve'] [maybe MODEL positional] --flag val --flag val ...
    args = [a for a in (inspect.get("Args") or []) if isinstance(a, str)]
    raw_entrypoint = config.get("Entrypoint")
    if isinstance(raw_entrypoint, str):
        entrypoint = [raw_entrypoint]
    elif isinstance(raw_entrypoint, list) and all(isinstance(item, str) for item in raw_entrypoint):
        entrypoint = raw_entrypoint
    else:
        entrypoint = []
    model_flag = None
    if entrypoint and entrypoint != ["vllm"] and len(args) >= 2 and args[0] == "--model":
        model_flag = "--model"
        args = args[2:]
    first_flag = next((i for i, t in enumerate(args) if t.startswith("-")), len(args))
    flags = _group_flags(args[first_flag:])

    serve: dict = {}
    engine = _engine_from_image(config.get("Image") or "")
    if engine:
        serve["engine"] = engine
    if config.get("Image"):
        serve["image"] = config["Image"]
    if entrypoint and entrypoint != ["vllm"]:
        serve["entrypoint"] = entrypoint
    if model_flag:
        serve["model_flag"] = model_flag
    port = _port_from_inspect(inspect)
    if port is not None:
        serve["port"] = port
    if serve_env:
        serve["env"] = serve_env
    if flags:
        serve["flags"] = flags
    # Capture describes the next managed launch, not permission to preserve a
    # legacy container's network. Every reconstructed model recipe is tightened
    # to the platform's egress-denied network.
    serve["network_egress"] = "deny"

    hardware: dict = {}
    gpu_uuid = _gpu_uuid_from_inspect(inspect, env_map)
    if gpu_uuid:
        hardware["gpu_uuid"] = gpu_uuid

    return {"serve": serve, "hardware": hardware}


def _parse_mib(text) -> float | None:
    try:
        return float(str(text).strip().split()[0])
    except (ValueError, IndexError):
        return None


def capture_hardware(gpu_uuid: str | None = None, *, _run=subprocess.run) -> dict:
    """Capture `{gpu, vram_total_gb}` from `nvidia-smi` (the row matching `gpu_uuid`).

    `_run` is injected for hermetic tests. Returns `{}` if nothing matches.
    """
    proc = _run(
        ["nvidia-smi", "--query-gpu=name,memory.total,uuid", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        check=True,
    )
    chosen = None
    for line in proc.stdout.splitlines():
        cols = [c.strip() for c in line.split(",")]
        if len(cols) < 3 or not cols[0]:
            continue
        row = {"gpu": cols[0], "mem": cols[1], "uuid": cols[2]}
        if gpu_uuid is None:
            chosen = row
            break
        if row["uuid"] == gpu_uuid:
            chosen = row
            break
    if chosen is None:
        return {}
    result = {"gpu": chosen["gpu"]}
    mib = _parse_mib(chosen["mem"])
    if mib:
        result["vram_total_gb"] = round(mib / 1024)
    return result

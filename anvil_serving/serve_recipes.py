"""serve_recipes.py — shared GENERATE + READ helpers for serve recipes.

A *serve recipe* is a reproducible record of HOW to serve a model on our hardware:
the exact `docker run` (engine / image / env / flags / quant / port), the hardware
it ran on, the MEASURED numbers, and the intent it is best suited for. The registry
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
(`models recipe list|show`, which replays a recorded recipe). No new CLI verb.
"""
from __future__ import annotations

import json
import subprocess
import tomllib

# Env-var prefixes that are part of a reproducible serve (not incidental docker noise).
_SERVE_ENV_PREFIXES = ("VLLM_", "FLASHINFER_", "CUDA_")

# Where model weights are mounted in the container (a named volume, never a C:/ bind
# mount — see the cold-load gotchas). Reconstructed commands reuse this exactly.
HFCACHE_MOUNT = "-v vllm-hfcache:/root/.cache/huggingface"


# --------------------------------------------------------------------------- #
# READ — parse the registry (tomllib) and look up a recipe.
# --------------------------------------------------------------------------- #
def load_registry(path) -> dict:
    """Parse a serve-recipe registry file (read-only, via `tomllib`)."""
    with open(path, "rb") as f:
        return tomllib.load(f)


def find_recipe(registry: dict, model: str) -> dict | None:
    """Return the recipe for `model`, matched by exact `model` first, then basename.

    So `"gpt-oss-120b"` matches a recorded `"openai/gpt-oss-120b"`, and vice-versa.
    """
    recipes = registry.get("recipe") or []
    for r in recipes:
        if r.get("model") == model:
            return r
    want = model.rsplit("/", 1)[-1]
    for r in recipes:
        if str(r.get("model", "")).rsplit("/", 1)[-1] == want:
            return r
    return None


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
        elif ord(ch) < 0x20:
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
    raise TypeError("unsupported TOML scalar type: %s" % type(v).__name__)


def _emit_kv(key: str, value, lines: list[str], indent: str) -> None:
    if isinstance(value, list):
        if len(value) <= 1:
            inner = ", ".join(_toml_scalar(x) for x in value)
            lines.append("%s%s = [%s]" % (indent, key, inner))
        else:
            lines.append("%s%s = [" % (indent, key))
            for x in value:
                lines.append("%s  %s," % (indent, _toml_scalar(x)))
            lines.append("%s]" % indent)
    else:
        lines.append("%s%s = %s" % (indent, key, _toml_scalar(value)))


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
            lines.append("")
            lines.append("%s[%s.%s]" % (sub_indent, header_path, k))
            _emit_table(v, "%s.%s" % (header_path, k), lines, sub_indent)


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
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n" + format_recipe(recipe))


# --------------------------------------------------------------------------- #
# READ (reproduce) — rebuild the exact `docker run` from a recorded recipe.
# --------------------------------------------------------------------------- #
def reconstruct_docker_run(recipe: dict) -> str:
    """Reconstruct the reproducible `docker run` for a recipe.

    The image ENTRYPOINT is `vllm serve`, so the model is a POSITIONAL after the
    image (NO extra 'serve'); flags follow the model.
    """
    serve = recipe.get("serve") or {}
    hw = recipe.get("hardware") or {}
    model = recipe.get("model") or ""
    image = serve.get("image") or ""
    port = serve.get("port")
    gpu_uuid = hw.get("gpu_uuid")

    parts = ["docker run -d"]
    parts.append("--gpus device=%s" % gpu_uuid if gpu_uuid else "--gpus all")
    for e in serve.get("env") or []:
        parts.append("-e %s" % e)
    parts.append(HFCACHE_MOUNT)
    if port:
        parts.append("-p 127.0.0.1:%s:%s" % (port, port))
    if image:
        parts.append(image)
    if model:
        parts.append(model)  # POSITIONAL model — entrypoint already is `vllm serve`
    for fl in serve.get("flags") or []:
        parts.append(fl)
    return " ".join(parts)


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
    first_flag = next((i for i, t in enumerate(args) if t.startswith("-")), len(args))
    flags = _group_flags(args[first_flag:])

    serve: dict = {}
    engine = _engine_from_image(config.get("Image") or "")
    if engine:
        serve["engine"] = engine
    if config.get("Image"):
        serve["image"] = config["Image"]
    port = _port_from_inspect(inspect)
    if port is not None:
        serve["port"] = port
    if serve_env:
        serve["env"] = serve_env
    if flags:
        serve["flags"] = flags

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

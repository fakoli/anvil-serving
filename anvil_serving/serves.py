"""anvil-serving serves — lifecycle for the local model serves
(status / up / down / rm / adopt).

The router (`anvil-serving router run`) only *connects* to model backends; it never
controls their containers. This verb fills that gap: a small, declarative way to
stop, start, and inspect the GPU-backed model serves — so you can free the cards
between sessions (`serves down`) and bring them back (`serves up`) without
remembering two different launch mechanisms.

Three companion verbs handle the messier day-to-day around experiments:
  - `serves rm <name-or-container>...` force-removes container(s) — and crucially works
    for a container that is NOT in the manifest (an experiment squatting a port): a token
    matching a manifest serve's name/container removes that serve's container, any other
    token is treated literally as a container name. An absent container is a no-op success.
  - `serves adopt <name>...` brings an externally-started (non-compose-managed) manifest
    serve under compose management by recreating it via its manifest `up` (the `--recreate`
    path: `docker rm -f` + `up`).
  - `serves up --compose <file> [service...]` brings up an ad-hoc/experiment serve straight
    from a compose file that is NOT in the manifest (`docker compose -f <file> up -d
    [service...]`) — independent of serves.toml; with `--compose`, `names` are compose
    SERVICE names.

It reads a manifest (default search: `~/.anvil-serving/serves.toml`, then
`./serves.toml`; bare `init` writes the machine-wide file, while
`init --single-model` retains the CWD convention; the
shipped reference is `examples/fakoli-dark/serves.toml`) that declares
each serve's container name, port, health path, declared `model` (served-model-name),
and an optional `up` command. Bringing a serve up is drift-safe: when `up` is a
`docker compose up -d`, that command IS the (re)start and is run UNCONDITIONALLY — even
when the container is already running — because compose recreates the container when its
config changed and fast-(re)starts it (a cheap no-op) when not, so editing the compose
file and re-running `serves up` recreates the container to match and a stale model is
never resurrected by a blind `docker start`. A one-shot `docker run` *script* serve can't
be re-run over an existing container, so it is `docker start`ed — with a loud warning if
it drifted from the declared `model` (fix: `--recreate`, or, better, convert it to a
compose file). A paused serve (either kind) is `docker unpause`d. `--recreate` forces a
clean `docker rm -f` + `up` for any serve. stdlib-only: `subprocess` to docker, `urllib`
for the health probe, `tomllib` to read the manifest.

GPU residency reservations (ADR-0017): a `[[serve]]` entry may declare
`gpu_role`/`vram_mib`/`residency`, and the manifest may declare `[[gpu_roles]]`
capacity rows (`id`, `vram_mib`, `reserve_mib`). When both are present, `up`
acquires the serve's VRAM reservation against the role's budget FIRST — an
over-budget request prints the per-role ledger (capacity/reserve/committed/
free plus the offending reservation) and exits 1 without running any container
command. The ledger is derived from docker state plus the declared fields (no
state file), so `down` releases a reservation simply by stopping the
container. Manifests without these fields are entirely unaffected.

TRUST BOUNDARY: a serve's `up` command from the manifest is EXECUTED. It is parsed
with `shlex` and run as an argv list (no shell), so `{dir}` paths with spaces are
safe and there is no shell-injection sink — but pointing `--manifest` at an
untrusted file still means running whatever programs its `up` lines name. Treat the
manifest as trusted, like a Makefile. A `bash {dir}/...sh` fresh-create `up` also
requires `bash` on PATH (Git Bash / WSL on Windows); a stopped container is just
`docker start`ed and needs none of this.
"""
import argparse
import base64
from contextlib import contextmanager, nullcontext
import copy
import hashlib
import json
import math
import mimetypes
import numbers
import os
import re
import shlex
import signal
import subprocess
import tempfile
import time
from . import envfile
from .events import LifecycleEventError, emit_lifecycle_event
from . import guard
from . import host as host_ops
from . import reservations
from . import serve_recipes
import sys
import urllib.request
import urllib.error

from .paths import config_path, runtime_url

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - guarded by requires-python >=3.11
    tomllib = None

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
# genericity:T012 — the default manifest is the CWD's own serves.toml (what
# `anvil-serving serves render`/`init` write there), not the shipped fakoli-dark
# example. EXAMPLE_MANIFEST keeps a name for the shipped reference topology
# (tests, docs) now that DEFAULT_MANIFEST no longer points at it.
DEFAULT_MANIFEST = "./serves.toml"
EXAMPLE_MANIFEST = os.path.join(REPO, "examples", "fakoli-dark", "serves.toml")
DEFAULT_RECIPE_REGISTRY = os.path.join("configs", "serve-recipes.toml")
DEFAULT_SERVE_PROFILES = "./serve-profiles.toml"
SERVE_PROFILES_SCHEMA = "anvil-serving/serve-profiles/v1"

# States meaning the container exists but is already stopped (nothing to free).
_STOPPED = ("exited", "created", "dead")


def _record_lifecycle_event(kind, payload):
    try:
        emit_lifecycle_event(kind, payload)
    except LifecycleEventError as exc:
        print(
            "lifecycle change applied but event was not recorded: %s" % exc,
            file=sys.stderr,
        )
        return False
    return True


_ENGINE_ALIASES = {
    "llama.cpp": "llamacpp",
    "llama-cpp": "llamacpp",
    "llama_cpp": "llamacpp",
}
# "audio" labels non-LLM serves (STT/TTS sidecars) truthfully in status output;
# it never routes into LLM-only paths (deploy render, multiplexer swap).
# "embedding"/"reranker" extend that precedent (ADR-0017 §7) for the
# purpose-model serves (text embeddings, cross-encoder reranking): they run on
# an OpenAI-compatible pooling engine, not a chat LLM, so labeling them "vllm"
# would invite LLM tooling (preflight, promotion gates) at a /v1 surface that
# has no chat completions. "image" (gpu-reservations:T012) labels the ComfyUI
# image/video-generation tenant the same truthful way: a graph UI + API,
# no OpenAI-compatible surface at all. "q36" is the dedicated q36 CUDA engine;
# it exposes an OpenAI-compatible chat surface but is not vLLM/llama.cpp.
_ENGINES = {
    "vllm", "sglang", "llamacpp", "q36",
    "audio", "embedding", "reranker", "image",
}
# ADR-0017 GPU residency reservations: the residency vocabulary for a serve's
# declared VRAM reservation. "resident" is never evicted, "evictable" may be
# stopped to make room, "on-demand" is started per task and may evict
# "evictable" serves. (The VRAM types are reservations, never *Lease —
# AdmissionLease in router/admission.py is the request-admission layer.)
_RESIDENCIES = ("resident", "evictable", "on-demand")
DUAL_GPU_EXCLUSIVE_MODE = "dual-gpu-exclusive"
# serve groups (serve-groups): a serve may be tagged into any number of named
# groups so `serves up/down/status --group NAME` can act on the whole set at
# once. "all" is the RESERVED implicit group (every serve in the manifest set);
# it is never authored on a [[serve]] entry.
RESERVED_GROUP = "all"
# ADR-0017 §5 eviction defaults: the bounded ADR-0018 drain wait before a
# victim's container is stopped, and the deployed router the transition talks
# to (matching the promotion plans' router_health_url default host).
EVICTION_DRAIN_TIMEOUT = 120
DEFAULT_ROUTER_URL = "http://127.0.0.1:8000"
DEFAULT_ROUTER_CONTAINER = "anvil-router"
DEFAULT_ROUTER_CFG_VOLUME = "anvil-router-cfg"
DEFAULT_STACK = "serving"
DEFAULT_COMPOSE_PROJECT = "anvil-serving"
_STACK_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ROUTER_CFG_SIDE_MOUNT = "/cfg"
_ROUTER_CFG_PATH = "/cfg/config.toml"
LIFECYCLE_READINESS_TIMEOUT_SECONDS = 600
LIFECYCLE_READINESS_POLL_SECONDS = 2
DOCKER_STOP_COMMAND_TIMEOUT_SECONDS = 45
_DOCKER_STATES = {
    "absent", "created", "dead", "error", "exited", "paused", "removing",
    "restarting", "running", "unknown",
}
_ENGINE_MARKERS = {
    "vllm": re.compile(r"(^|[^a-z0-9])vllm([^a-z0-9]|$)"),
    "sglang": re.compile(r"(^|[^a-z0-9])sglang([^a-z0-9]|$)"),
    "llamacpp": re.compile(r"(^|[^a-z0-9])llama(?:[._-]?cpp|[._-]server)([^a-z0-9]|$)"),
}


def default_manifest_candidates():
    """Manifest search path for operator commands when --manifest is omitted."""
    return [config_path("serves.toml"), DEFAULT_MANIFEST]


def resolve_manifest_path(path=None):
    if path:
        return path
    for candidate in default_manifest_candidates():
        if os.path.isfile(os.path.expanduser(candidate)):
            return candidate
    return DEFAULT_MANIFEST


def resolve_recipe_registry_path(path=None):
    """Resolve the recipe catalog used by role-based serve switching."""
    if path:
        return path
    candidates = (
        config_path("serve-recipes.toml"),
        "./serve-recipes.toml",
        DEFAULT_RECIPE_REGISTRY,
        os.path.join(REPO, "configs", "serve-recipes.toml"),
        os.path.join(HERE, "_scaffold_templates", "serve-recipes.toml"),
    )
    for candidate in candidates:
        if os.path.isfile(os.path.expanduser(candidate)):
            return candidate
    return config_path("serve-recipes.toml")


class ServeProfileError(ValueError):
    """A declared serving profile is incomplete or internally inconsistent."""


def default_serve_profile_candidates():
    """Return the operator-first search path for serving-profile declarations."""
    return [config_path("serve-profiles.toml"), DEFAULT_SERVE_PROFILES]


def resolve_serve_profiles_path(path=None):
    if path:
        return path
    for candidate in default_serve_profile_candidates():
        if os.path.isfile(os.path.expanduser(candidate)):
            return candidate
    return config_path("serve-profiles.toml")


def load_serve_profiles(path):
    """Load a small, declarative selector for split and exclusive profiles.

    A profile contains the target exclusive serve, named split restore group,
    and optional bounded readiness timing.  The serves manifest remains the
    sole owner of lifecycle, GPU reservations, and router profile paths, so
    this file cannot introduce a second, drifting implementation of a model
    serve.
    """
    with open(os.path.expanduser(path), "rb") as f:
        data = tomllib.load(f)
    if data.get("schema") != SERVE_PROFILES_SCHEMA:
        raise ServeProfileError(
            "serve profiles schema must be %r" % SERVE_PROFILES_SCHEMA
        )
    rows = data.get("profile")
    if not isinstance(rows, list) or not rows:
        raise ServeProfileError("serve profiles must declare at least one [[profile]] row")
    profiles = []
    seen = set()
    required = ("id", "mode", "exclusive_target", "restore_group")
    for raw in rows:
        if not isinstance(raw, dict):
            raise ServeProfileError("each [[profile]] row must be a TOML table")
        missing = [key for key in required if not isinstance(raw.get(key), str) or not raw[key]]
        if missing:
            raise ServeProfileError(
                "serve profile is missing non-empty %s" % ", ".join(missing)
            )
        if raw["id"] in seen:
            raise ServeProfileError("duplicate serve profile id %r" % raw["id"])
        if raw["mode"] not in {"split", DUAL_GPU_EXCLUSIVE_MODE}:
            raise ServeProfileError(
                "serve profile %r has unsupported mode %r"
                % (raw["id"], raw["mode"])
            )
        profile = dict(raw)
        profile.setdefault("startup_timeout", LIFECYCLE_READINESS_TIMEOUT_SECONDS)
        profile.setdefault("poll_interval", LIFECYCLE_READINESS_POLL_SECONDS)
        for field in ("startup_timeout", "poll_interval"):
            value = profile[field]
            if (
                isinstance(value, bool)
                or not isinstance(value, numbers.Real)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ServeProfileError(
                    "serve profile %r %s must be a finite positive number"
                    % (raw["id"], field)
                )
        seen.add(raw["id"])
        profiles.append(profile)
    return profiles


def select_serve_profile(profiles, profile_id):
    matches = [profile for profile in profiles if profile["id"] == profile_id]
    if len(matches) != 1:
        raise ServeProfileError("unknown serve profile %r" % profile_id)
    return matches[0]


def manifest_set_paths(manifest_path):
    """Every `serves*.toml` in the manifest's directory (sorted), for group
    resolution (serve-groups §2).

    A serve may span serves.toml + serves.voice.toml + serves.comfyui.toml, so
    a group must resolve across the whole set — deterministically. The set is
    "all files matching serves*.toml in the manifest's own directory" (the
    default ~/.anvil-serving, or the --manifest's dir), sorted by path. The
    manifest itself is always included even if it does not match the glob
    (an operator may point --manifest at a differently named file).
    """
    import glob

    mdir = os.path.dirname(os.path.abspath(os.path.expanduser(manifest_path)))
    paths = sorted(glob.glob(os.path.join(mdir, "serves*.toml")))
    manifest_abs = os.path.abspath(os.path.expanduser(manifest_path))
    if os.path.isfile(manifest_abs) and manifest_abs not in {
        os.path.abspath(p) for p in paths
    }:
        paths.append(manifest_abs)
    return paths


def load_manifest_set(manifest_path, *, reject_duplicates=True):
    """Load + de-dupe the serves across the whole manifest set (serve-groups §2).

    De-dup is BY CONTAINER (a serve can be mirrored across files — e.g. the
    read-only ledger mirrors in serves.comfyui.toml re-declare the serves.toml
    reservations on the same card). The lifecycle-owning entry (one that
    declares an `up` command) wins over a read-only mirror so `--group up/down`
    always targets the real serve and the reservation ledger is not
    double-counted; ties keep the first entry in sorted-file order. The result
    order is that first-seen order, so `serves groups` output is deterministic.
    """
    by_container = {}
    for path in manifest_set_paths(manifest_path):
        try:
            loaded = load_manifest(path)
        except FileNotFoundError:
            continue
        for s in loaded:
            s["_manifest_file"] = os.path.abspath(os.path.expanduser(path))
            key = s["container"]
            incumbent = by_container.get(key)
            if incumbent is None:
                by_container[key] = s
            elif not incumbent.get("up") and s.get("up"):
                # A lifecycle-owning entry supersedes a read-only mirror while
                # keeping the incumbent's position (dict reassignment).
                by_container[key] = s
    resolved = list(by_container.values())
    if reject_duplicates:
        _reject_duplicate_serve_names(resolved)
    return resolved


def _reject_duplicate_serve_names(serves):
    """Refuse two surviving entries that share a `name`.

    De-dup above is BY CONTAINER, so a read-only mirror collapses into its
    lifecycle-owning entry and is not affected. Two entries that survive with
    the same name are a different thing: name selection matches both, one
    silently wins, and an edit to the loser is invisible at runtime. That
    shadowing caused two live incidents in six days, the second leaving a
    promoted serve unmanageable because `serves down` resolved the wrong
    container. `serves lint` reports this without raising, so an operator can
    see the whole set of offenders before this refusal blocks them.
    """
    by_name = {}
    for serve in serves:
        by_name.setdefault(serve["name"], []).append(serve)
    duplicates = {n: e for n, e in by_name.items() if len(e) > 1}
    if not duplicates:
        return
    detail = "; ".join(
        "%r in %s (containers: %s)" % (
            name,
            ", ".join(sorted({e.get("_manifest_file", "?") for e in entries})),
            ", ".join(e["container"] for e in entries),
        )
        for name, entries in sorted(duplicates.items())
    )
    raise ValueError(
        "duplicate serve name(s) across the manifest set: %s. Each serve must "
        "be declared once; run `anvil-serving serves lint` to see every "
        "finding." % detail
    )


def resolve_group(serves, group):
    """Serves tagged `group`; the reserved 'all' selects every serve."""
    if group.lower() == RESERVED_GROUP:
        return list(serves)
    return [s for s in serves if group in (s.get("groups") or [])]


def select_groups(serves, groups):
    """Union of the serves tagged by any of `groups`, de-duped by container.

    Returns ``(selected, unknown)`` where `unknown` lists requested groups that
    matched no serve (a likely typo — the caller refuses rather than acting on
    a silently empty set). The reserved 'all' is never "unknown".
    """
    selected, seen, unknown = [], set(), []
    for group in groups:
        members = resolve_group(serves, group)
        if not members and group.lower() != RESERVED_GROUP:
            unknown.append(group)
            continue
        for s in members:
            if s["container"] not in seen:
                seen.add(s["container"])
                selected.append(s)
    return selected, unknown


def resolve_group_targets(serves, groups, names):
    """Target serve names for a `--group` operation (serve-groups §3).

    The union of every serve tagged by any of `groups` with the positional
    `names`, de-duped by container, preserving group-then-name order. Returns
    ``(target_names, unknown_groups)``; a non-empty `unknown_groups` means a
    requested group matched no serve and the caller should refuse.
    """
    group_serves, unknown = select_groups(serves, groups)
    selected = list(group_serves)
    seen = {s["container"] for s in selected}
    for s in (_select(serves, names) if names else []):
        if s["container"] not in seen:
            seen.add(s["container"])
            selected.append(s)
    return [s["name"] for s in selected], unknown


def groups_summary(serves):
    """Machine-readable catalog of defined groups -> member serve names.

    Mirrors the status/reservation JSON conventions: one row per group with its
    members in manifest-set order, groups sorted by name. The reserved 'all' is
    implicit (every serve) and is reported separately so tooling can enumerate
    it without it colliding with authored groups.
    """
    catalog = {}
    for s in serves:
        for group in (s.get("groups") or []):
            members = catalog.setdefault(group, [])
            if s["name"] not in members:
                members.append(s["name"])
    return {
        "groups": [
            {"group": group, "serves": members}
            for group, members in sorted(catalog.items())
        ],
        "all": [s["name"] for s in serves],
    }


# Shared with controller/transport token resolution (ADR-0033): one dotenv
# grammar for every durable-secret fallback path.
_read_dotenv = envfile.read_dotenv


def _serve_env(s):
    env = os.environ.copy()
    shell_names = set(env)
    for name, value in _read_dotenv(os.path.join(os.path.expanduser("~"), ".env")).items():
        env.setdefault(name, value)
    for name, value in _read_dotenv(config_path(".env")).items():
        if name not in shell_names:
            env[name] = value
    manifest_dir = s.get("_manifest_dir")
    if manifest_dir:
        for name, value in _read_dotenv(os.path.join(manifest_dir, ".env")).items():
            if name not in shell_names:
                env[name] = value
    return env


def _legacy_engine(s, up):
    """Infer engines for manifests generated before the field existed.

    Old generated entries identify their engine through the container name,
    compose service, module, or launch-script name. An entry with no marker is
    from the older SGLang-only era. Conflicting markers require an explicit
    migration instead of guessing which command the operator intended.
    """
    candidates = [str(s.get("container") or "")]
    if up:
        first = os.path.basename(up[0])
        candidates.append(first)
        python_launcher = re.fullmatch(r"python(?:\.exe|[0-9]+(?:\.[0-9]+)?)?", first.casefold())
        if (first.casefold() in {"bash", "sh"} or python_launcher) and len(up) > 1:
            candidates.append(os.path.basename(up[1]))
        candidates.extend(up[index + 1] for index, token in enumerate(up[:-1]) if token == "-m")
        try:
            compose_up = up.index("up")
        except ValueError:
            pass
        else:
            candidates.extend(token for token in up[compose_up + 1:] if not token.startswith("-"))

    markers = {
        engine
        for candidate in candidates
        for engine, pattern in _ENGINE_MARKERS.items()
        if pattern.search(candidate.casefold())
    }
    if len(markers) > 1:
        raise ValueError(
            "serve entry has conflicting legacy engine markers "
            f"{sorted(markers)}; add an explicit engine: {s!r}"
        )
    return next(iter(markers), "sglang")


def _normalize_engine(s, up):
    if "engine" not in s:
        return _legacy_engine(s, up)
    raw_engine = str(s.get("engine")).lower()
    engine = _ENGINE_ALIASES.get(raw_engine, raw_engine)
    if engine not in _ENGINES:
        raise ValueError(
            f"serve entry engine must be one of {sorted(_ENGINES)}: {s!r}"
        )
    return engine


def _normalize_reservation(s, raw):
    """Validate/normalize ADR-0017 reservation fields on one serve entry.

    All three fields are optional and independent; an entry that declares none
    of them is left untouched (no keys are added), so pre-reservation manifests
    parse byte-for-byte the same as before.
    """
    if "gpu_inference" in s and not isinstance(s.get("gpu_inference"), bool):
        raise ValueError(f"serve entry gpu_inference must be a boolean: {raw!r}")
    if "native_kv_offload" in s and not isinstance(s.get("native_kv_offload"), bool):
        raise ValueError(
            f"serve entry native_kv_offload must be a boolean: {raw!r}"
        )
    if "gpu_role" in s and "gpu_roles" in s:
        raise ValueError(
            "serve entry must declare either gpu_role or gpu_roles, not both: "
            f"{raw!r}"
        )
    if "gpu_role" in s:
        gpu_role = s.get("gpu_role")
        if not isinstance(gpu_role, str) or not gpu_role.strip():
            raise ValueError(f"serve entry gpu_role must be a non-empty string: {raw!r}")
        s["gpu_role"] = gpu_role.strip()
    if "gpu_roles" in s:
        gpu_roles = s.get("gpu_roles")
        if (
            not isinstance(gpu_roles, list)
            or len(gpu_roles) != 2
            or any(not isinstance(role, str) or not role.strip() for role in gpu_roles)
        ):
            raise ValueError(
                "serve entry gpu_roles must contain exactly two non-empty strings: "
                f"{raw!r}"
            )
        normalized_roles = [role.strip() for role in gpu_roles]
        if len(set(normalized_roles)) != 2:
            raise ValueError(
                f"serve entry gpu_roles must be distinct: {raw!r}"
            )
        s["gpu_roles"] = normalized_roles
    mode = s.get("operating_mode")
    if mode is not None:
        if mode != DUAL_GPU_EXCLUSIVE_MODE:
            raise ValueError(
                "serve entry operating_mode must be "
                f"{DUAL_GPU_EXCLUSIVE_MODE!r}: {raw!r}"
            )
        if "gpu_roles" not in s:
            raise ValueError(
                "dual-gpu-exclusive serve must declare gpu_roles: "
                f"{raw!r}"
            )
        if s.get("tensor_parallel_size") != 2:
            raise ValueError(
                "dual-gpu-exclusive serve must declare tensor_parallel_size = 2: "
                f"{raw!r}"
            )
    elif "gpu_roles" in s or "tensor_parallel_size" in s:
        raise ValueError(
            "multi-role/tensor-parallel serve must declare operating_mode = "
            f"{DUAL_GPU_EXCLUSIVE_MODE!r}: {raw!r}"
        )
    if "router_tier" in s:
        # The serve's router tier id, for the ADR-0018 quiesce/drain transition
        # that eviction (gpu-reservations:T005) runs before stopping it. Like
        # a promotion plan's affected_tiers, the mapping is DECLARED, not
        # guessed: an evictable serve that routes traffic should name its tier
        # so in-flight generations drain before `serves down`; one without it
        # (nothing routes through the router) is stopped directly.
        router_tier = s.get("router_tier")
        if not isinstance(router_tier, str) or not router_tier.strip():
            raise ValueError(
                f"serve entry router_tier must be a non-empty string: {raw!r}"
            )
        s["router_tier"] = router_tier.strip()
    if "vram_mib" in s:
        vram = s.get("vram_mib")
        if isinstance(vram, bool) or not isinstance(vram, int) or vram <= 0:
            raise ValueError(
                f"serve entry vram_mib must be a positive integer (MiB): {raw!r}"
            )
    if "residency" in s:
        residency = s.get("residency")
        if not isinstance(residency, str):
            raise ValueError(
                "serve entry residency must be one of "
                f"{list(_RESIDENCIES)}: {raw!r}"
            )
        normalized = residency.strip().lower().replace("_", "-")
        if normalized not in _RESIDENCIES:
            raise ValueError(
                "serve entry residency must be one of "
                f"{list(_RESIDENCIES)} (got {residency!r}): {raw!r}"
            )
        s["residency"] = normalized


_SERVE_RUNTIMES = ("docker", "native")


class NativeRuntimeNotSupported(ValueError):
    """A `runtime = "native"` serve was declared before native lifecycle exists.

    ADR-0034 makes `runtime` an explicit discriminator so a non-container serve
    is expressible. The native lifecycle itself is not implemented: every serve
    path here resolves a container, so accepting a native entry would surface as
    a `KeyError` deep inside an unrelated command. Failing at manifest load
    keeps the schema honest and the failure legible.
    """


def _normalize_serve_runtime(s, raw):
    """Validate one serve entry's runtime discriminator (ADR-0034).

    Normalizes exactly like the neighbouring `residency` check so a manifest
    author gets the same forgiveness for both fields.
    """
    runtime = s["runtime"]
    if not isinstance(runtime, str):
        raise ValueError(
            "serve entry runtime must be one of "
            f"{list(_SERVE_RUNTIMES)}: {raw!r}"
        )
    normalized = runtime.strip().lower()
    if normalized not in _SERVE_RUNTIMES:
        raise ValueError(
            "serve entry runtime must be one of "
            f"{list(_SERVE_RUNTIMES)} (got {runtime!r}): {raw!r}"
        )
    s["runtime"] = runtime = normalized
    if runtime == "docker":
        if not s.get("container"):
            raise ValueError(
                "serve entry missing required field(s) container: %r" % (raw,)
            )
        return
    if "container" in s:
        raise ValueError(
            'serve entry with runtime "native" must not declare container: %r'
            % (raw,)
        )
    raise NativeRuntimeNotSupported(
        'serve entry %r declares runtime "native", which is not implemented '
        "yet; only \"docker\" serves can be loaded" % s["name"]
    )


def _normalize_mode_router_configs(s, raw, manifest_dir):
    """Validate the router profiles owned by a routed exclusive-mode serve."""
    fields = ("router_config", "rollback_router_config")
    present = [field for field in fields if field in s]
    exclusive = reservations.is_exclusive(s)
    router_tier = s.get("router_tier")
    if present and not exclusive:
        raise ValueError(
            "serve entry router_config fields are only valid for a "
            f"dual-gpu-exclusive serve: {raw!r}"
        )
    if exclusive and router_tier and len(present) != len(fields):
        missing = [field for field in fields if field not in s]
        raise ValueError(
            "routed dual-gpu-exclusive serve must declare "
            f"{', '.join(missing)}: {raw!r}"
        )
    if present and not router_tier:
        raise ValueError(
            "serve entry router_config fields require router_tier: "
            f"{raw!r}"
        )
    for field in present:
        value = s.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"serve entry {field} must be a non-empty path: {raw!r}"
            )
        value = value.strip().replace("{dir}", manifest_dir)
        resolved = os.path.abspath(
            value if os.path.isabs(value) else os.path.join(manifest_dir, value)
        )
        if not os.path.isfile(resolved):
            raise ValueError(
                f"serve entry {field} does not exist: {resolved}"
            )
        s[field] = resolved


def _normalize_groups(s, raw):
    """Validate/normalize the optional `groups` field on one serve entry.

    `groups` is an optional list of non-empty strings (a serve may belong to
    many groups); absent = no groups. Anything else — a non-list, a non-string
    member, an empty/whitespace member, or the reserved name "all" — fails
    loudly at parse time, exactly like the gpu_role/vram_mib/residency fields.
    An entry that omits `groups` is left untouched (no key added), so
    pre-groups manifests parse byte-for-byte the same as before.
    """
    if "groups" not in s:
        return
    groups = s.get("groups")
    if not isinstance(groups, list):
        raise ValueError(
            f"serve entry groups must be a list of non-empty strings: {raw!r}"
        )
    normalized = []
    for member in groups:
        if not isinstance(member, str) or not member.strip():
            raise ValueError(
                f"serve entry groups must be a list of non-empty strings: {raw!r}"
            )
        name = member.strip()
        if name.lower() == RESERVED_GROUP:
            raise ValueError(
                "serve entry groups must not include the reserved group "
                f"{RESERVED_GROUP!r} (it implicitly selects every serve): {raw!r}"
            )
        if name not in normalized:
            normalized.append(name)
    s["groups"] = normalized


def _normalize_shared_volumes(s, raw):
    """Validate/normalize the optional `shared_volumes` field on one serve entry.

    Sharing a named volume between containers is a DEPLOYMENT decision, so it
    is declared here, not inferred: `shared_volumes` lists the volume names
    this serve deliberately shares with other containers. The `serves up`
    storage guard never auto-repairs ownership on a declared-shared volume
    (whose uid is the deployment's call, made where the sharing is declared),
    and treats runtime sharing of an UNDECLARED volume as a topology fault to
    report. Same shape rules as `groups`: optional list of non-empty strings,
    absent key left untouched.
    """
    if "shared_volumes" not in s:
        return
    volumes = s.get("shared_volumes")
    if not isinstance(volumes, list):
        raise ValueError(
            f"serve entry shared_volumes must be a list of non-empty volume "
            f"names: {raw!r}"
        )
    normalized = []
    for member in volumes:
        if not isinstance(member, str) or not member.strip():
            raise ValueError(
                f"serve entry shared_volumes must be a list of non-empty "
                f"volume names: {raw!r}"
            )
        name = member.strip()
        if name not in normalized:
            normalized.append(name)
    s["shared_volumes"] = normalized


def load_manifest(path):
    """Parse the serves manifest into a list of serve dicts.

    Each serve's `up` is parsed with `shlex` into an argv list, then `{dir}` is
    resolved to the manifest's own directory PER TOKEN — so a repo path with
    spaces stays one argument and there is no shell to inject into.
    """
    if tomllib is None:
        raise RuntimeError("tomllib unavailable (need Python >= 3.11)")
    with open(path, "rb") as f:
        data = tomllib.load(f)
    mdir = os.path.dirname(os.path.abspath(path))
    # ADR-0017: optional [[gpu_roles]] capacity rows (id / vram_mib /
    # reserve_mib, mirroring the topology schema) declare each gpu_role's VRAM
    # budget for the reservation ledger. Attached to every serve dict (like
    # `_manifest_dir`) so the budgets travel with the parsed serves through
    # every cmd_up call path — but ONLY when the manifest declares them, so a
    # pre-reservation manifest still parses byte-for-byte the same as before.
    gpu_role_budgets = reservations.parse_gpu_roles(data)
    serves = []
    for raw in data.get("serve", []):
        s = dict(raw)
        missing = [
            field for field in ("name", "runtime", "port")
            if field not in s or s.get(field) in ("", None)
        ]
        if not s.get("model") and not s.get("served_name"):
            missing.append("model/served_name")
        if missing:
            raise ValueError(
                "serve entry missing required field(s) "
                f"{', '.join(missing)}: {raw!r}"
            )
        _normalize_serve_runtime(s, raw)
        if not isinstance(s.get("port"), int):
            raise ValueError(f"serve entry port must be an integer: {raw!r}")
        s["model"] = s.get("model") or s.get("served_name")
        s["served_name"] = s.get("served_name") or s["model"]
        up = shlex.split(s["up"]) if s.get("up") else None
        stack = s.get("stack", DEFAULT_STACK)
        if not isinstance(stack, str) or not _STACK_RE.fullmatch(stack):
            raise ValueError(
                "serve entry stack must be a lowercase slug "
                f"(for example 'serving' or 'voice-audio'): {raw!r}"
            )
        s["stack"] = stack
        if _is_compose_up(up):
            explicit_project = _explicit_compose_project(up)
            expected_project = _stack_project(stack)
            if explicit_project and explicit_project != expected_project:
                raise ValueError(
                    f"serve entry stack {stack!r} owns Compose project "
                    f"{expected_project!r}, but up declares {explicit_project!r}: {raw!r}"
                )
        s["engine"] = _normalize_engine(s, up)
        _normalize_reservation(s, raw)
        _normalize_mode_router_configs(s, raw, mdir)
        declared_roles = [
            reservation.gpu_role for reservation in reservations.reservations_of(s)
        ]
        unknown_roles = [
            role for role in declared_roles if role not in gpu_role_budgets
        ]
        if reservations.is_exclusive(s) and unknown_roles:
            raise ValueError(
                "serve entry references undeclared gpu role(s) "
                f"{unknown_roles}: {raw!r}"
            )
        _normalize_groups(s, raw)
        _normalize_shared_volumes(s, raw)
        if gpu_role_budgets:
            s[reservations.GPU_ROLES_KEY] = gpu_role_budgets
        s["_manifest_dir"] = mdir
        s.setdefault("health", "/health")
        if up:
            # split the TEMPLATE (forward-slash, no backslashes) then substitute,
            # so a backslashed/spaced {dir} never re-splits.
            s["up"] = [tok.replace("{dir}", mdir) for tok in up]
        serves.append(s)
    return serves


def load_promotions(path):
    """Load guarded model-promotion plans from a serves manifest.

    Paths are resolved relative to the manifest, matching ``serve.up``. A plan
    names the promoted and rollback serve, the fixed router tier ids, and one
    complete direct-only router config for each model identity.
    """
    with open(path, "rb") as f:
        data = tomllib.load(f)
    mdir = os.path.dirname(os.path.abspath(path))
    required = (
        "name", "target", "rollback", "affected_tiers",
        "router_config", "rollback_router_config",
    )
    plans = []
    for raw in data.get("promotion", []):
        plan = dict(raw)
        missing = [field for field in required if not plan.get(field)]
        if missing:
            raise ValueError("promotion entry missing required field(s) %s: %r" % (
                ", ".join(missing), raw))
        legacy = sorted(
            field for field in ("router_profile", "rollback_router_profile")
            if field in plan
        )
        if legacy:
            raise ValueError(
                "promotion entry contains removed profile field(s): %s"
                % ", ".join(legacy)
            )
        for field in ("router_config", "rollback_router_config"):
            value = str(plan[field]).replace("{dir}", mdir)
            resolved = os.path.abspath(
                value if os.path.isabs(value) else os.path.join(mdir, value)
            )
            if not os.path.isfile(resolved):
                raise ValueError(
                    "promotion entry %r %s does not exist: %s"
                    % (plan["name"], field, resolved)
                )
            plan[field] = resolved
        plan.setdefault("candidate", None)
        affected = plan.get("affected_tiers")
        if (
            not isinstance(affected, list)
            or not affected
            or not all(isinstance(tier, str) and tier for tier in affected)
            or len(set(affected)) != len(affected)
        ):
            raise ValueError(
                "promotion affected_tiers must be a non-empty unique array of tier ids"
            )
        plan["affected_tiers"] = list(affected)
        plan.setdefault("drain_timeout", 120)
        plan.setdefault("needle_ctx", 32768)
        plan.setdefault("tool_batch", 20)
        plan.setdefault("startup_timeout", 600)
        plan.setdefault("rollback_startup_timeout", plan["startup_timeout"])
        plan.setdefault("poll_interval", 5)
        for field in (
            "startup_timeout", "rollback_startup_timeout", "poll_interval",
            "drain_timeout",
        ):
            value = plan[field]
            if (isinstance(value, bool) or not isinstance(value, numbers.Real)
                    or not math.isfinite(value) or value <= 0):
                raise ValueError("promotion %s must be a finite positive number" % field)
        default_gate = {
            "name": "preflight", "checks": "smoke,json,needle,tools",
            "thinking_mode": "default", "visible_answer_tokens": 256,
            "reasoning_headroom_tokens": 0,
            "reasoning_evidence": "any",
        }
        for field in ("gate", "rollback_gate"):
            gates = plan.get(field) or [default_gate]
            if not isinstance(gates, list) or not all(isinstance(g, dict) for g in gates):
                raise ValueError("promotion %s must be an array of gate tables" % field)
            normalized = []
            for index, raw_gate in enumerate(gates):
                gate = dict(default_gate)
                gate.update(raw_gate)
                gate.setdefault("name", "%s-%d" % (field, index + 1))
                if gate["thinking_mode"] not in {"default", "enabled", "disabled", "unsupported"}:
                    raise ValueError("promotion gate has invalid thinking_mode: %r" % gate)
                if gate["reasoning_evidence"] not in {"any", "required", "forbidden"}:
                    raise ValueError("promotion gate has invalid reasoning_evidence: %r" % gate)
                if gate.get("json_out"):
                    value = str(gate["json_out"]).replace("{dir}", mdir)
                    gate["json_out"] = os.path.abspath(value if os.path.isabs(value) else os.path.join(mdir, value))
                normalized.append(gate)
            plan[field] = normalized
        plans.append(plan)
    return plans


def _exact_serve(serves, name):
    matches = [serve for serve in serves if serve["name"] == name]
    if len(matches) != 1:
        raise ValueError("serve %r must match exactly one manifest entry" % name)
    return matches[0]


def _validate_promotion_topology(serves, plan):
    """Validate both complete direct configs against their selected serve."""
    from .router.config import load as load_router_config

    target = _exact_serve(serves, plan["target"])
    old = _exact_serve(serves, plan["rollback"])
    affected = set(plan["affected_tiers"])
    if not affected:
        raise ValueError("promotion affected_tiers must not be empty")
    if target["port"] != old["port"]:
        raise ValueError("promotion target and rollback must use the same fixed endpoint port")
    if target["name"] == old["name"]:
        raise ValueError("promotion target and rollback must be distinct serves")
    promoted = load_router_config(plan["router_config"])
    rolled_back = load_router_config(plan["rollback_router_config"])
    if dict(promoted.model_routes) != dict(rolled_back.model_routes):
        raise ValueError("promotion router configs must declare identical direct aliases")
    promoted_ids = {tier.id for tier in promoted.tiers}
    rollback_ids = {tier.id for tier in rolled_back.tiers}
    if promoted_ids != rollback_ids:
        raise ValueError("promotion router configs must declare identical tier ids")
    for tier_id in affected:
        promoted_tier = promoted.tier(tier_id)
        rollback_tier = rolled_back.tier(tier_id)
        if not promoted_tier.model_identity or not rollback_tier.model_identity:
            raise ValueError(
                "promotion affected tiers must enable exact model_identity in both configs"
            )
        if promoted_tier.model != target["served_name"]:
            raise ValueError(
                "promotion router config tier %r model does not match target %r"
                % (tier_id, target["served_name"])
            )
        if rollback_tier.model != old["served_name"]:
            raise ValueError(
                "rollback router config tier %r model does not match rollback %r"
                % (tier_id, old["served_name"])
            )
    for tier_id in promoted_ids - affected:
        if promoted.tier(tier_id) != rolled_back.tier(tier_id):
            raise ValueError(
                "promotion router configs differ on unaffected tier %r" % tier_id
            )
    return True


_PROMOTION_DEFAULTS = (
    ("drain_timeout", 120),
    ("needle_ctx", 32768),
    ("tool_batch", 20),
    ("startup_timeout", 600),
    ("rollback_startup_timeout", 600),
    ("poll_interval", 5),
)


def derive_promotion_plan(
    serves, target_name, rollback_name, router_config, rollback_router_config,
):
    """Derive a complete ``[[promotion]]`` plan dict from two serves and their
    promoted/rollback router configs (issue #381, feature 16: `serves promote
    --derive`).

    Resolves both config paths with `os.path.abspath` (relative inputs are
    resolved against the current working directory) and requires each to
    exist. `target_name`/`rollback_name` must each match exactly one manifest
    serve (`_exact_serve`). `affected_tiers` is the sorted set of tier ids in
    the PROMOTED router config whose `model` equals the target serve's
    `served_name`; an empty result is refused rather than silently emitting a
    no-op plan. The six numeric fields `load_promotions` would otherwise
    `setdefault` are emitted explicitly, so the returned plan is already
    complete. Before returning, the plan is validated with the existing
    `_validate_promotion_topology` -- pure library code: returns a dict on
    success, raises `ValueError` on any input the topology cannot support,
    and never prints.
    """
    from .router.config import load as load_router_config

    router_config = os.path.abspath(router_config)
    rollback_router_config = os.path.abspath(rollback_router_config)
    for label, path in (
        ("router_config", router_config),
        ("rollback_router_config", rollback_router_config),
    ):
        if not os.path.isfile(path):
            raise ValueError("promotion %s does not exist: %s" % (label, path))
        # `load_promotions` substitutes the literal token "{dir}" with the
        # manifest directory unconditionally -- a real path component named
        # `{dir}` would survive derivation and then resolve to a different,
        # wrong path on reload. Refuse instead of emitting a mangled plan.
        if "{dir}" in path:
            raise ValueError(
                "promotion %s path contains the literal '{dir}' placeholder, "
                "which load_promotions would substitute on reload: %s"
                % (label, path)
            )

    target = _exact_serve(serves, target_name)
    _exact_serve(serves, rollback_name)  # must match exactly one serve too

    promoted = load_router_config(router_config)
    affected_tiers = sorted(
        tier.id for tier in promoted.tiers if tier.model == target["served_name"]
    )
    if not affected_tiers:
        raise ValueError(
            "no tier in %s serves target %r (served name %r)"
            % (router_config, target_name, target["served_name"])
        )

    plan = {
        "name": "%s-promotion" % target_name,
        "target": target_name,
        "rollback": rollback_name,
        "affected_tiers": affected_tiers,
        "router_config": router_config,
        "rollback_router_config": rollback_router_config,
    }
    for field, value in _PROMOTION_DEFAULTS:
        plan[field] = value

    _validate_promotion_topology(serves, plan)
    return plan


def _render_promotion_toml(plan):
    """Hand-render one ``[[promotion]]`` block as TOML text (stdlib only --
    no `tomli_w`). Returns a string ending in exactly one trailing newline.

    Field order: name, target, rollback, affected_tiers, router_config,
    rollback_router_config, then the six numeric defaults in
    `_PROMOTION_DEFAULTS` order. Strings are TOML basic strings with
    backslash/double-quote escaping (load-bearing on Windows paths).
    """
    def _basic_string(value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        # Control characters are illegal raw in a TOML basic string; a serve
        # name or tier id carrying one must not yield rc 0 plus a block
        # tomllib cannot parse back. \uXXXX escapes round-trip identically.
        escaped = "".join(
            ch if ch >= " " and ch != "\x7f" else "\\u%04X" % ord(ch)
            for ch in escaped
        )
        return '"%s"' % escaped

    lines = [
        "[[promotion]]",
        "name = %s" % _basic_string(plan["name"]),
        "target = %s" % _basic_string(plan["target"]),
        "rollback = %s" % _basic_string(plan["rollback"]),
        "affected_tiers = [%s]" % ", ".join(
            _basic_string(tier) for tier in plan["affected_tiers"]
        ),
        "router_config = %s" % _basic_string(plan["router_config"]),
        "rollback_router_config = %s" % _basic_string(plan["rollback_router_config"]),
    ]
    for field, _default in _PROMOTION_DEFAULTS:
        lines.append("%s = %d" % (field, plan[field]))
    return "\n".join(lines) + "\n"


def cmd_promote_derive(
    serves, target_name, rollback_name, router_config, rollback_router_config,
    out=None,
):
    """Derive and print (or, with `out`, write) a `[[promotion]]` block.

    Read-only unless `out` is given. `out` never overwrites an existing
    file -- refuses with exit 1 and leaves the file untouched. Every refusal
    goes to stderr so stdout stays exclusively the machine-readable TOML
    block (unlike cmd_promote's interactive stdout narration, this command's
    stdout is designed to be redirected into a manifest).
    """
    try:
        plan = derive_promotion_plan(
            serves, target_name, rollback_name, router_config, rollback_router_config,
        )
    except ValueError as exc:
        print("derived promotion plan refused: %s" % exc, file=sys.stderr)
        return 1
    block = _render_promotion_toml(plan)
    if out is None:
        # TOML is UTF-8 by definition; emit bytes so a console code page
        # (cp1252) can never crash a `> plan.toml` redirect over a
        # non-ASCII path. Fall back to print() for replaced stdouts
        # without a buffer.
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is None:
            print(block, end="")
        else:
            sys.stdout.flush()
            buffer.write(block.encode("utf-8"))
            buffer.flush()
        return 0
    # Exclusive create ("x"), not exists+open: fail loud, never silently
    # clobber (the host.py backup-write precedent), with no TOCTOU window.
    try:
        with open(out, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(block)
    except FileExistsError:
        print("refusing to overwrite existing file: %s" % out, file=sys.stderr)
        return 1
    except OSError as exc:
        print("cannot write %s: %s" % (out, exc), file=sys.stderr)
        return 1
    print("wrote derived promotion plan to %s" % out)
    return 0


_DIRECT_CONFIG_VALIDATOR = (
    "import os,sys,tempfile; "
    "from anvil_serving.router.config import load,load_server_config; "
    "f=tempfile.NamedTemporaryFile(mode='wb',suffix='.toml',delete=False); "
    "f.write(sys.stdin.buffer.read()); f.close(); "
    "load(f.name); load_server_config(f.name); os.unlink(f.name)"
)


def _install_router_config(
    config_file, *, container=DEFAULT_ROUTER_CONTAINER,
    cfg_volume=DEFAULT_ROUTER_CFG_VOLUME, _run=subprocess.run,
):
    """Validate and atomically install one direct config, then restart.

    Returns 0 on success, 1 when the prior config was certainly retained or
    restored, and 4 when the deployed router state is uncertain.
    """
    try:
        with open(config_file, "r", encoding="utf-8") as handle:
            config_text = handle.read().replace("\r\n", "\n").replace("\r", "\n")
    except OSError as exc:
        print("  router config unreadable: %s" % exc)
        return 1

    validate = [
        "docker", "exec", "-i", container, "python", "-c",
        _DIRECT_CONFIG_VALIDATOR,
    ]
    print("  validate direct router config: %s" % config_file)
    try:
        result = _run(
            validate, input=config_text, capture_output=True, text=True,
            encoding="utf-8",
        )
    except FileNotFoundError:
        print("  router config install failed: docker not available")
        return 1
    if result.returncode != 0:
        print("  router config rejected by deployed image: %s" % (
            result.stderr or result.stdout or "validation failed"
        ).strip())
        return 1

    image_result = _run(
        ["docker", "inspect", "-f", "{{.Config.Image}}", container],
        capture_output=True, text=True,
    )
    image = (image_result.stdout or "").strip()
    if image_result.returncode != 0 or not image:
        print("  router config install failed: cannot resolve deployed router image")
        return 1
    mount = cfg_volume + ":" + _ROUTER_CFG_SIDE_MOUNT
    config_path = _ROUTER_CFG_PATH
    backup_path = config_path + ".bak"
    new_path = config_path + ".new"
    backup_script = (
        "if [ -f {cfg} ]; then cp {cfg} {bak}; "
        "else rm -f {bak}; fi"
    ).format(cfg=config_path, bak=backup_path)
    backup = _run(
        ["docker", "run", "--rm", "--user", "0", "-v", mount,
         "--entrypoint", "sh", image, "-c", backup_script],
        capture_output=True, text=True,
    )
    if backup.returncode != 0:
        print("  router config backup failed: %s" % (
            backup.stderr or backup.stdout or "unknown error"
        ).strip())
        return 1
    write_script = "cat > {new} && mv {new} {cfg}".format(
        new=new_path, cfg=config_path
    )
    write = _run(
        ["docker", "run", "--rm", "-i", "--user", "0", "-v", mount,
         "--entrypoint", "sh", image, "-c", write_script],
        input=config_text, capture_output=True, text=True, encoding="utf-8",
    )
    if write.returncode != 0:
        print("  router config write failed: %s" % (
            write.stderr or write.stdout or "unknown error"
        ).strip())
        return 1

    restart = _run(
        ["docker", "restart", container], capture_output=True, text=True
    )
    if restart.returncode == 0:
        return 0

    print("  router restart failed; restoring the previous direct config")
    restore_script = (
        "if [ -f {bak} ]; then mv {bak} {cfg}; "
        "else rm -f {cfg}; fi"
    ).format(bak=backup_path, cfg=config_path)
    restored = _run(
        ["docker", "run", "--rm", "--user", "0", "-v", mount,
         "--entrypoint", "sh", image, "-c", restore_script],
        capture_output=True, text=True,
    )
    recovered = _run(
        ["docker", "restart", container], capture_output=True, text=True
    )
    return 1 if restored.returncode == 0 and recovered.returncode == 0 else 4


def _router_base_url(plan):
    from urllib.parse import urlsplit, urlunsplit

    health_url = runtime_url(
        str(plan.get("router_health_url", "http://127.0.0.1:8000/healthz"))
    )
    parsed = urlsplit(health_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def _transition_cli(router_url, action, tier_id, *, timeout=None, reason=None,
                    _run=subprocess.run):
    """One ADR-0018 router transition step (quiesce/drain/readmit/
    transition-status) through the deployed router's authenticated CLI
    boundary. Shared by promotion plans and reservation eviction — both
    compose the SAME transition; neither grows a second state authority.

    ``reason`` labels a quiesce with its owning transaction (ADR-0033): a
    router with persisted admission intent restores "eviction"/"operator"
    quiescence after a restart but not "promotion", whose transaction owns its
    quiescence end-to-end and re-asserts it on ``--resume``.
    """
    argv = [
        sys.executable, "-m", "anvil_serving.cli", "router", action,
        "--tier", tier_id, "--router-url", router_url,
    ]
    if timeout is not None:
        argv += ["--timeout", str(timeout)]
    if action in ("quiesce", "readmit"):
        argv.append("--confirm")
    if action == "quiesce" and reason:
        argv += ["--reason", reason]
    print("  gate: %s" % " ".join(argv))
    return _run(argv, text=True).returncode


def _promotion_transition_cli(plan, action, tier_id, *, timeout=None, _run=subprocess.run):
    return _transition_cli(
        _router_base_url(plan), action, tier_id, timeout=timeout,
        reason="promotion", _run=_run
    )


def _compensate_quiesce(plan, tier_ids, *, _run=subprocess.run):
    """Idempotently readmit every possibly quiesced tier after a refusal."""
    failed = []
    for tier_id in dict.fromkeys(tier_ids):
        if _promotion_transition_cli(plan, "readmit", tier_id, _run=_run) != 0:
            failed.append(tier_id)
    if failed:
        print(
            "  recovery: admission remains fail-closed for %s; use --resume "
            "after router readiness recovers" % ", ".join(failed)
        )
        return False
    return True


def _serve_identity_ready(serve, *, _open=urllib.request.urlopen, max_bytes=65536):
    from urllib.parse import urlunsplit

    url = urlunsplit(("http", "127.0.0.1:%s" % serve["port"], "/v1/models", "", ""))
    request = urllib.request.Request(runtime_url(url), headers={"Accept": "application/json"})
    try:
        with _open(request, timeout=5) as response:
            raw = response.read(max_bytes + 1)
    except Exception:
        return False
    if len(raw) > max_bytes:
        return False
    try:
        data = json.loads(raw).get("data")
        ids = [item.get("id") for item in data if isinstance(item, dict)]
    except Exception:
        return False
    return ids == [serve["served_name"]]


def _await_healthy(serve, timeout, poll_interval, *, _open=urllib.request.urlopen,
                   _sleep=time.sleep):
    deadline = time.monotonic() + timeout
    while True:
        if _health(serve["port"], serve["health"], _open=_open) == 200:
            return True
        if time.monotonic() >= deadline:
            return False
        _sleep(min(poll_interval, max(0, deadline - time.monotonic())))


def _await_cache_reclaim_targets(serves, timeout=LIFECYCLE_READINESS_TIMEOUT_SECONDS,
                                 poll_interval=LIFECYCLE_READINESS_POLL_SECONDS, *,
                                 _open=urllib.request.urlopen, _sleep=time.sleep):
    """Wait for every selected manifest serve's declared HTTP health."""
    deadline = time.monotonic() + timeout
    while True:
        if all(
            _health(serve["port"], serve["health"], _open=_open) == 200
            for serve in serves
        ):
            return True
        if time.monotonic() >= deadline:
            return False
        _sleep(min(poll_interval, max(0, deadline - time.monotonic())))


def _finish_cache_reclaim(rc, policy, before, operation, *, dry_run=False,
                          readiness_targets=None):
    """Run the best-effort postcondition without changing the parent exit code."""
    if rc != 0 or dry_run or policy is None or not policy["enabled"]:
        return rc
    readiness = True
    if (
        readiness_targets is not None
        and host_ops.cache_reclaim_is_active(policy)
        and before is not None
    ):
        readiness = _await_cache_reclaim_targets(readiness_targets)
    result = host_ops.automatic_cache_reclaim(
        policy, before, operation=operation, readiness=readiness,
    )
    host_ops.render_cache_reclaim_result(result)
    return rc


def _promotion_cli(argv, *, _run=subprocess.run):
    command = [sys.executable, "-m", "anvil_serving.cli", *argv, "--confirm"]
    print("  gate: %s" % " ".join(command))
    result = _run(command, text=True)
    return result.returncode


def _gateway_status(url, *, _open=urllib.request.urlopen):
    try:
        with _open(runtime_url(url), timeout=5) as response:
            return getattr(response, "status", None) or response.getcode()
    except urllib.error.HTTPError as exc:
        return exc.code  # auth failures still prove the router is reachable
    except Exception:
        return None


def _await_gateway(url, timeout, poll_interval, *, _open=urllib.request.urlopen,
                   _sleep=time.sleep):
    """Wait for the restarted router to expose its health endpoint."""
    deadline = time.monotonic() + timeout
    while True:
        status = _gateway_status(url, _open=_open)
        if status == 200:
            return status
        if time.monotonic() >= deadline:
            return status
        _sleep(min(poll_interval, max(0, deadline - time.monotonic())))


def _promotion_transition(serves, plan, manifest_path, *, rollback=False,
                          dry_run=False, require_candidate=True, resume=False,
                          _run=subprocess.run,
                          _open=urllib.request.urlopen, _sleep=time.sleep):
    target = _exact_serve(serves, plan["rollback"] if rollback else plan["target"])
    displaced = _exact_serve(serves, plan["target"] if rollback else plan["rollback"])
    candidate = _exact_serve(serves, plan["candidate"]) if plan.get("candidate") else None
    label = "rollback" if rollback else "promotion"
    if candidate is not None and not rollback and not dry_run and require_candidate:
        if docker_state(candidate["container"], _run=_run) != "running" or _health(
            candidate["port"], candidate["health"], _open=_open
        ) != 200:
            print("  promotion refused: candidate %s is not running and healthy" % candidate["name"])
            return 2

    stop_names = [displaced["name"]]
    if candidate is not None and not rollback:
        stop_names.insert(0, candidate["name"])
    gates = plan["rollback_gate" if rollback else "gate"]
    selected_config = plan[
        "rollback_router_config" if rollback else "router_config"
    ]
    print("  %s plan: stop %s; start %s; %d preflight gate(s); install %s" % (
        label, ", ".join(stop_names), target["name"], len(gates),
        os.path.basename(selected_config)))
    if dry_run:
        for tier_id in plan["affected_tiers"]:
            print("  gate: quiesce router tier %s" % tier_id)
            print("  gate: drain router tier %s (timeout %ss)" % (
                tier_id, plan["drain_timeout"]))
        cmd_down(
            serves, stop_names, dry_run=True, keep_container=True, _run=_run
        )
        cmd_up(serves, [target["name"]], dry_run=True, recreate=True, _run=_run)
        print("  gate: exact served-model identity for %s" % target["served_name"])
        for gate in gates:
            print("  gate %s: eval preflight --tier %s --checks %s --thinking-mode %s "
                  "--visible-answer-tokens %s --reasoning-headroom-tokens %s" % (
                      gate["name"], target["name"], gate["checks"], gate["thinking_mode"],
                      gate["visible_answer_tokens"], gate["reasoning_headroom_tokens"]))
        print("  apply: atomically install %s and restart the router" % selected_config)
        print("  verify: router gateway is reachable after the serve swap")
        print("  verify: post-restart health and model identity for %s" % (
            ", ".join(plan["affected_tiers"])))
        return 0

    quiesced = []
    for tier_id in plan["affected_tiers"]:
        if _promotion_transition_cli(
            plan, "quiesce", tier_id, _run=_run
        ) != 0:
            print("  %s refused: failed to quiesce %s" % (label, tier_id))
            # The router may have applied quiescence before its response was
            # lost. Compensate the current tier as well as earlier successes.
            return 2 if _compensate_quiesce(
                plan, [*quiesced, tier_id], _run=_run
            ) else 3
        quiesced.append(tier_id)
    for tier_id in plan["affected_tiers"]:
        if _promotion_transition_cli(
            plan, "drain", tier_id, timeout=plan["drain_timeout"], _run=_run
        ) != 0:
            print("  %s refused: drain timed out for %s before container mutation" % (
                label, tier_id))
            return 2 if _compensate_quiesce(
                plan, quiesced, _run=_run
            ) else 3

    # Preserve displaced/candidate containers and their logs until the
    # promotion transaction and its rollback opportunity complete.
    if cmd_down(serves, stop_names, keep_container=True, _run=_run) != 0:
        return 1
    target_state = docker_state(target["container"], _run=_run)
    reuse_target = (
        resume
        and target_state == "running"
        and _health(target["port"], target["health"], _open=_open) == 200
        and _serve_identity_ready(target, _open=_open)
    )
    if reuse_target:
        print("  resume: %s is already healthy with exact model identity" % target["name"])
    elif cmd_up(serves, [target["name"]], recreate=True, _run=_run) != 0:
        return 1
    startup_timeout = plan["rollback_startup_timeout"] if rollback else plan["startup_timeout"]
    if not _await_healthy(target, startup_timeout, plan["poll_interval"],
                          _open=_open, _sleep=_sleep):
        print("  %s failed: %s did not become healthy" % (label, target["name"]))
        return 1
    if not _serve_identity_ready(target, _open=_open):
        print("  %s failed: %s did not advertise the exact configured model" % (
            label, target["name"]))
        return 1
    for gate in gates:
        preflight = [
            "eval", "preflight", "--tier", target["name"], "--manifest", manifest_path,
            "--needle-ctx", str(plan["needle_ctx"]), "--tool-batch", str(plan["tool_batch"]),
            "--checks", str(gate["checks"]), "--thinking-mode", str(gate["thinking_mode"]),
            "--visible-answer-tokens", str(gate["visible_answer_tokens"]),
            "--reasoning-headroom-tokens", str(gate["reasoning_headroom_tokens"]),
            "--reasoning-evidence", str(gate["reasoning_evidence"]),
        ]
        if gate.get("reasoning_effort"):
            preflight.extend(["--reasoning-effort", str(gate["reasoning_effort"])])
        if gate.get("json_out"):
            preflight.extend(["--json-out", str(gate["json_out"])])
        if _promotion_cli(preflight, _run=_run) != 0:
            print("  %s failed: preflight gate %s rejected %s" % (
                label, gate["name"], target["name"]))
            return 1
    config_rc = _install_router_config(selected_config, _run=_run)
    if config_rc != 0:
        print("  %s failed: direct router config was not installed" % label)
        return config_rc
    gateway_url = str(plan.get("router_health_url", "http://127.0.0.1:8000/healthz"))
    status = _await_gateway(
        gateway_url,
        min(60, startup_timeout),
        plan["poll_interval"],
        _open=_open,
        _sleep=_sleep,
    )
    if status != 200:
        print("  %s failed: router health gate returned HTTP %s" % (label, status))
        return 1
    print("  router gateway reachable after reload (HTTP %s)" % status)
    # The restart intentionally discards process-local quiescence.  Ordinary
    # health+identity readiness in the new router is the fail-closed guard.
    for tier_id in plan["affected_tiers"]:
        if _promotion_transition_cli(
            plan, "transition-status", tier_id, _run=_run
        ) != 0:
            print("  %s failed: post-restart readiness rejected %s" % (
                label, tier_id))
            return 1
    return 0


def _cmd_promote_unlocked(serves, promotions, name, manifest_path, *, rollback=False,
                          resume=False, dry_run=False, _run=subprocess.run,
                          _open=urllib.request.urlopen, _sleep=time.sleep):
    """Atomically promote a staged model recipe or restore its complete rollback state."""
    matches = [plan for plan in promotions if plan["name"] == name]
    if len(matches) != 1:
        print("promotion %r must match exactly one [[promotion]] plan" % name)
        return 1
    plan = matches[0]
    try:
        for field in ("target", "rollback"):
            _exact_serve(serves, plan[field])
        if plan.get("candidate"):
            _exact_serve(serves, plan["candidate"])
        _validate_promotion_topology(serves, plan)
    except ValueError as exc:
        print("promotion refused: %s" % exc)
        return 1
    try:
        rc = _promotion_transition(
            serves, plan, manifest_path, rollback=rollback, dry_run=dry_run,
            require_candidate=not resume, resume=resume,
            _run=_run, _open=_open, _sleep=_sleep,
        )
    except Exception as exc:
        print("promotion transition failed: %s" % exc)
        rc = 1
    if rc == 0 or dry_run:
        return rc
    if rc == 2:  # refused before the first mutation; nothing needs restoring
        return 1
    if rc == 3:
        # No container mutation occurred, but the router's admission state is
        # uncertain. Do not compound that uncertainty with an automatic swap.
        print("  CRITICAL: pre-mutation admission compensation failed; no containers changed")
        return 1
    if rc == 4:
        # Container state may already have changed, but the router's exact
        # deployed artifacts are unknown. Starting the opposite transition
        # could compound the split-brain, so stop and require inspection.
        print(
            "  CRITICAL: router state is uncertain; automatic container recovery blocked"
        )
        return 1
    if rollback:
        print("  rollback gate failed; restoring the promoted serve and router state")
        try:
            recover_rc = _promotion_transition(
                serves, plan, manifest_path, rollback=False, require_candidate=False,
                _run=_run, _open=_open, _sleep=_sleep,
            )
        except Exception as exc:
            print("  promoted-state recovery raised: %s" % exc)
            recover_rc = 1
        if recover_rc != 0:
            print("  CRITICAL: rollback and promoted-state recovery both failed")
        return 1
    print("  promotion gate failed; restoring serve and router rollback state")
    try:
        rollback_rc = _promotion_transition(
            serves, plan, manifest_path, rollback=True, _run=_run, _open=_open, _sleep=_sleep,
        )
    except Exception as exc:
        print("  automatic rollback raised: %s" % exc)
        rollback_rc = 1
    if rollback_rc != 0:
        print("  CRITICAL: automatic rollback failed; inspect serves status and router artifacts")
    return 1


def _compose_service_for_recipe(serve, recipe, activation, *, _run=subprocess.run):
    """Resolve Compose and prove its effective service exactly matches the recipe."""
    up = serve.get("up") or []
    try:
        compose_index = up.index("compose")
        up_index = up.index("up", compose_index + 1)
    except ValueError as exc:
        raise serve_recipes.RecipeError(
            "activation-ready serve %r must use a docker compose up command" % serve["name"]
        ) from exc
    service_name = activation["compose_service"]
    requested_services = [token for token in up[up_index + 1:] if not token.startswith("-")]
    if requested_services != [service_name]:
        raise serve_recipes.RecipeError(
            "activation compose_service %r does not match manifest up target %r" % (
                service_name, requested_services,
            )
        )
    command = [*up[:up_index], "config", "--format", "json"]
    try:
        completed = _run(command, capture_output=True, text=True)
    except OSError as exc:
        raise serve_recipes.RecipeError("cannot resolve effective Compose configuration: %s" % exc) from exc
    if completed.returncode != 0:
        raise serve_recipes.RecipeError(
            "cannot resolve effective Compose configuration: %s" % (
                (completed.stderr or completed.stdout or "unknown docker compose error").strip()
            )
        )
    try:
        service = json.loads(completed.stdout)["services"][service_name]
        if not isinstance(service, dict):
            raise TypeError("service must be an object")
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise serve_recipes.RecipeError(
            "docker compose config did not contain service %r" % service_name
        ) from exc
    hash_command = [*up[:up_index], "config", "--hash", service_name]
    try:
        hash_result = _run(hash_command, capture_output=True, text=True)
    except OSError as exc:
        raise serve_recipes.RecipeError(
            "cannot resolve effective Compose service hash: %s" % exc
        ) from exc
    hash_parts = (hash_result.stdout or "").strip().split()
    if (
        hash_result.returncode != 0
        or len(hash_parts) != 2
        or hash_parts[0] != service_name
        or not re.fullmatch(r"[0-9a-fA-F]{64}", hash_parts[1])
    ):
        raise serve_recipes.RecipeError(
            "cannot resolve effective Compose service hash: %s" % (
                (hash_result.stderr or hash_result.stdout or "invalid docker compose hash").strip()
            )
        )
    compose_hash = hash_parts[1].lower()

    recipe_serve = recipe.get("serve") or {}
    expected_command = ["serve", recipe["model"]]
    for flag in recipe_serve.get("flags", []):
        try:
            expected_command.extend(shlex.split(flag))
        except ValueError as exc:
            raise serve_recipes.RecipeError("cannot parse recipe flag %r: %s" % (flag, exc)) from exc
    checks = {
        "image": (service.get("image"), recipe_serve.get("image")),
        "container_name": (service.get("container_name"), serve["container"]),
        "command": (service.get("command"), expected_command),
    }
    for field, (actual, expected) in checks.items():
        if actual != expected:
            raise serve_recipes.RecipeError(
                "effective Compose %s for %r does not match recipe (actual=%r, expected=%r)"
                % (field, service_name, actual, expected)
            )
    environment = service.get("environment") or {}
    for item in recipe_serve.get("env", []):
        name, separator, value = item.partition("=")
        if not separator or environment.get(name) != value:
            raise serve_recipes.RecipeError(
                "effective Compose environment %s for %r does not match recipe" % (
                    name, service_name,
                )
            )
    gpu_uuid = (recipe.get("hardware") or {}).get("gpu_uuid")
    if gpu_uuid:
        devices = (
            service.get("deploy", {}).get("resources", {}).get("reservations", {})
            .get("devices", [])
        )
        device_ids = [
            device_id
            for device in devices if isinstance(device, dict)
            for device_id in (device.get("device_ids") or [])
        ]
        if device_ids != [gpu_uuid]:
            raise serve_recipes.RecipeError(
                "effective Compose GPU assignment for %r does not match recipe" % service_name
            )
    port = recipe_serve.get("port")
    ports = service.get("ports") or []
    normalized_ports = sorted(
        (
            str(item.get("host_ip") or ""),
            int(item.get("target", -1)),
            str(item.get("published")),
            str(item.get("protocol") or "tcp"),
        )
        for item in ports if isinstance(item, dict)
    )
    expected_ports = [] if port is None else [("127.0.0.1", port, str(port), "tcp")]
    if normalized_ports != expected_ports:
        raise serve_recipes.RecipeError(
            "effective Compose ports for %r must be exactly the reviewed loopback binding %r"
            % (service_name, expected_ports)
        )
    scrubbed = json.loads(json.dumps(service))
    for name in list((scrubbed.get("environment") or {})):
        if any(marker in name.upper() for marker in ("TOKEN", "KEY", "SECRET", "PASSWORD")):
            scrubbed["environment"][name] = "<redacted>"
    payload = json.dumps(scrubbed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    shm_size = service.get("shm_size")
    if isinstance(shm_size, str) and shm_size.isdigit():
        shm_size = int(shm_size)
    sensitive_environment = any(
        any(marker in name.upper() for marker in ("TOKEN", "KEY", "SECRET", "PASSWORD"))
        for name in environment
    )
    contract = {
        "service": service_name,
        "compose_hash": compose_hash,
        "compose_hash_verifiable": not sensitive_environment,
        "cap_add": sorted(service.get("cap_add") or []),
        "cap_drop": sorted(service.get("cap_drop") or []),
        "devices": service.get("devices") or [],
        "entrypoint": service.get("entrypoint") if "entrypoint" in service else None,
        "environment": {
            name: value for name, value in environment.items()
            if not any(
                marker in name.upper()
                for marker in ("TOKEN", "KEY", "SECRET", "PASSWORD")
            )
        },
        "ipc": service.get("ipc"),
        "network_mode": service.get("network_mode") if "network_mode" in service else None,
        "pid": service.get("pid") or "",
        "ports": normalized_ports,
        "privileged": bool(service.get("privileged", False)),
        "read_only": bool(service.get("read_only", False)),
        "restart": service.get("restart"),
        "security_opt": (
            sorted(service["security_opt"]) if "security_opt" in service else None
        ),
        "shm_size": shm_size,
        "user": service.get("user") if "user" in service else None,
        "sysctls": service.get("sysctls") or {},
        "ulimits": service.get("ulimits") or {},
        "uts": service.get("uts") or "",
        "volumes": service.get("volumes") or [],
        "working_dir": service.get("working_dir") if "working_dir" in service else None,
    }
    return {
        "fingerprint": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "contract": contract,
    }


def _running_container_matches_recipe(serve, recipe, deployment, *, _run=subprocess.run):
    """Verify the live container's immutable launch inputs against the recipe."""
    try:
        result = _run(["docker", "inspect", serve["container"]], capture_output=True, text=True)
    except OSError:
        return False
    if result.returncode != 0:
        return False
    try:
        documents = json.loads(result.stdout)
        inspect = documents[0]
        config = inspect["Config"]
        host = inspect["HostConfig"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return False
    recipe_serve = recipe.get("serve") or {}
    expected_command = ["serve", recipe["model"]]
    for flag in recipe_serve.get("flags", []):
        try:
            expected_command.extend(shlex.split(flag))
        except ValueError:
            return False
    if config.get("Image") != recipe_serve.get("image") or config.get("Cmd") != expected_command:
        return False
    contract = deployment.get("contract") if isinstance(deployment, dict) else None
    if not isinstance(contract, dict):
        return False
    labels = config.get("Labels") or {}
    if labels.get("com.docker.compose.service") != contract["service"]:
        return False
    if contract["compose_hash_verifiable"] and (
        labels.get("com.docker.compose.config-hash") != contract["compose_hash"]
    ):
        return False
    if contract["entrypoint"] is not None and (
        config.get("Entrypoint") or []
    ) != contract["entrypoint"]:
        return False
    if contract["user"] is not None and (config.get("User") or "") != contract["user"]:
        return False
    if contract["working_dir"] is not None and (
        config.get("WorkingDir") or ""
    ) != contract["working_dir"]:
        return False
    host_checks = {
        "IpcMode": contract["ipc"] or "private",
        "PidMode": contract["pid"],
        "Privileged": contract["privileged"],
        "ReadonlyRootfs": contract["read_only"],
        "UTSMode": contract["uts"],
    }
    if any(host.get(name) != expected for name, expected in host_checks.items()):
        return False
    if sorted(host.get("CapAdd") or []) != contract["cap_add"]:
        return False
    if sorted(host.get("CapDrop") or []) != contract["cap_drop"]:
        return False
    if (host.get("Sysctls") or {}) != contract["sysctls"]:
        return False
    if contract["network_mode"] is not None and (
        host.get("NetworkMode") != contract["network_mode"]
    ):
        return False
    if contract["devices"]:
        expected_devices = sorted(
            (
                str(item.get("source", "")),
                str(item.get("target", "")),
                str(item.get("permissions", "rwm")),
            )
            for item in contract["devices"] if isinstance(item, dict)
        )
        actual_devices = sorted(
            (
                str(item.get("PathOnHost", "")),
                str(item.get("PathInContainer", "")),
                str(item.get("CgroupPermissions", "rwm")),
            )
            for item in (host.get("Devices") or []) if isinstance(item, dict)
        )
        if actual_devices != expected_devices:
            return False
    elif host.get("Devices") not in (None, []):
        return False
    expected_ulimits = {}
    for name, value in contract["ulimits"].items():
        if isinstance(value, dict):
            expected_ulimits[name] = (
                int(value.get("soft", value.get("hard", 0))),
                int(value.get("hard", value.get("soft", 0))),
            )
        else:
            expected_ulimits[name] = (int(value), int(value))
    actual_ulimits = {
        str(item.get("Name")): (int(item.get("Soft", 0)), int(item.get("Hard", 0)))
        for item in (host.get("Ulimits") or []) if isinstance(item, dict)
    }
    if actual_ulimits != expected_ulimits:
        return False
    if contract["security_opt"] is not None and sorted(
        host.get("SecurityOpt") or []
    ) != contract["security_opt"]:
        return False
    restart = (host.get("RestartPolicy") or {}).get("Name") or "no"
    if restart != (contract["restart"] or "no"):
        return False
    if contract["shm_size"] is not None:
        try:
            expected_shm_size = int(contract["shm_size"])
        except (TypeError, ValueError):
            return False
        if host.get("ShmSize") != expected_shm_size:
            return False
    environment = {}
    for item in config.get("Env") or []:
        name, separator, value = str(item).partition("=")
        if separator:
            environment[name] = value
    for item in recipe_serve.get("env", []):
        name, _, value = item.partition("=")
        if environment.get(name) != value:
            return False
    for name, value in contract["environment"].items():
        if environment.get(name) != str(value):
            return False
    expected_mounts = []
    for item in contract["volumes"]:
        if not isinstance(item, dict):
            return False
        expected_mounts.append({
            "type": item.get("type", "volume"),
            "source": item.get("source"),
            "target": item.get("target"),
            "read_only": bool(item.get("read_only", False)),
        })
    actual_mounts = inspect.get("Mounts") or []
    if len(actual_mounts) != len(expected_mounts):
        return False
    for expected in expected_mounts:
        matches = [
            mount for mount in actual_mounts
            if isinstance(mount, dict)
            and str(mount.get("Type", "")).lower() == expected["type"]
            and mount.get("Destination") == expected["target"]
            and bool(not mount.get("RW", True)) == expected["read_only"]
        ]
        if len(matches) != 1:
            return False
        actual_source = matches[0].get("Name") or matches[0].get("Source")
        expected_source = expected["source"]
        if expected_source and expected["type"] == "bind" and actual_source != expected_source:
            return False
        if expected_source and expected["type"] == "volume" and not (
            actual_source == expected_source
            or str(actual_source).endswith("_" + str(expected_source))
        ):
            return False
    gpu_uuid = (recipe.get("hardware") or {}).get("gpu_uuid")
    if gpu_uuid:
        device_ids = [
            device_id
            for request in (host.get("DeviceRequests") or []) if isinstance(request, dict)
            for device_id in (request.get("DeviceIDs") or [])
        ]
        if device_ids != [gpu_uuid]:
            return False
    try:
        actual_ports = sorted(
            (
                str(binding.get("HostIp") or ""),
                int(str(container_port).split("/", 1)[0]),
                str(binding.get("HostPort")),
                str(container_port).split("/", 1)[1]
                if "/" in str(container_port) else "tcp",
            )
            for container_port, bindings in (host.get("PortBindings") or {}).items()
            for binding in (bindings or []) if isinstance(binding, dict)
        )
    except (TypeError, ValueError):
        return False
    if actual_ports != [tuple(item) for item in contract["ports"]]:
        return False
    return True


@contextmanager
def _switch_role_lock(role):
    """Hold one non-blocking, cross-platform lock for a deployment role."""
    lock_dir = config_path("locks")
    os.makedirs(lock_dir, exist_ok=True)
    path = os.path.join(lock_dir, "serves-switch-%s.lock" % role)
    handle = open(path, "a+b")
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
                raise RuntimeError("another switch is already active for role %r" % role) from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("another switch is already active for role %r" % role) from exc
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


def _write_switch_journal(path, document):
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".switch-", suffix=".json", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def cmd_promote(serves, promotions, name, manifest_path, *, rollback=False,
                resume=False, dry_run=False, _run=subprocess.run, _open=urllib.request.urlopen,
                _sleep=time.sleep):
    """Run the common promotion transaction under the global promotion lock."""
    # Promotion plans may overlap on only some affected tiers. A lock derived
    # from the complete tier set would let partially overlapping plans race, so
    # all live promotions share one short, explicit transaction lock.
    lock = nullcontext() if dry_run else _switch_role_lock("promotion")
    try:
        with lock:
            result = _cmd_promote_unlocked(
                serves, promotions, name, manifest_path,
                rollback=rollback, resume=resume, dry_run=dry_run,
                _run=_run, _open=_open, _sleep=_sleep,
            )
            if result == 0 and not dry_run:
                plan = next(item for item in promotions if item["name"] == name)
                target_name = plan["rollback"] if rollback else plan["target"]
                rollback_name = plan["target"] if rollback else plan["rollback"]
                target = _exact_serve(serves, target_name)
                rollback_serve = _exact_serve(serves, rollback_name)
                base_payload = {
                    "promotion": name,
                    "model": target.get("served_name", target.get("model")),
                }
                if rollback:
                    base_payload["restored_model"] = base_payload.pop("model")
                else:
                    base_payload["context"] = plan.get("needle_ctx")
                    base_payload["rollback"] = rollback_serve.get(
                        "served_name", rollback_serve.get("model")
                    )
                for tier in plan["affected_tiers"]:
                    emit_lifecycle_event(
                        "promote.rolled_back" if rollback else "promote.applied",
                        {**base_payload, "tier": tier},
                    )
            return result
    except LifecycleEventError as exc:
        print(
            "promotion applied but lifecycle event was not recorded: %s" % exc,
            file=sys.stderr,
        )
        return 1
    except RuntimeError as exc:
        print("promotion refused: %s" % exc, file=sys.stderr)
        return 1


def _operation_promotion(promotions, plan_name, role, recipe, rollback,
                         deployment_fingerprint, manifest_path, dry_run):
    operation_id = "%s-%s-%s" % (time.time_ns(), os.getpid(), role)
    operation_dir = config_path("operations", operation_id)
    selected = copy.deepcopy(next(item for item in promotions if item["name"] == plan_name))
    for group in ("gate", "rollback_gate"):
        for index, gate in enumerate(selected.get(group, []), 1):
            safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(gate["name"])).strip("-")
            gate["json_out"] = os.path.join(
                operation_dir, "%s-%02d-%s.json" % (group, index, safe_name or "gate"),
            )
    operation = {
        "schema": "anvil-serving.serves-switch-operation/v1",
        "operation_id": operation_id,
        "role": role,
        "recipe": recipe["model"],
        "promotion": plan_name,
        "direction": "rollback" if rollback else "promote",
        "deployment_fingerprint": deployment_fingerprint,
        "manifest": os.path.abspath(manifest_path),
        "status": "preview" if dry_run else "planned",
        "evidence_dir": operation_dir,
    }
    replaced = [selected if item["name"] == plan_name else item for item in promotions]
    return replaced, selected, operation, os.path.join(operation_dir, "journal.json")


def resolve_recipe_activation(serves, promotions, registry, role, selector, *,
                              _run=subprocess.run):
    """Resolve one recipe's role activation to a proven promotion direction."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", role):
        raise serve_recipes.RecipeError(
            "deployment role must use only letters, digits, '.', '_', or '-'"
        )
    recipe = serve_recipes.find_recipe(registry, selector)
    if recipe is None:
        raise serve_recipes.RecipeError("no serve recipe for %r" % selector)
    serve_recipes.validate_recipe(recipe)
    activation = (recipe.get("activation") or {}).get(role)
    if not isinstance(activation, dict):
        raise serve_recipes.RecipeError(
            "recipe %r is not activation-ready for role %r; add "
            "[recipe.activation.%s] with plan and direction" % (
                recipe["model"], role, role,
            )
        )
    plan_name = activation["plan"]
    direction = activation["direction"]
    matching_plans = [plan for plan in promotions if plan["name"] == plan_name]
    if len(matching_plans) != 1:
        raise serve_recipes.RecipeError(
            "activation plan %r must match exactly one [[promotion]] entry" % plan_name
        )
    plan = matching_plans[0]
    manifest_name = plan["target" if direction == "promote" else "rollback"]
    selected_serve = _exact_serve(serves, manifest_name)
    serve = recipe.get("serve") or {}
    managed_serve = serve.get("managed_serve")
    served_model_name = serve.get("served_model_name")
    if managed_serve != selected_serve["name"]:
        raise serve_recipes.RecipeError(
            "recipe %r declares managed_serve %r, but %s direction of plan %r "
            "selects %r" % (
                recipe["model"], managed_serve, direction, plan_name,
                selected_serve["name"],
            )
        )
    if served_model_name != selected_serve["served_name"]:
        raise serve_recipes.RecipeError(
            "recipe %r declares served_model_name %r, but manifest serve %r "
            "advertises %r" % (
                recipe["model"], served_model_name, selected_serve["name"],
                selected_serve["served_name"],
            )
        )
    _validate_promotion_topology(serves, plan)
    deployment = _compose_service_for_recipe(
        selected_serve, recipe, activation, _run=_run,
    )
    return recipe, plan_name, direction == "rollback", deployment


def cmd_switch(serves, promotions, registry, role, selector, manifest_path, *,
               dry_run=False, _run=subprocess.run,
               _open=urllib.request.urlopen, _sleep=time.sleep):
    """Switch a deployment role to an activation-ready recipe."""
    try:
        recipe, plan_name, rollback, deployment = resolve_recipe_activation(
            serves, promotions, registry, role, selector, _run=_run,
        )
    except (serve_recipes.RecipeError, OSError, KeyError, TypeError, ValueError) as exc:
        print(
            "switch refused: %s; run `anvil-serving serves switch %s` to list choices"
            % (exc, role),
            file=sys.stderr,
        )
        return 2
    direction = "rollback" if rollback else "promote"
    print("switch %s -> %s (%s plan %s)" % (
        role, recipe["model"], direction, plan_name,
    ))
    print("  effective deployment: %s" % deployment["fingerprint"])
    promotions, plan, operation, journal_path = _operation_promotion(
        promotions, plan_name, role, recipe, rollback, deployment["fingerprint"],
        manifest_path, dry_run,
    )
    operation["registry_sha256"] = "sha256:" + hashlib.sha256(
        json.dumps(registry, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    prefix = "planned " if dry_run else ""
    print("  %soperation: %s" % (prefix, operation["operation_id"]))
    print("  %sevidence: %s" % (prefix, operation["evidence_dir"]))
    role_lock = nullcontext() if dry_run else _switch_role_lock(role)
    promotion_lock = nullcontext() if dry_run else _switch_role_lock("promotion")
    mutation_started = False
    try:
        with role_lock, promotion_lock:
            selected_serve = _exact_serve(
                serves, plan["rollback" if rollback else "target"],
            )
            rebound = _compose_service_for_recipe(
                selected_serve, recipe, recipe["activation"][role], _run=_run,
            )
            if rebound != deployment:
                print(
                    "switch refused: effective Compose configuration changed after preview",
                    file=sys.stderr,
                )
                return 2
            _validate_promotion_topology(serves, plan)
            if not dry_run:
                operation["manifest_sha256"] = serve_recipes.registry_digest(manifest_path)
            target_name = plan["rollback" if rollback else "target"]
            target = _exact_serve(serves, target_name)
            state = docker_state(target["container"], _run=_run)
            if state == "running" and _health(
                target["port"], target["health"], _open=_open
            ) == 200 and _serve_identity_ready(target, _open=_open) \
                    and _running_container_matches_recipe(
                        target, recipe, deployment, _run=_run
                    ):
                print("  already active: direct tier, container health, and exact model identity match")
                return 0
            if not dry_run:
                operation["status"] = "running"
                _write_switch_journal(journal_path, operation)
                mutation_started = True
            rc = _cmd_promote_unlocked(
                serves, promotions, plan_name, manifest_path,
                rollback=rollback, resume=False, dry_run=dry_run,
                _run=_run, _open=_open, _sleep=_sleep,
            )
            if not dry_run:
                operation["status"] = "complete" if rc == 0 else "failed"
                operation["exit_code"] = rc
                _write_switch_journal(journal_path, operation)
            if rc == 0 and not dry_run and not _running_container_matches_recipe(
                target, recipe, deployment, _run=_run,
            ):
                print(
                    "switch failed: running container no longer matches the selected recipe; "
                    "restoring the prior state",
                    file=sys.stderr,
                )
                recovery = _cmd_promote_unlocked(
                    serves, promotions, plan_name, manifest_path,
                    rollback=not rollback, resume=False, dry_run=False,
                    _run=_run, _open=_open, _sleep=_sleep,
                )
                operation["status"] = "failed"
                operation["exit_code"] = 1
                operation["recovery_exit_code"] = recovery
                _write_switch_journal(journal_path, operation)
                return 1
            return rc
    except Exception as exc:
        if mutation_started:
            print(
                "switch failed after mutation began: %s; inspect %s" % (
                    exc, journal_path,
                ),
                file=sys.stderr,
            )
            return 1
        print("switch refused: %s" % exc, file=sys.stderr)
        return 2


def cmd_switch_choices(serves, promotions, registry, role, registry_path, *,
                       _run=subprocess.run):
    """List and validate recipes that declare activation for one deployment role."""
    available_roles = sorted({
        candidate_role
        for recipe in registry.get("recipe", []) if isinstance(recipe, dict)
        for candidate_role in (recipe.get("activation") or {})
    })
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", role) or role not in available_roles:
        print(
            "unknown deployment role %r (available: %s)" % (
                role, ", ".join(available_roles) or "none",
            ),
            file=sys.stderr,
        )
        return 2
    rows = []
    for recipe in registry.get("recipe", []):
        activation = (recipe.get("activation") or {}).get(role)
        if isinstance(activation, dict):
            try:
                resolve_recipe_activation(
                    serves, promotions, registry, role, recipe["model"], _run=_run,
                )
                readiness, detail = "ready", "-"
            except (serve_recipes.RecipeError, OSError, KeyError, TypeError, ValueError) as exc:
                readiness = "blocked"
                detail = str(exc).replace("\n", " ")[:120]
            rows.append((
                recipe["model"], recipe.get("status", "-"), readiness,
                activation, detail,
            ))
    print("recipe registry: %s" % os.path.abspath(os.path.expanduser(registry_path)))
    if not rows:
        print("no recipes declare activation for role %r" % role)
        return 0
    print("%-52s %-10s %-9s %-10s %-22s %s" % (
        "MODEL", "STATUS", "ACTIVATE", "DIRECTION", "PLAN", "DETAIL",
    ))
    for model, status, readiness, activation, detail in rows:
        print("%-52s %-10s %-9s %-10s %-22s %s" % (
            model, status, readiness, activation["direction"], activation["plan"], detail,
        ))
    return 0


def _select(serves, names):
    """Serves matching any of `names` (by name or container); all if empty."""
    if not names:
        return list(serves)
    want = set(names)
    return [s for s in serves if s["name"] in want or s["container"] in want]


def _serving_path_scope(serves, selected=()):
    """Return authored serving-path entries plus explicitly selected opt-ins.

    A non-empty ``groups`` list is the manifest's declaration that a serve is
    part of an operator-supported serving path. Untagged experiment and
    candidate rows stay available by explicit name, but bare status must not
    poll them. Explicitly selected rows are appended in manifest order without
    duplicating a serving-path entry.
    """
    selected_containers = {s["container"] for s in selected}
    return [
        s for s in serves
        if s.get("groups") or s["container"] in selected_containers
    ]


def _run_or(argv, default, _run=subprocess.run, **kwargs):
    """Run argv, returning `default` when the executable is missing.

    Isolates the `except FileNotFoundError: return <sentinel>` pattern that
    recurs around docker/nvidia-smi probes. Callers still handle a non-zero
    returncode themselves.
    """
    try:
        return _run(argv, **kwargs)
    except FileNotFoundError:
        return default


def docker_state(container, _run=subprocess.run):
    """Container state, distinguishing genuine absence from a docker error.

    Returns the raw docker status (running/exited/created/paused/restarting/...),
    or 'absent' (no such container), or 'error' (docker missing / daemon down /
    permission denied — i.e. we could NOT determine state, so callers must not
    claim success).
    """
    r = _run_or(["docker", "inspect", "-f", "{{.State.Status}}", container],
                None, _run, capture_output=True, text=True)
    if r is None:
        return "error"  # docker not installed -> cannot manage containers
    if r.returncode != 0:
        return "absent" if "no such" in (r.stderr or "").lower() else "error"
    return (r.stdout or "").strip() or "unknown"


def _docker_ps_lines(_run=subprocess.run):
    """Stdout lines of `docker ps -a --format {{json .}}`, or ``None`` on
    failure (docker missing or non-zero exit) — callers apply their own
    failure default.
    """
    result = _run_or(
        ["docker", "ps", "-a", "--format", "{{json .}}"],
        None, _run, capture_output=True, text=True,
    )
    if result is None or result.returncode != 0:
        return None
    return (result.stdout or "").splitlines()


def docker_states(containers, _run=subprocess.run):
    """Resolve many named container states with one fail-closed Docker query."""
    wanted = list(dict.fromkeys(str(container) for container in containers))
    if not wanted:
        return {}
    lines = _docker_ps_lines(_run)
    if lines is None:
        return {container: "error" for container in wanted}

    states = {container: "absent" for container in wanted}
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            return {container: "error" for container in wanted}
        name = row.get("Names") or row.get("Name")
        if name in states:
            states[name] = str(row.get("State") or "unknown").casefold()
    return states


def docker_compose_project(container, _run=subprocess.run):
    """Return one container's Compose project label, or ``None`` when absent.

    A lifecycle operation must not infer ownership from the directory holding a
    compose file.  The label is Docker's durable identity for an existing
    container; callers compare it with the explicit project in the launch argv.
    """
    result = _run_or(
        [
            "docker", "inspect", "-f",
            '{{index .Config.Labels "com.docker.compose.project"}}',
            container,
        ],
        None, _run, capture_output=True, text=True,
    )
    if result is None or result.returncode != 0:
        return None
    value = (result.stdout or "").strip()
    # Several older injected test runners return the state for every inspect
    # template.  A Docker state is never a useful Compose owner.
    return value if value and value not in _DOCKER_STATES and value != "<no value>" else None


def _docker_port_occupants(ports, _run=subprocess.run):
    """Return non-authoritative Docker rows publishing any requested host port."""
    wanted = {int(port) for port in ports}
    found = {port: [] for port in wanted}
    if not wanted:
        return found
    lines = _docker_ps_lines(_run)
    if lines is None:
        return found
    for line in lines:
        try:
            row = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        published = {
            int(match)
            for match in re.findall(r"(?::|\[::\]:)(\d+)->", str(row.get("Ports") or ""))
        }
        labels = {}
        for item in str(row.get("Labels") or "").split(","):
            key, separator, value = item.partition("=")
            if separator:
                labels[key] = value
        item = {
            "container": row.get("Names") or row.get("Name") or row.get("ID"),
            "state": row.get("State") or row.get("Status"),
            "compose_project": labels.get("com.docker.compose.project"),
        }
        for port in wanted & published:
            found[port].append(item)
    return found


def _health(port, path, _open=urllib.request.urlopen):
    url = runtime_url("http://127.0.0.1:%s%s" % (port, path))
    try:
        with _open(url, timeout=3) as resp:
            return getattr(resp, "status", None) or resp.getcode()
    except Exception:
        return None


def _gpu_lines(_run=subprocess.run):
    r = _run_or(["nvidia-smi", "--query-gpu=index,memory.used,memory.total",
                "--format=csv,noheader,nounits"], None, _run, capture_output=True, text=True)
    if r is None or r.returncode != 0:
        return []
    return [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]


def reservation_summary(serves, _run=subprocess.run, _states=None):
    """Machine-readable per-gpu_role VRAM reservation ledger (ADR-0017, T004).

    The MCP `reservation_status` tool returns exactly this; `serves status`
    prints the same ledger. `_states` lets callers that already probed docker
    (cmd_status's serve loop) reuse those observations instead of re-probing.
    Manifests without `[[gpu_roles]]` yield an empty `gpu_roles` list and run
    zero docker probes.
    """
    budgets = reservations.budgets_of(serves)
    known = _states or {}

    def state_of(container):
        return known.get(container) or docker_state(container, _run=_run)

    ledger = reservations.build_ledger(serves, state_of, budgets=budgets)
    return reservations.ledger_summary(ledger)


def operating_mode_summary(serves, state_of):
    """Structured split/exclusive mode and per-role ownership snapshot."""
    exclusive = [serve for serve in serves if reservations.is_exclusive(serve)]
    active = []
    unresolved = []
    for serve in exclusive:
        state = state_of(serve["container"])
        if state in reservations.RESERVED_STATES:
            active.append(serve)
        elif state in {"error", "unknown", "removing"}:
            unresolved.append({"serve": serve["name"], "state": state})
    owner = active[0] if len(active) == 1 else None
    ledger = reservations.build_ledger(serves, state_of)
    unresolved_by_serve = {
        item["serve"]: item for item in unresolved
    }
    for role_ledger in ledger.values():
        for reservation in role_ledger.reservations:
            if reservation.state in {"error", "unknown", "removing"}:
                unresolved_by_serve.setdefault(
                    reservation.serve,
                    {"serve": reservation.serve, "state": reservation.state},
                )
    unresolved = list(unresolved_by_serve.values())
    mode = (
        DUAL_GPU_EXCLUSIVE_MODE if len(active) == 1 and not unresolved
        else "unresolved" if unresolved or len(active) > 1
        else "split"
    )
    role_ownership = []
    for role, role_ledger in sorted(ledger.items()):
        committed = sorted({
            reservation.serve for reservation in role_ledger.reservations
            if reservation.committed
        })
        role_ownership.append({"gpu_role": role, "owners": committed})
    return {
        "mode": mode,
        "exclusive_owner": owner["name"] if owner else None,
        "gpu_roles": (
            [r.gpu_role for r in reservations.reservations_of(owner)] if owner else []
        ),
        "gpu_ownership": role_ownership,
        "tensor_parallel_size": owner.get("tensor_parallel_size") if owner else None,
        "blocked_workloads": (
            [
                serve["name"] for serve in serves
                if serve["name"] != owner["name"]
                and reservations.is_gpu_inference(serve)
            ]
            if owner else []
        ),
        "unresolved": unresolved,
    }


def status_summary(serves, names=None, _run=subprocess.run, _open=urllib.request.urlopen):
    """Machine-readable serve status for MCP/automation.

    Mirrors :func:`cmd_status` without printing. The shape is intentionally
    simple and stable so agent tools do not scrape the human table.
    """
    selected = _select(serves, names) if names else _serving_path_scope(serves)
    status_scope = _serving_path_scope(serves, selected)
    rows = []
    states = {}

    def state_of(container):
        if container not in states:
            states[container] = docker_state(container, _run=_run)
        return states[container]

    occupants = _docker_port_occupants((s["port"] for s in selected), _run=_run)
    for s in selected:
        st = state_of(s["container"])
        health = _health(s["port"], s.get("health", "/health"), _open=_open) if st == "running" else None
        up = s.get("up")
        expected_project = _expected_compose_project(s) if _is_compose_up(up) else None
        observed_project = (
            docker_compose_project(s["container"], _run=_run)
            if st not in {"absent", "error"} and expected_project
            else None
        )
        conflicts = [
            item for item in occupants.get(int(s["port"]), [])
            if item.get("container") != s["container"]
        ]
        rows.append({
            "name": s["name"],
            "container": s["container"],
            "port": s["port"],
            "health_path": s.get("health", "/health"),
            "docker_state": st,
            "running": st == "running",
            "health_status": health,
            "model": s.get("model"),
            "engine": s.get("engine"),
            "stack": s.get("stack", DEFAULT_STACK),
            "expected_compose_project": expected_project,
            "observed_compose_project": observed_project,
            "compose_ownership_mismatch": bool(
                expected_project and observed_project and expected_project != observed_project
            ),
            "port_conflicts": conflicts,
        })
    return {
        "serves": rows,
        "selected": [r["name"] for r in rows],
        "gpu_memory_lines": _gpu_lines(_run=_run),
        # Only serving-path rows and explicit opt-ins participate. Polling
        # every experiment merely because it exists in the registry makes a
        # default status both noisy and operationally misleading.
        "reservations": reservation_summary(
            status_scope, _run=_run, _states=states
        ),
        "operating_mode": operating_mode_summary(serves, state_of),
    }


def cmd_status(
    serves,
    names=None,
    _run=subprocess.run,
    _open=urllib.request.urlopen,
    ledger_serves=None,
):
    # `names` (from positional selectors and/or --group) filters WHICH rows are
    # printed; the reservation ledger below still spans the WHOLE `serves` list,
    # because committed VRAM on a role comes from every declared serve — a
    # filtered ledger would misreport `free`. `names=None` retains the library
    # API's all-serves behavior; the CLI passes its explicit serving-path scope.
    # docker_state is memoized so a filtered view probes only the rows it prints
    # plus the reservation-declaring serves.
    selected = (
        list(serves)
        if names is None
        else (_select(serves, names) if names else [])
    )
    selected_containers = {s["container"] for s in selected}
    states = {}
    occupants = _docker_port_occupants((s["port"] for s in selected), _run=_run)

    def state_of(container):
        if container not in states:
            states[container] = docker_state(container, _run=_run)
        return states[container]

    print("%-16s %-16s %-6s %-9s %s" % ("SERVE", "CONTAINER", "PORT", "DOCKER", "HEALTH"))
    for s in serves:
        if s["container"] not in selected_containers:
            continue
        st = state_of(s["container"])
        health = _health(s["port"], s["health"], _open=_open) if st == "running" else None
        print("%-16s %-16s %-6s %-9s %s" % (
            s["name"], s["container"], s["port"], st, health if health else "-"))
        up = s.get("up")
        expected_project = _expected_compose_project(s) if _is_compose_up(up) else None
        if expected_project and st not in {"absent", "error"}:
            observed_project = docker_compose_project(s["container"], _run=_run)
            if observed_project and observed_project != expected_project:
                print(
                    "  WARNING: %s stack ownership mismatch: stack %r expects "
                    "Compose project %r, observed %r"
                    % (
                        s["container"],
                        s.get("stack", DEFAULT_STACK),
                        expected_project,
                        observed_project,
                    )
                )
        conflicts = [
            item for item in occupants.get(int(s["port"]), [])
            if item.get("container") != s["container"]
        ]
        for conflict in conflicts:
            print(
                "  WARNING: port %s also published by unmanaged/conflicting "
                "container %s (project=%s state=%s)"
                % (
                    s["port"],
                    conflict.get("container") or "?",
                    conflict.get("compose_project") or "-",
                    conflict.get("state") or "-",
                )
            )
    gpus = _gpu_lines(_run=_run)
    if gpus:
        print("\nGPU memory (index, used MiB, total MiB):")
        for g in gpus:
            print("  " + g)
    # ADR-0017 reservation ledger (T004): per-gpu_role capacity/reserve/
    # committed/free plus each declared reservation. Reuses the states probed
    # above (every manifest serve was just inspected), so this section adds no
    # docker calls; manifests without [[gpu_roles]] print nothing extra.
    ledger_source = serves if ledger_serves is None else ledger_serves
    budgets = reservations.budgets_of(ledger_source)
    if budgets:
        ledger = reservations.build_ledger(
            ledger_source, state_of, budgets=budgets
        )
        print("\nGPU reservations (ADR-0017, derived from docker state):")
        for _, role_ledger in sorted(ledger.items()):
            print("  " + role_ledger.describe())
            for r in role_ledger.reservations:
                print("    %s%s" % (r.describe(), "" if r.committed else " [not committed]"))
        mode = operating_mode_summary(ledger_source, state_of)
        print("\nOperating mode: %s" % mode["mode"])
        if mode["exclusive_owner"]:
            print("  exclusive owner: %s (TP=%s)" % (
                mode["exclusive_owner"], mode["tensor_parallel_size"],
            ))
            print("  gpu roles: %s" % ", ".join(mode["gpu_roles"]))
            print("  blocked workloads: %s" % (
                ", ".join(mode["blocked_workloads"]) or "none",
            ))
        for ownership in mode["gpu_ownership"]:
            print("  %s owners: %s" % (
                ownership["gpu_role"], ", ".join(ownership["owners"]) or "none",
            ))
        for unresolved in mode["unresolved"]:
            print("  UNRESOLVED: %s state %s" % (
                unresolved["serve"], unresolved["state"],
            ))
    return 0


def _registry_path_of(serve):
    """The `--registry` argument inside a serve's `up` command, if it has one.

    `up` is already shlex-split at load, so this is a token scan rather than a
    second parse of the command string.
    """
    up = serve.get("up") or []
    for index, token in enumerate(up):
        if token == "--registry" and index + 1 < len(up):
            return up[index + 1]
        if token.startswith("--registry="):
            return token.split("=", 1)[1]
    return None


def _inside_linked_worktree(path, _run=subprocess.run):
    """True when `path` resolves inside a linked git worktree.

    A linked worktree reports a `--git-dir` under the main checkout's
    `--git-common-dir`; for the main checkout the two are the same place. That
    difference is the exact, cheap test, and it matters because a linked
    worktree is disposable: `git worktree remove` deletes state a live serve
    depends on.
    """
    directory = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(directory):
        return False
    try:
        common = _run(["git", "-C", directory, "rev-parse", "--git-common-dir"],
                      capture_output=True, text=True)
        own = _run(["git", "-C", directory, "rev-parse", "--git-dir"],
                   capture_output=True, text=True)
    except OSError:
        return False
    if common.returncode != 0 or own.returncode != 0:
        return False

    def _resolve(result):
        value = result.stdout.strip()
        if not value:
            return None
        return os.path.realpath(os.path.join(directory, value))

    common_dir, own_dir = _resolve(common), _resolve(own)
    return bool(common_dir and own_dir and common_dir != own_dir)


def lint_manifest_set(serves, _run=subprocess.run):
    """Report manifest defects that no other surface makes visible.

    Each check exists because the defect it finds occurred live and every
    command reported success while it was present. See
    docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md.
    """
    findings = []

    # Duplicate names AFTER container de-dup. Sharing a container across files
    # is a supported mirror pattern, so it is not a defect; two surviving
    # entries sharing a NAME is, because name selection then matches more than
    # one serve and one of them silently wins.
    by_name = {}
    for serve in serves:
        by_name.setdefault(serve["name"], []).append(serve)
    for name, entries in sorted(by_name.items()):
        if len(entries) < 2:
            continue
        findings.append({
            "check": "duplicate-serve-name",
            "severity": "error",
            "serve": name,
            "detail": "declared %d times with different containers (%s); name "
                      "selection is ambiguous and one entry silently wins"
                      % (len(entries), ", ".join(e["container"] for e in entries)),
            "files": sorted({e.get("_manifest_file", "?") for e in entries}),
        })

    for serve in serves:
        registry = _registry_path_of(serve)
        if not registry:
            continue
        resolved = os.path.abspath(os.path.expanduser(registry))
        if not os.path.isfile(resolved):
            findings.append({
                "check": "missing-registry",
                "severity": "error",
                "serve": serve["name"],
                "detail": "up command names a recipe registry that does not "
                          "exist: %s" % resolved,
                "files": [serve.get("_manifest_file", "?")],
            })
            continue
        if _inside_linked_worktree(resolved, _run=_run):
            findings.append({
                "check": "worktree-anchored-registry",
                "severity": "warning",
                "serve": serve["name"],
                "detail": "recipe registry resolves inside a linked git "
                          "worktree, which routine cleanup deletes: %s" % resolved,
                "files": [serve.get("_manifest_file", "?")],
            })

    return {
        "findings": findings,
        "errors": sum(1 for f in findings if f["severity"] == "error"),
        "warnings": sum(1 for f in findings if f["severity"] == "warning"),
        "serves_checked": len(serves),
    }


def _print_findings(findings, check_width=27, file=None):
    """Shared finding-printing loop for lint, rollback-check, and the preflight
    gate -- one format, so an operator sees identical detail from every
    surface that reports the same kind of finding."""
    for finding in findings:
        print("%-8s %-*s %s" % (
            finding["severity"].upper(), check_width, finding["check"],
            finding["serve"]), file=file)
        print("         %s" % finding["detail"], file=file)
        for path in finding["files"]:
            print("         in %s" % path, file=file)


def cmd_lint(serves, as_json=False, _run=subprocess.run):
    """Report manifest defects; non-zero exit on errors so it can gate CI."""
    report = lint_manifest_set(serves, _run=_run)
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1 if report["errors"] else 0
    if not report["findings"]:
        print("serves lint: %d serves checked, no findings"
              % report["serves_checked"])
        return 0
    _print_findings(report["findings"], check_width=27)
    print("serves lint: %d serves checked, %d error(s), %d warning(s)" % (
        report["serves_checked"], report["errors"], report["warnings"]))
    return 1 if report["errors"] else 0


def _serve_by_name(serves, name):
    matches = [s for s in serves if s["name"] == name]
    return matches[0] if len(matches) == 1 else None


def _compose_invocation_of(serve):
    """The compose shape of a serve's `up` command: (files, profiles, services).

    `up` is already shlex-split at load, so this is a token scan, matching
    `_registry_path_of` (including the `=` forms). Returns None for a serve
    whose `up` names no compose file (a script/recipe-load command).

    The whole shape matters, not just the file: a rollback serve gated behind
    `--profile rollback` is EXCLUDED from `docker compose config` unless the
    profile is passed, so checking the bare file silently skips exactly the
    services a rollback check exists to verify. Likewise the service names
    after `up` scope the image query to the serve actually being checked, and
    overlay chains (`-f base.yml -f override.yml`) must all be forwarded or
    the override's pinned image is never seen.
    """
    up = serve.get("up") or []
    files, profiles, services = [], [], []
    manifest_dir = serve.get("_manifest_dir") or ""

    def _resolve(path):
        if os.path.isabs(path):
            return path
        return os.path.join(manifest_dir, path) if manifest_dir else path

    saw_up = False
    index = 0
    while index < len(up):
        token = up[index]
        if token in ("-f", "--file") and index + 1 < len(up):
            files.append(_resolve(up[index + 1]))
            index += 2
            continue
        if token.startswith("-f=") or token.startswith("--file="):
            files.append(_resolve(token.split("=", 1)[1]))
        elif token == "--profile" and index + 1 < len(up):
            profiles.append(up[index + 1])
            index += 2
            continue
        elif token.startswith("--profile="):
            profiles.append(token.split("=", 1)[1])
        elif token == "up":
            saw_up = True
        elif saw_up and not token.startswith("-"):
            services.append(token)
        index += 1
    if not files:
        return None
    return tuple(files), tuple(profiles), tuple(services)


def rollback_check_manifest_set(serves, promotions, restore_group=None, _run=subprocess.run):
    """Prove every declared rollback is actually usable, read-only.

    Two rollback paths were found broken live on 2026-08-08: a promotion
    plan's `rollback_router_config` referenced a file that did not exist
    (found by accident), and a restore-group serve's compose image was an
    evicted nightly tag, so the documented rollback group could not start. A
    rollback that cannot run is a false safety net. See
    docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md (feature 4).
    """
    from .router.config import load as load_router_config

    findings = []

    # 1. Promotion plans: topology validation must never raise into the caller
    # -- a broken plan is exactly the kind of finding this command exists to
    # report, not a crash.
    for plan in promotions:
        try:
            _validate_promotion_topology(serves, plan)
        except Exception as exc:
            findings.append({
                "check": "promotion-topology",
                "severity": "error",
                "serve": plan.get("name", "?"),
                "detail": str(exc),
                "files": [],
            })

    # 2. Routed exclusive serves: rollback_router_config existence is already
    # guaranteed at manifest load (_normalize_mode_router_configs); this
    # checks it actually parses/validates as a router config.
    for serve in serves:
        path = serve.get("rollback_router_config")
        if not path:
            continue
        try:
            load_router_config(path)
        except Exception as exc:
            findings.append({
                "check": "rollback-profile-invalid",
                "severity": "error",
                "serve": serve["name"],
                "detail": "rollback_router_config failed to load: %s" % exc,
                "files": [path],
            })

    # 3. Image presence for every serve a rollback depends on: each plan's
    # `rollback` serve, plus every serve in --restore-group (when given).
    dependents = []
    for plan in promotions:
        serve = _serve_by_name(serves, plan.get("rollback"))
        if serve is not None:
            dependents.append(("promotion %r rollback" % plan["name"], serve))
    if restore_group:
        members, unknown = select_groups(serves, [restore_group])
        if unknown:
            # A typo'd group silently checking nothing is itself a false
            # safety net — the exact defect class this command exists to kill.
            findings.append({
                "check": "unknown-restore-group",
                "severity": "error",
                "serve": restore_group,
                "detail": "restore group %r matches no serve in the manifest "
                          "set; nothing was verified for it" % restore_group,
                "files": [],
            })
        for serve in members:
            dependents.append(("restore-group %r" % restore_group, serve))

    compose_sources = {}
    for label, serve in dependents:
        invocation = _compose_invocation_of(serve)
        if invocation is None:
            findings.append({
                "check": "image-unverifiable",
                "severity": "info",
                "serve": serve["name"],
                "detail": "%s (%s) has no compose file in its up command; "
                          "image presence cannot be verified" % (label, serve["name"]),
                "files": [],
            })
            continue
        source = "%s (%s)" % (label, serve["name"])
        sources = compose_sources.setdefault(invocation, [])
        if source not in sources:
            sources.append(source)

    try:
        for invocation, sources in sorted(compose_sources.items()):
            files, profiles, services = invocation
            who = "; ".join(sources)
            argv = ["docker", "compose"]
            for compose_file in files:
                argv += ["-f", compose_file]
            for profile in profiles:
                argv += ["--profile", profile]
            argv += ["config", "--images", *services]
            config = _run(argv, capture_output=True, text=True)
            if config.returncode != 0:
                findings.append({
                    "check": "rollback-image-missing",
                    "severity": "error",
                    "serve": who,
                    "detail": "%s failed: %s"
                              % (" ".join(argv), (config.stderr or "").strip()),
                    "files": list(files),
                })
                continue
            seen_images = set()
            for image in (line.strip() for line in config.stdout.splitlines()):
                if not image or image in seen_images:
                    continue
                seen_images.add(image)
                inspect = _run(["docker", "image", "inspect", image],
                               capture_output=True, text=True)
                if inspect.returncode != 0:
                    findings.append({
                        "check": "rollback-image-missing",
                        "severity": "error",
                        "serve": who,
                        "detail": "image %s (from %s) is not present locally; "
                                  "the rollback cannot start: %s"
                                  % (image, " ".join(files),
                                     (inspect.stderr or "").strip()),
                        "files": list(files),
                    })
    except OSError as exc:
        findings.append({
            "check": "docker-unavailable",
            "severity": "warning",
            "serve": "-",
            "detail": "docker is not available; rollback image presence could "
                      "not be verified: %s" % exc,
            "files": [],
        })

    return {
        "findings": findings,
        "errors": sum(1 for f in findings if f["severity"] == "error"),
        "warnings": sum(1 for f in findings if f["severity"] == "warning"),
        "infos": sum(1 for f in findings if f["severity"] == "info"),
        "serves_checked": len(serves),
        "promotions_checked": len(promotions),
    }


def cmd_rollback_check(serves, promotions, restore_group=None, as_json=False, _run=subprocess.run):
    """Prove every declared rollback is usable; non-zero exit on errors."""
    report = rollback_check_manifest_set(
        serves, promotions, restore_group=restore_group, _run=_run)
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1 if report["errors"] else 0
    if not report["findings"]:
        print("serves rollback-check: %d serve(s), %d promotion plan(s) checked, "
              "no findings" % (report["serves_checked"], report["promotions_checked"]))
        return 0
    _print_findings(report["findings"], check_width=24)
    print("serves rollback-check: %d serve(s), %d promotion plan(s) checked, "
          "%d error(s), %d warning(s), %d info" % (
              report["serves_checked"], report["promotions_checked"],
              report["errors"], report["warnings"], report["infos"]))
    return 1 if report["errors"] else 0


def preflight_check_reports(serves, promotions, restore_group=None, _run=subprocess.run):
    """Both preflight reports a mutating transaction gates on: lint +
    rollback-check. Pure and read-only, like the two checks it wraps.
    """
    return {
        "lint": lint_manifest_set(serves, _run=_run),
        "rollback_check": rollback_check_manifest_set(
            serves, promotions, restore_group=restore_group, _run=_run),
    }


def _finding_is_relevant(finding, involved, restore_group):
    """Is `finding` about this transaction's blast radius, or some other serve?

    Only called for error-severity findings; warning/info findings are never
    blocking regardless of relevance (they always print as advisory context).

    check name               | `serve` holds                | relevance
    ------------------------ | ------------------------------ | -------------------------------------------
    duplicate-serve-name     | the duplicated NAME            | blocks-always (structural: breaks name
                              |                                 | resolution itself, everywhere)
    missing-registry         | serve name                      | blocks-if-involved
    promotion-topology       | PROMOTION PLAN name, not a      | blocks-if-involved; the promote call site
                              | serve name                      | adds the resolved plan's NAME to
                              |                                 | `involved`, so a topology error on the
                              |                                 | plan actually being promoted blocks.
                              |                                 | Other plans' topology errors are advisory.
    rollback-profile-invalid | serve name                      | blocks-if-involved
    unknown-restore-group    | the --restore-group value       | blocks-always when --restore-group was
                              |                                 | passed (nothing was verified for it)
    rollback-image-missing   | "label (name); label2 (n2)"     | blocks-if-involved (matched by "(name)"
                              | joined string                   | substring against the involved set --
                              |                                 | see rollback_check_manifest_set's `who`)
    """
    check = finding["check"]
    serve = finding.get("serve") or ""
    if check == "duplicate-serve-name" or not serve or serve == "-":
        return True
    if check == "unknown-restore-group":
        return bool(restore_group)
    if check == "rollback-image-missing":
        return any("(%s)" % name in serve for name in involved)
    return serve in involved


def _preflight_gate(serves, promotions, *, restore_group=None, skip=False,
                    involved=frozenset(), label, _run=subprocess.run):
    """Run the implicit lint + rollback-check gate before `promote`/`mode
    enter` begin their transaction (docs/FEATURE-EXECUTION-PLAYBOOK.md gate
    sequence). Both checks always run over the FULL manifest set -- scoping
    happens only at the abort decision, so a defect anywhere is still
    reported (detection stays loud everywhere; see
    docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md and
    docs/FEATURE-EXECUTION-PLAYBOOK.md).

    Only an error-severity finding relevant to `involved` (the serve names
    this transaction actually touches -- see `_finding_is_relevant`) aborts
    the transaction. An error about an unrelated serve (e.g. a stale
    `missing-registry` on a serve nobody is touching) is printed as advisory
    and does not block: refusing every command over one unrelated manifest
    entry is exactly what feature 5's revision in
    STRATEGY-MAKE-DIVERGENCE-LOUD.md rejects. Warning/info findings never
    block. `label` names the calling transaction ("promote" / "mode enter")
    in every message this prints.

    Returns 3 when the transaction must abort, None when it may proceed.
    """
    if skip:
        print(
            "preflight checks SKIPPED (--skip-preflight-checks): lint and "
            "rollback-check were NOT run for this %s" % label,
            file=sys.stderr,
        )
        return None
    reports = preflight_check_reports(
        serves, promotions, restore_group=restore_group, _run=_run)
    findings = reports["lint"]["findings"] + reports["rollback_check"]["findings"]
    errors = reports["lint"]["errors"] + reports["rollback_check"]["errors"]
    warnings = reports["lint"]["warnings"] + reports["rollback_check"]["warnings"]
    if not findings:
        return None

    blocking, advisory = [], []
    for finding in findings:
        if finding["severity"] == "error" and _finding_is_relevant(
            finding, involved, restore_group
        ):
            blocking.append(finding)
        else:
            advisory.append(finding)

    # Everything the gate prints goes to stderr: it is refusal/advisory
    # diagnostics, not transaction output, and the --json envelope only
    # carries stderr for a nonzero exit -- findings on stdout would be
    # invisible to a JSON-mode caller exactly when they block it.
    infos = len(findings) - errors - warnings
    print("preflight checks for %s: %d error(s), %d warning(s), %d info "
          "(%d blocking, %d advisory)" % (
              label, errors, warnings, infos, len(blocking), len(advisory)),
          file=sys.stderr)
    for finding in blocking:
        _print_findings([finding], check_width=27, file=sys.stderr)
    for finding in advisory:
        marker = (
            "ADVISORY (outside this transaction): "
            if finding["severity"] == "error" else ""
        )
        print("%s%-8s %-27s %s" % (
            marker, finding["severity"].upper(), finding["check"],
            finding["serve"]), file=sys.stderr)
        print("         %s" % finding["detail"], file=sys.stderr)
        for path in finding["files"]:
            print("         in %s" % path, file=sys.stderr)
    if blocking:
        print(
            "%s refused before any mutation: %d preflight check error(s) "
            "blocking this transaction (%d additional advisory finding(s); "
            "rerun with --skip-preflight-checks to override)" % (
                label, len(blocking), len(advisory)),
            file=sys.stderr,
        )
        return 3
    return None


def resolve_alias_backers(config, serves, alias):
    """Join alias -> tier -> serve, the walk `serves up-for` exists to do.

    Pure and read-only: no argparse, no docker, no stdout/stderr. `config` is a
    loaded `RouterConfig`; `serves` is a manifest SET. A serve is a candidate
    when its `router_tier` equals the alias's resolved tier id -- ordinarily
    exactly one, but a promoted primary and its rollback legitimately share a
    `router_tier` (and a port), so more than one is possible. See
    docs/PRODUCT-DISCOVERY-PERSONAS.md §2 and
    docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md (feature 11).
    """
    from .router.config import normalize_model_alias

    normalized = normalize_model_alias(alias)
    tier_id = config.model_routes.get(normalized)
    candidates = []
    if tier_id is not None:
        for serve in serves:
            if serve.get("router_tier") == tier_id:
                candidates.append({
                    "name": serve["name"],
                    "container": serve["container"],
                    "port": serve.get("port"),
                    "groups": list(serve.get("groups") or []),
                    "up": list(serve.get("up") or []),
                    "manifest_file": serve.get("_manifest_file"),
                })
    return {
        "alias": alias,
        "normalized_alias": normalized,
        "tier_id": tier_id,
        "known_aliases": sorted(config.model_routes),
        "candidates": candidates,
    }


def cmd_up_for(config, serves, alias, config_path, as_json=False, confirm=False,
               dry_run=False, ledger_serves=None, _run=subprocess.run):
    """Resolve ALIAS -> tier -> serve and print the chain; `--confirm` starts it.

    Closes the walk an operator does by hand today: `model_routes` maps alias
    to tier, serve entries carry `router_tier` -- nothing joins them. Multiple
    candidates (a promoted primary sharing its `router_tier` with a rollback
    serve on the same port) are refused automatically: starting the wrong one
    on a shared port is worse than asking the operator to pick with
    `serves up NAME`.
    """
    result = resolve_alias_backers(config, serves, alias)

    if result["tier_id"] is None:
        message = "unknown alias %r; configured aliases: %s" % (
            alias, ", ".join(result["known_aliases"]) or "(none)")
        if as_json:
            print(json.dumps({**result, "error": message}, indent=2, sort_keys=True))
        else:
            print(message, file=sys.stderr)
        return 2

    candidates = result["candidates"]
    if not candidates:
        message = (
            "tier %r (alias %r) has no backing serve in the manifest set; the "
            "serve manifest declares no [[serve]] with router_tier = %r"
            % (result["tier_id"], alias, result["tier_id"])
        )
        if as_json:
            print(json.dumps({**result, "error": message}, indent=2, sort_keys=True))
        else:
            print(message, file=sys.stderr)
        return 1

    if len(candidates) > 1:
        message = (
            "tier %r (alias %r) has %d backing serves; starting the wrong one "
            "on a shared port is worse than guessing -- pick one explicitly "
            "with `serves up NAME`" % (result["tier_id"], alias, len(candidates))
        )
        if as_json:
            print(json.dumps({**result, "error": message}, indent=2, sort_keys=True))
        else:
            print(message, file=sys.stderr)
            for candidate in candidates:
                print("  %-20s container=%-24s port=%-6s groups=%s" % (
                    candidate["name"], candidate["container"], candidate["port"],
                    ", ".join(candidate["groups"]) or "-"), file=sys.stderr)
        return 1

    candidate = candidates[0]
    if not confirm:
        resolution = {**result, "resolved": candidate, "config": str(config_path)}
        if as_json:
            print(json.dumps(resolution, indent=2, sort_keys=True))
        else:
            print("alias:    %s -> %s" % (alias, result["normalized_alias"]))
            print("tier:     %s" % result["tier_id"])
            print("serve:    %s (container=%s, port=%s)" % (
                candidate["name"], candidate["container"], candidate["port"]))
            print("manifest: %s" % candidate["manifest_file"])
            print("config:   %s" % config_path)
            print("up:       %s" % (
                " ".join(candidate["up"]) if candidate["up"]
                else "(no compose up command declared)"))
            print("\nrun with --confirm to start it: "
                  "anvil-serving serves up-for %s --confirm" % alias)
        return 0

    return cmd_up(serves, [candidate["name"]], dry_run=dry_run,
                  wait_for_readiness=not dry_run, ledger_serves=ledger_serves,
                  _run=_run)


def cmd_groups(serves, as_json=False):
    """List the groups defined across the manifest set and their member serves.

    Read-only (no docker/network); the manifest set has already been resolved
    and de-duped by the caller. `--json` emits the same catalog structurally for
    tooling, matching the status/reservation JSON conventions.
    """
    summary = groups_summary(serves)
    if as_json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    if not summary["groups"]:
        print("no groups defined in the manifest set")
    else:
        print("%-14s %s" % ("GROUP", "SERVES"))
        for row in summary["groups"]:
            print("%-14s %s" % (row["group"], ", ".join(row["serves"])))
    print(
        "\nreserved: 'all' selects every serve in the set (%d): %s"
        % (len(summary["all"]), ", ".join(summary["all"]) or "-")
    )
    return 0


def _docker_rm_f(container, _run, *, timeout=None, action="remove", label="removed",
                  suffix="", fail_suffix=None):
    """`docker rm -f` one container, returning ``(ok, message)`` to print.

    Isolates the run/timeout/returncode handling repeated 4x in ``cmd_down``;
    `label`/`suffix`/`fail_suffix` reproduce each call site's wording exactly.
    """
    if fail_suffix is None:
        fail_suffix = suffix
    kwargs = dict(capture_output=True, text=True)
    if timeout is not None:
        kwargs["timeout"] = timeout
    try:
        removed = _run(["docker", "rm", "-f", container], **kwargs)
    except subprocess.TimeoutExpired:
        return False, "  FAILED to %s %s within %ss" % (action, container, timeout)
    if removed.returncode == 0:
        return True, "  %s %s%s" % (label, container, suffix)
    return False, "  FAILED to %s %s%s: %s" % (
        action, container, fail_suffix, (removed.stderr or "").strip()
    )


def cmd_down(
    serves,
    names,
    dry_run=False,
    keep_container=False,
    force_remove=False,
    _run=subprocess.run,
):
    """Stop selected serves and remove their containers by default.

    ``keep_container=True`` preserves the stopped container and its logs for
    diagnostics or a cheap restart. The normal operator contract removes stale
    runtime configuration so model experiments do not accumulate in Docker.
    """
    # ADR-0017: stopping/removing a container IS the reservation release — the
    # ledger is derived from docker state, so no bookkeeping happens (or could
    # drift) here.
    targets = _select(serves, names)
    if not targets:
        print("no matching serves in manifest")
        return 1
    rc = 0
    for s in targets:
        st = docker_state(s["container"], _run=_run)
        if st == "error":
            print("  %s: cannot determine state (docker missing / daemon down / "
                  "permission?)" % s["container"])
            rc = 1
            continue
        declared_native_offload = s.get("native_kv_offload") is True

        def finish_native_offload_cleanup():
            cleanup = host_ops.prepare_native_kv_offload_shared_memory(_run=_run)
            host_ops.render_vllm_offload_shared_memory(cleanup)
            if cleanup.get("outcome") in {"clean", "reclaimed"}:
                return True
            print(
                "  FAILED native KV-offload shared-memory cleanup after %s: %s"
                % (s["container"], cleanup.get("outcome", "unknown"))
            )
            return False

        if st == "absent":
            print("  %s: absent (nothing to stop or remove)" % s["container"])
            if declared_native_offload:
                if dry_run:
                    print(
                        "  would inspect/reclaim twice-verified native KV-offload "
                        "orphan mmap files"
                    )
                elif not finish_native_offload_cleanup():
                    rc = 1
            continue
        detected_native_offload = host_ops.container_uses_native_kv_offload(
            s["container"], _run=_run,
        ) is True
        native_offload = declared_native_offload or detected_native_offload

        def finish_detected_native_offload_cleanup():
            if not native_offload:
                return True
            return finish_native_offload_cleanup()

        def record_down(graceful):
            nonlocal rc
            if not _record_lifecycle_event(
                "serve.down",
                {"serve": s["name"], "graceful": graceful},
            ):
                rc = 1

        if st in _STOPPED:
            if keep_container:
                print("  %s: %s (kept for logs/restart)" % (s["container"], st))
                if not dry_run and not finish_detected_native_offload_cleanup():
                    rc = 1
                continue
            print("  rm -f %s (%s)" % (s["container"], st))
            if dry_run:
                continue
            ok, message = _docker_rm_f(s["container"], _run)
            print(message)
            if ok:
                if not finish_detected_native_offload_cleanup():
                    rc = 1
            else:
                rc = 1
            continue
        # running / paused / restarting / removing / unknown -> stop (frees the GPU).
        # Honor --dry-run: `down` is state-changing (it frees GPUs / kills in-flight
        # serving), so a preview must NOT actually stop anything.
        print("  stop %s" % s["container"])
        if dry_run:
            if not keep_container:
                print("  rm -f %s" % s["container"])
            continue
        if force_remove and not keep_container:
            print("  rm -f %s (forced lifecycle release)" % s["container"])
            ok, message = _docker_rm_f(
                s["container"], _run,
                timeout=DOCKER_STOP_COMMAND_TIMEOUT_SECONDS,
                action="force-remove", label="force-removed",
            )
            print(message)
            if ok:
                if not finish_detected_native_offload_cleanup():
                    rc = 1
                record_down(False)
            else:
                rc = 1
            continue
        try:
            r = _run(
                ["docker", "stop", s["container"]],
                capture_output=True,
                text=True,
                timeout=DOCKER_STOP_COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            if keep_container:
                print(
                    "  FAILED to stop %s within %ss; container kept for diagnostics"
                    % (s["container"], DOCKER_STOP_COMMAND_TIMEOUT_SECONDS)
                )
                rc = 1
                continue
            print(
                "  stop timed out after %ss; force-removing %s"
                % (DOCKER_STOP_COMMAND_TIMEOUT_SECONDS, s["container"])
            )
            ok, message = _docker_rm_f(
                s["container"], _run,
                timeout=DOCKER_STOP_COMMAND_TIMEOUT_SECONDS,
                action="force-remove", label="force-removed",
                suffix=" after stop timeout",
            )
            print(message)
            if ok:
                if not finish_detected_native_offload_cleanup():
                    rc = 1
                record_down(False)
            else:
                rc = 1
            continue
        if r.returncode == 0:
            if keep_container:
                # Verify the stop STUCK: a `restart: always` policy revives the
                # container immediately, silently un-freeing the GPU.
                st_after = docker_state(s["container"], _run=_run)
                if st_after in ("running", "restarting"):
                    print(
                        "  WARNING: %s is %s again after stop (restart policy?) - "
                        "the GPU was NOT freed; omit `--keep-container` to remove it"
                        % (s["container"], st_after)
                    )
                    rc = 1
                else:
                    print("  stopped and kept %s" % s["container"])
                    if not finish_detected_native_offload_cleanup():
                        rc = 1
                    record_down(True)
            else:
                ok, message = _docker_rm_f(
                    s["container"], _run,
                    label="stopped and removed", fail_suffix=" after stop",
                )
                print(message)
                if ok:
                    if not finish_detected_native_offload_cleanup():
                        rc = 1
                    record_down(True)
                else:
                    rc = 1
        else:
            print("  FAILED to stop %s: %s" % (s["container"], (r.stderr or "").strip()))
            rc = 1
    return rc


# Flags whose value names the model a container was created to serve. We prefer
# --served-model-name (what the OpenAI API advertises, what the manifest's `model`
# is), falling back to the weights id in --model / --model-path.
_SERVED_NAME_FLAGS = ("--served-model-name", "--served_model_name")
_MODEL_PATH_FLAGS = ("--model", "--model-path", "--model_path")


def _created_argv(container, _run=subprocess.run):
    """The argv a container was CREATED with (Config.Cmd + Args), one token per
    line. Empty list if docker is unavailable or inspect fails — callers must
    treat 'unknown' as 'no drift' and never block on uncertainty.
    """
    tmpl = "{{range .Config.Cmd}}{{println .}}{{end}}{{range .Args}}{{println .}}{{end}}"
    r = _run_or(["docker", "inspect", "-f", tmpl, container],
                None, _run, capture_output=True, text=True)
    if r is None or r.returncode != 0:
        return []
    return [ln for ln in (r.stdout or "").splitlines() if ln.strip()]


def _model_from_argv(tokens):
    """Best-effort served-model identifier from a container's created argv: the
    value of --served-model-name (preferred) else --model / --model-path. Handles
    both `--flag value` and `--flag=value`. None if neither flag is present.
    """
    def _value(flags):
        for i, tok in enumerate(tokens):
            for fl in flags:
                if tok == fl and i + 1 < len(tokens):
                    return tokens[i + 1]
                if tok.startswith(fl + "="):
                    return tok.split("=", 1)[1]
        return None
    return _value(_SERVED_NAME_FLAGS) or _value(_MODEL_PATH_FLAGS)


def _served_model(container, _run=subprocess.run):
    """The model an EXISTING container was created to serve, or None if it can't
    be determined (docker down, inspect failed, or no model flag on its argv).
    """
    return _model_from_argv(_created_argv(container, _run=_run))


def _is_compose_up(up):
    """True if the manifest `up` is a `docker compose up` — idempotent and drift-safe
    (it recreates the container when the compose config changed and fast-(re)starts it
    when unchanged), unlike a one-shot `docker run` script that can't be re-run over an
    existing container.
    """
    if not up:
        return False
    return up[:2] == ["docker", "compose"] or up[0] == "docker-compose"


def _stack_project(stack):
    """Map the user-facing stack name to Docker Compose's ownership label."""
    return "anvil-" + stack


def _explicit_compose_project(up):
    """Return a project explicitly authored in a Compose argv, if present."""
    if not _is_compose_up(up):
        return None
    for index, token in enumerate(up):
        if token in {"-p", "--project-name"} and index + 1 < len(up):
            return up[index + 1]
        if token.startswith("--project-name="):
            return token.split("=", 1)[1]
    return None


def _expected_compose_project(serve):
    """Return the durable Compose owner implied by a serve's stack."""
    return _stack_project(serve.get("stack", DEFAULT_STACK))


def _compose_up_with_project(up, project=DEFAULT_COMPOSE_PROJECT):
    """Make Compose ownership independent of the selected file's directory."""
    if not _is_compose_up(up) or any(
        token in {"-p", "--project-name"} or token.startswith("--project-name=")
        for token in up
    ):
        return list(up)
    insert_at = 2 if up[:2] == ["docker", "compose"] else 1
    return list(up[:insert_at]) + ["--project-name", project] + list(up[insert_at:])


def _warn_drift(s, _run=subprocess.run):
    """Loudly warn if an EXISTING (script-serve) container was created serving a
    different model than the manifest declares — a `docker start` would resurrect the
    STALE model. Best-effort: silent if the declared/served model can't be determined
    (never block on uncertainty). Compose serves don't need this: `up -d` self-heals.
    """
    declared = s.get("model")
    if not declared:
        return
    served = _served_model(s["container"], _run=_run)
    if served and served != declared:
        print("  WARNING: %s was created serving %r but the manifest declares %r -- "
              "`docker start` will resurrect the STALE model; run `up --recreate` (or "
              "convert this serve to a compose file) to fix."
              % (s["container"], served, declared))


def _readmit_evicted(tiers, transition):
    """Best-effort readmission after a refused eviction (mirrors promotion's
    `_compensate_quiesce`). `router readmit` is guarded — it re-runs health +
    exact-identity readiness — so a tier that cannot prove readiness stays
    quiesced (fail closed) and the operator is told."""
    failed = [t for t in dict.fromkeys(tiers) if transition("readmit", t) != 0]
    if failed:
        print(
            "  recovery: admission remains fail-closed for %s; readmit after "
            "router readiness recovers" % ", ".join(failed)
        )


def _evict_victims(serves, victims, *, dry_run=False, drain_timeout, transition,
                   _run=subprocess.run):
    """Stop committed `evictable` reservations through the ADR-0018 transition
    (ADR-0017 §5): per victim, quiesce its declared router tier and drain the
    tier's counted in-flight generations — the router's `AdmissionLease`
    accounting, bounded by `drain_timeout` — BEFORE any container is stopped.
    Only then does `cmd_down` stop the victims, which IS the reservation
    release (the ledger derives from docker state).

    A victim with no `router_tier` in its manifest entry has no router
    admission to drain (nothing routes through the router to it) and is
    stopped directly. A quiesce/drain refusal aborts the WHOLE eviction before
    the first container mutation, readmitting already-quiesced tiers
    best-effort. After a successful eviction the victims' tiers deliberately
    stay quiesced: an evicted serve is an unavailable tier (ADR-0017 §6), and
    guarded `router readmit` (health + exact model identity) is the only way
    back into rotation.

    `transition(action, tier_id, timeout=None) -> int` is the ADR-0018 step
    seam (returncode semantics, 0 = applied); the default is the deployed
    router's authenticated CLI boundary via `_transition_cli`.
    """
    by_name = {s["name"]: s for s in serves}
    plan = [(victim, by_name[victim.serve].get("router_tier")) for victim in victims]
    for victim, tier in plan:
        if tier:
            print("  evict %s: quiesce + drain router tier %s (timeout %ss), "
                  "then stop %s" % (
                      victim.serve, tier, drain_timeout, victim.container))
        else:
            print("  evict %s: no router_tier declared -- no router admission "
                  "to drain; stop %s directly" % (victim.serve, victim.container))
    if dry_run:
        return 0
    quiesced = []
    for _victim, tier in plan:
        if tier is None:
            continue
        if transition("quiesce", tier) != 0:
            print("  eviction refused: failed to quiesce %s" % tier)
            # The router may have applied quiescence before its response was
            # lost. Compensate the current tier as well as earlier successes.
            _readmit_evicted([*quiesced, tier], transition)
            return 2
        quiesced.append(tier)
    for _victim, tier in plan:
        if tier is None:
            continue
        if transition("drain", tier, timeout=drain_timeout) != 0:
            print("  eviction refused: drain timed out for %s before "
                  "container mutation" % tier)
            _readmit_evicted(quiesced, transition)
            return 2
    if cmd_down(serves, [victim.serve for victim, _ in plan], _run=_run) != 0:
        print("  eviction failed: a victim container did not stop; its router "
              "tier stays quiesced (fail closed)")
        return 1
    for victim, tier in plan:
        if tier:
            print("  evicted %s; router tier %s stays quiesced until "
                  "`router readmit` passes health + identity readiness" % (
                      victim.serve, tier))
    return 0


def ensure_router_healthy(*, no_router=False, dry_run=False, container=None,
                          compose=None, env_file=None,
                          _run=subprocess.run, _open=urllib.request.urlopen):
    """Ensure the DEPLOYED router is healthy before `serves up` (serves are only
    reachable behind it).

    Reuses the `router` verb's own machinery — its `status_summary` health-check
    path and its `cmd_up` bring-up code path — rather than re-deriving either, so
    "healthy" and "start" mean exactly what `anvil-serving router status`/`router up`
    mean. Idempotent: a healthy router is left untouched (no restart). `--no-router`
    skips the whole step (offline/serve-only workflows); `--dry-run` reports the
    action without performing it. Prints one `router: …` line describing what it did.

    Returns 0 when the router is (or would be) healthy or the step was skipped; the
    non-zero `router up` return code when a real bring-up failed. Router bring-up is
    a safety net, not a gate — the caller reports the failure but still brings serves
    up (a failed router does not make the serves themselves un-startable).

    router_manage is imported lazily: it does `from .serves import docker_state` at
    module load, so a top-level import here would be circular.
    """
    from . import router_manage

    if no_router:
        print("router: skipped (--no-router)")
        return 0
    if container is None:
        container = router_manage.DEFAULT_CONTAINER
    # Reuse the `router status` health-check path verbatim (its status_summary /
    # _health), rather than re-deriving either.
    summary = router_manage.status_summary(container, _run=_run, _open=_open)
    if summary.get("docker_state") == "error":
        # docker is unreachable — we can neither probe nor bring the router up.
        print("router: cannot determine health (docker unavailable) -- bringing serves up anyway")
        return 1
    # "healthy" == the container is running (and not in a docker error state). A
    # positive loopback HTTP code is EXTRA confirmation, but its ABSENCE is not
    # proof of unhealth: a router deployed with ROUTER_PUBLISH=<tailnet-ip>
    # publishes 8000 on that IP, not 127.0.0.1, so the loopback probe returns
    # nothing even when the front door is up and docker-healthy (it answers 401
    # on the tailnet address). Requiring a loopback 200 here would needlessly
    # RESTART every tailnet-published router on each `serves up` — the exact
    # opposite of "if already healthy, do nothing". So a running container is
    # treated as healthy; only a genuinely-down (absent/exited) one is started.
    if summary.get("running"):
        print("router: already healthy")
        return 0
    if dry_run:
        print("router: not healthy -> would start (dry-run)")
        return 0
    print("router: not healthy -> starting")
    compose_path = compose or router_manage.resolve_compose_path(None)
    if env_file is None:
        env_file = router_manage._default_env_file()
    rc = router_manage.cmd_up(
        compose_path, router_manage.DEFAULT_SERVICE,
        env_file=env_file, dry_run=False, _run=_run,
    )
    if rc == 0:
        print("router: started")
    else:
        print("router: FAILED to start (see above) -- bringing serves up anyway")
    return rc


_STORAGE_PROBE_NAME = ".anvil-serving-write-probe"


def _container_mount_plan(container, _run):
    """(uid, gid, volume mounts) of a running container, or None when unprobeable.

    `docker exec` (not `Config.User`, which may be empty or a name the host
    can't resolve) answers what identity the workload ACTUALLY runs as. A
    container without `sh` (scratch/distroless) can't be probed — or repaired —
    this way, so the caller treats None as "note it and move on" rather than
    failing bring-ups for an image class the check cannot see into.
    """
    ident = _run_or(["docker", "exec", container, "sh", "-c", "id -u; id -g"],
                    None, _run, capture_output=True, text=True)
    if ident is None or ident.returncode != 0:
        return None
    parts = ident.stdout.split()
    if len(parts) < 2 or not all(p.isdigit() for p in parts[:2]):
        return None
    mounts_raw = _run_or(["docker", "inspect", "-f", "{{json .Mounts}}", container],
                         None, _run, capture_output=True, text=True)
    if mounts_raw is None or mounts_raw.returncode != 0:
        return None
    try:
        mounts = json.loads(mounts_raw.stdout or "null") or []
    except ValueError:
        return None
    # Named volumes only: bind mounts reach into the HOST filesystem (on the
    # reference fleet that is a 9P path into Windows) which is not ours to
    # chown, and a read-only mount fails a write probe by design.
    volumes = [m for m in mounts
               if isinstance(m, dict)
               and m.get("Type") == "volume"
               and m.get("RW")
               and m.get("Destination")]
    return int(parts[0]), int(parts[1]), volumes


def _volume_other_users(volume, container, _run):
    """Names of OTHER containers (any state) that mount `volume`."""
    r = _run_or(["docker", "ps", "-a", "--filter", "volume=%s" % volume,
                 "--format", "{{.Names}}"], None, _run,
                capture_output=True, text=True)
    if r is None or r.returncode != 0:
        return []
    return [n for n in r.stdout.split() if n and n != container]


def _storage_write_check(s, _run):
    """Post-start guard: the container must be able to WRITE its volume mounts.

    Docker copies an image directory's contents and their uid/gid into a named
    volume only while that volume is still EMPTY. Any bring-up that pre-creates
    a volume's layout (required whenever compose mounts subpaths — docker does
    not create missing subpaths on mount) defeats that donation, leaving the
    directories root-owned while the image runs as a non-root user. The failure
    mode is nasty precisely because readiness cannot see it: the ComfyUI tenant
    answered /system_stats while every write failed (sqlite "unable to open
    database file", PermissionError on /app/user/default per UI request).

    So: probe every RW named-volume mount as the container's real runtime
    identity. A denied mount resolves three ways:

    - backing volume declared in the serve's `shared_volumes` -> the sharing
      is a deployment decision and so is the ownership; refuse auto-repair
      and print the manual command.
    - backing volume shared at runtime but NOT declared -> topology fault:
      report the undeclared sharing itself (declare it or separate the
      storage); never silently re-own storage another tenant mounts.
    - unshared -> repair (chown to the runtime identity via `docker exec
      --user 0`), restart the container so failed init re-runs, re-verify.

    Prints its own evidence; returns False when the serve is left unable to
    write (the caller fails that serve and skips its readiness wait).
    """
    container = s["container"]
    plan = _container_mount_plan(container, _run)
    if plan is None:
        print("  storage: cannot probe %s runtime identity/mounts "
              "(no shell in image?) -- write check skipped" % container)
        return True
    uid, gid, volumes = plan
    if uid == 0 or not volumes:
        return True

    def _denied(paths):
        failed = []
        for dest in paths:
            probe = "%s/%s" % (dest.rstrip("/"), _STORAGE_PROBE_NAME)
            quoted_probe = shlex.quote(probe)
            r = _run_or(["docker", "exec", container, "sh", "-c",
                         "touch %s && rm -f %s" % (quoted_probe, quoted_probe)],
                        None, _run, capture_output=True, text=True)
            if r is None or r.returncode != 0:
                failed.append(dest)
        return failed

    denied = _denied([m["Destination"] for m in volumes])
    if not denied:
        print("  storage: %d/%d volume mounts writable (uid %d)"
              % (len(volumes), len(volumes), uid))
        return True

    declared_shared = set(s.get("shared_volumes") or [])
    declared, undeclared = {}, {}
    for mount in volumes:
        if mount["Destination"] not in denied:
            continue
        volume = mount.get("Name", "")
        if volume in declared_shared:
            declared.setdefault(volume, []).append(mount["Destination"])
            continue
        others = _volume_other_users(volume, container, _run)
        if others:
            undeclared.setdefault(volume, set()).update(others)
    if declared or undeclared:
        print("  FAILED: %s cannot write %s as uid %d"
              % (container, " ".join(denied), uid))
        for volume, dests in sorted(declared.items()):
            print("    volume %s (%s) is declared shared_volumes: ownership "
                  "is the deployment's decision -- fix it where the sharing "
                  "is declared, e.g.:" % (volume, " ".join(dests)))
            print("    docker exec --user 0 %s chown -R %d:%d %s"
                  % (container, uid, gid, " ".join(dests)))
        for volume, others in sorted(undeclared.items()):
            print("    volume %s is shared with %s but NOT declared in this "
                  "serve's shared_volumes -- undeclared sharing is a topology "
                  "fault: declare it (and decide its ownership) or give this "
                  "serve its own volume"
                  % (volume, ", ".join(sorted(others))))
        return False

    print("  storage: %s not writable by uid %d -> repairing"
          % (" ".join(denied), uid))
    repair = _run_or(["docker", "exec", "--user", "0", container,
                      "chown", "-R", "%d:%d" % (uid, gid)] + denied,
                     None, _run, capture_output=True, text=True)
    if repair is None or repair.returncode != 0:
        print("  FAILED: could not chown %s to %d:%d: %s" % (
            " ".join(denied), uid, gid,
            ((repair.stderr or repair.stdout) if repair else "docker missing").strip()))
        return False
    # Restart so whatever failed during the broken boot (sqlite init, config
    # writes) re-runs against the repaired storage; `docker restart` blocks
    # until the container is running again.
    restarted = _run_or(["docker", "restart", container], None, _run,
                        capture_output=True, text=True)
    if restarted is None or restarted.returncode != 0:
        print("  FAILED: chowned %s but could not restart %s: %s" % (
            " ".join(denied), container,
            ((restarted.stderr or restarted.stdout) if restarted else "docker missing").strip()))
        return False
    still = _denied(denied)
    if still:
        print("  FAILED: %s still cannot write %s after repair -- "
              "inspect the volume contents manually" % (container, " ".join(still)))
        return False
    print("  storage: chowned %s to %d:%d and restarted %s; all mounts writable"
          % (" ".join(denied), uid, gid, container))
    return True


def cmd_up(serves, names, dry_run=False, recreate=False, _run=subprocess.run,
           evict=False, drain_timeout=EVICTION_DRAIN_TIMEOUT, router_url=None,
           _transition=None, wait_for_readiness=False,
           readiness_timeout=LIFECYCLE_READINESS_TIMEOUT_SECONDS,
           readiness_poll=LIFECYCLE_READINESS_POLL_SECONDS, ledger_serves=None,
           _open=urllib.request.urlopen, _sleep=time.sleep,
           _allow_exclusive_target=False):
    targets = _select(serves, names)
    if not targets:
        print("no matching serves in manifest")
        return 1
    if evict and (
        isinstance(drain_timeout, bool)
        or not isinstance(drain_timeout, numbers.Real)
        or not math.isfinite(drain_timeout) or drain_timeout <= 0
    ):
        print("--drain-timeout must be a finite positive number of seconds")
        return 2
    reservation_scope = ledger_serves if ledger_serves is not None else serves
    state_cache = docker_states(
        [serve["container"] for serve in reservation_scope],
        _run=_run,
    )

    def state_of(container):
        return state_cache.get(container, "absent")
    # ADR-0017 reservation ledger admission: acquiring the targets' declared
    # VRAM reservations must fit their gpu_role budgets BEFORE any container
    # command runs — an over-budget request fails the whole batch with the
    # ledger printed, and nothing is started/recreated. Committed state is
    # derived from docker (running serves), so `serves down` releases a
    # reservation with no ledger bookkeeping. Read-only, so it also gates
    # --dry-run (the preview should show the same refusal the real run hits).
    # Serves/manifests without reservation fields skip this entirely.
    exclusive_denial = reservations.deny_exclusive_conflict(
        reservation_scope,
        targets,
        state_of,
        allow_exclusive_target=_allow_exclusive_target,
    )
    if exclusive_denial:
        for line in exclusive_denial:
            print("  " + line)
        return 1
    denial = reservations.deny_over_budget(reservation_scope, targets, state_of)
    if denial and evict:
        # ADR-0017 §5 eviction (gpu-reservations:T005): an over-budget
        # `on-demand` acquisition may stop committed `evictable` reservations
        # instead of failing — composing the ADR-0018 transition (quiesce +
        # bounded AdmissionLease drain) before each victim's container stops.
        # `resident` serves are never candidates; an impossible plan is the
        # same loud, ledger-printing refusal as plain admission.
        victims, lines = reservations.plan_eviction(reservation_scope, targets, state_of)
        if victims is None:
            for line in lines:
                print("  " + line)
            return 1
        if victims:
            transition = _transition or (
                lambda action, tier_id, timeout=None: _transition_cli(
                    router_url or DEFAULT_ROUTER_URL, action, tier_id,
                    timeout=timeout, reason="eviction", _run=_run))
            evict_rc = _evict_victims(
                reservation_scope, victims, dry_run=dry_run, drain_timeout=drain_timeout,
                transition=transition, _run=_run)
            if evict_rc != 0:
                return evict_rc
        if dry_run:
            # The preview stopped nothing, so the ledger still shows the old
            # commitments; the plan above is the preview of their release.
            denial = None
        else:
            # Re-derive admission from live docker state: the victims are
            # stopped, so the request must now fit (fail loudly if not —
            # e.g. a victim's restart policy revived it).
            state_cache = docker_states(
                [serve["container"] for serve in reservation_scope],
                _run=_run,
            )
            denial = reservations.deny_over_budget(reservation_scope, targets, state_of)
    if denial:
        for line in denial:
            print("  " + line)
        if not evict:
            victims, _ = reservations.plan_eviction(reservation_scope, targets, state_of)
            if victims:
                print("  (re-run with --evict to stop evictable serve(s) %s "
                      "via a drained ADR-0018 transition)" % ", ".join(
                          victim.serve for victim in victims))
        return 1
    rc = 0
    for s in targets:
        st = docker_state(s["container"], _run=_run)
        if st == "error":
            print("  %s: cannot determine state (docker missing / daemon down / "
                  "permission?)" % s["container"])
            rc = 1
            continue
        if st in ("restarting", "removing", "dead", "unknown") and not (recreate and st == "dead"):
            # exotic / transitional state -> don't fresh-create (collision/destroy risk).
            # Exception: an explicit `--recreate` may rescue a `dead` container — it's a
            # terminal (not running) state, so a `docker rm -f` + fresh `up` is safe. The
            # other states stay hands-off even under --recreate.
            print("  %s: in state %r -- not auto-started; resolve manually" % (s["container"], st))
            rc = 1
            continue

        up = s.get("up")
        compose = _is_compose_up(up)
        if compose:
            expected_project = _expected_compose_project(s)
            up = _compose_up_with_project(up, expected_project)
            if st not in {"absent", "error"}:
                observed_project = docker_compose_project(s["container"], _run=_run)
                if (
                    observed_project
                    and observed_project != expected_project
                    and not recreate
                ):
                    print(
                        "  %s: stack ownership mismatch for stack %r "
                        "(observed project %r, expected %r); "
                        "rerun `serves up %s --recreate` to replace only "
                        "this container under the managed stack"
                        % (
                            s["container"], s.get("stack", DEFAULT_STACK),
                            observed_project, expected_project,
                            s["name"],
                        )
                    )
                    rc = 1
                    continue

        if recreate:
            # Explicit clean recreate from `up` (compose OR script): force-remove the
            # existing container, then run the fresh-create `up`.
            if not up:
                print("  %s: --recreate requested but no `up` command in manifest -- "
                      "cannot recreate; resolve manually" % s["container"])
                rc = 1
                continue
            if st == "absent":
                # Nothing to remove — a `docker rm -f` of a nonexistent container errors
                # (exit 1) and would abort the fresh `up`. So `--recreate` also bootstraps
                # a serve that isn't there yet: just run `up`.
                steps = [up]
                desc = "up %s (--recreate, none present): %s" % (s["name"], " ".join(up))
            else:
                steps = [["docker", "rm", "-f", s["container"]], up]
                desc = "recreate %s: docker rm -f + %s" % (s["container"], " ".join(up))
        elif st == "absent":
            if not up:
                print("  %s: absent and no `up` command in manifest -- start it "
                      "manually (see examples/fakoli-dark/)" % s["name"])
                rc = 1
                continue
            steps, desc = [up], "up %s: %s" % (s["name"], " ".join(up))
        elif st == "paused":
            # A paused container (compose OR script) still pins 100% of its VRAM; resume
            # it with `docker unpause`. Handled BEFORE the compose branch so a paused
            # compose serve isn't routed through `docker compose up -d` (which would not
            # unpause it) and left stuck paused.
            steps, desc = [["docker", "unpause", s["container"]]], "unpause %s" % s["container"]
        elif compose:
            # `docker compose up -d` natively recreates the container when its compose
            # config changed and fast-(re)starts it (a cheap no-op) otherwise — so we run
            # `up` UNCONDITIONALLY, even when the container is already running. That is the
            # whole point of ADR-0002: edit the compose file, re-run `serves up`, and the
            # container is recreated to match, instead of a blind "already running" skip or
            # a `docker start` silently resurrecting the container's STALE model. Drift-
            # safety for free; no bespoke config-hashing needed.
            steps = [up]
            desc = "compose up %s: %s" % (s["name"], " ".join(up))
        elif st == "running":
            _warn_drift(s, _run=_run)  # script serve: can't self-heal, so at least warn
            steps, desc = [], "%s: already running" % s["container"]
        else:  # exited / created -- a `docker run` script serve
            # A `docker run` script can't be re-run over an existing container (name
            # clash), so we `docker start` it — but that resurrects whatever model it
            # was CREATED with. Warn loudly on drift; the fix is `--recreate` or compose.
            _warn_drift(s, _run=_run)
            steps = [["docker", "start", s["container"]]]
            desc = ("start %s (restart existing container; convert to a compose serve "
                    "or use --recreate for drift-safety)" % s["container"])

        print("  " + desc)
        if dry_run:
            continue
        env = _serve_env(s)
        for step in steps:
            r = _run(step, capture_output=True, text=True, env=env)
            if r.returncode != 0:
                print("  FAILED: %s" % (r.stderr or r.stdout or "").strip())
                rc = 1
                break
        else:
            ready = True
            # Storage guard BEFORE the readiness wait: a serve can answer its
            # health endpoint while unable to write its volumes (the 2026-08-09
            # ComfyUI defect), and if a repair restart is needed it must happen
            # before we start the readiness clock, not after it passes.
            if not _storage_write_check(s, _run):
                rc = 1
                continue
            if wait_for_readiness:
                print(
                    "  waiting up to %ss for %s%s"
                    % (readiness_timeout, s["name"], s["health"])
                )
                if not _await_healthy(
                    s,
                    readiness_timeout,
                    readiness_poll,
                    _open=_open,
                    _sleep=_sleep,
                ):
                    print(
                        "  FAILED: %s did not become ready at "
                        "http://127.0.0.1:%s%s within %ss"
                        % (
                            s["name"], s["port"], s["health"],
                            readiness_timeout,
                        )
                    )
                    ready = False
                    rc = 1
            if steps and ready and (recreate or st != "running"):
                # Record serve.up only when the container was not already
                # running before this invocation, EXCEPT for an explicit
                # --recreate, which provably removes and recreates the
                # container (docker rm -f + up) — a real lifecycle change.
                # For a compose serve whose container was already running
                # without --recreate, `docker compose up -d` is a cheap
                # drift-recreate-or-no-op; we cannot tell from the CLI
                # whether the container actually changed, so we must not
                # create a false serve.up history entry for an already-
                # running state.
                gpu_roles = s.get("gpu_roles")
                if gpu_roles is None and s.get("gpu_role"):
                    gpu_roles = [s["gpu_role"]]
                if not _record_lifecycle_event(
                    "serve.up",
                    {
                        "serve": s["name"],
                        "model": s.get("served_name", s.get("model")),
                        "port": s["port"],
                        "gpu_roles": gpu_roles or [],
                        "residency": s.get("residency"),
                    },
                ):
                    rc = 1
    return rc


def operating_mode_plan(serves, target_name, restore_group, state_of):
    """Return a structured, side-effect-free TP=2 transition plan."""
    matched = _select(serves, [target_name])
    if len(matched) != 1 or matched[0]["name"] != target_name:
        raise ValueError("exclusive mode target must name exactly one manifest serve")
    target = matched[0]
    if not reservations.is_exclusive(target):
        raise ValueError(
            "%s is not declared operating_mode = %r"
            % (target_name, DUAL_GPU_EXCLUSIVE_MODE)
        )
    roles = [r.gpu_role for r in reservations.reservations_of(target)]
    if len(roles) != 2 or target.get("tensor_parallel_size") != 2:
        raise ValueError("exclusive target must reserve two GPU roles with TP size 2")
    rollback = resolve_group(serves, restore_group)
    if not rollback:
        raise ValueError("restore group %r has no serves" % restore_group)
    if any(s["name"] == target_name for s in rollback):
        raise ValueError("restore group must not include the exclusive target")

    states = {}
    for serve in serves:
        if reservations.is_gpu_inference(serve):
            states[serve["name"]] = state_of(serve["container"])
    unresolved = [
        {"serve": name, "state": state}
        for name, state in states.items()
        if state in {"error", "unknown", "removing"}
    ]
    competitors = [
        serve for serve in serves
        if serve["name"] != target_name
        and reservations.is_gpu_inference(serve)
        and states.get(serve["name"]) in reservations.RESERVED_STATES
    ]
    return {
        "mode": DUAL_GPU_EXCLUSIVE_MODE,
        "target": target_name,
        "gpu_roles": roles,
        "tensor_parallel_size": 2,
        "drain": [
            {"serve": serve["name"], "router_tier": serve.get("router_tier")}
            for serve in competitors
        ],
        "stop": [serve["name"] for serve in competitors],
        "blocked": [
            serve["name"] for serve in serves
            if serve["name"] != target_name and reservations.is_gpu_inference(serve)
        ],
        "unresolved": unresolved,
        "rollback": {
            "group": restore_group,
            "serves": [serve["name"] for serve in rollback],
        },
    }


def _mode_router_plan(serves, target, plan):
    """Validate the complete direct-router swap for a routed exclusive owner."""
    tier_id = target.get("router_tier")
    if not tier_id:
        return None
    from .router.config import ConfigError, load as load_router_config

    target_config = load_router_config(target["router_config"])
    rollback_config = load_router_config(target["rollback_router_config"])
    try:
        target_tier = target_config.tier(tier_id)
        rollback_tier = rollback_config.tier(tier_id)
    except ConfigError as exc:
        raise ValueError(
            "exclusive router profiles must both declare tier %r" % tier_id
        ) from exc
    if target_tier.model != target["served_name"]:
        raise ValueError(
            "exclusive router profile tier %r model %r does not match target %r"
            % (tier_id, target_tier.model, target["served_name"])
        )
    target_aliases = {
        alias for alias, selected in target_config.model_routes.items()
        if selected == tier_id
    }
    rollback_aliases = {
        alias for alias, selected in rollback_config.model_routes.items()
        if selected == tier_id
    }
    if not target_aliases:
        raise ValueError(
            "exclusive router profile does not route any alias to tier %r" % tier_id
        )
    if target_aliases != rollback_aliases:
        raise ValueError(
            "exclusive and rollback router profiles must route the same aliases "
            "to tier %r" % tier_id
        )
    rollback_serves = [
        serve for serve in _select(serves, plan["rollback"]["serves"])
        if serve.get("router_tier") == tier_id
        and serve["served_name"] == rollback_tier.model
    ]
    if len(rollback_serves) != 1:
        raise ValueError(
            "rollback router profile tier %r model %r must match exactly one "
            "serve in restore group %r"
            % (tier_id, rollback_tier.model, plan["rollback"]["group"])
        )
    return {
        "tier": tier_id,
        "aliases": sorted(target_aliases),
        "router_config": target["router_config"],
        "rollback_router_config": target["rollback_router_config"],
    }


def _print_operating_mode_plan(plan):
    print("mode: %s" % plan["mode"])
    print("target: %s (TP=%s)" % (plan["target"], plan["tensor_parallel_size"]))
    print("gpu roles: %s" % ", ".join(plan["gpu_roles"]))
    if plan["drain"]:
        for item in plan["drain"]:
            if item["router_tier"]:
                print("  drain %s via router tier %s" % (
                    item["serve"], item["router_tier"],
                ))
            else:
                print("  drain %s: no router tier declared" % item["serve"])
    else:
        print("  drain: none")
    print("stop: %s" % (", ".join(plan["stop"]) or "none"))
    print("blocked while active: %s" % (", ".join(plan["blocked"]) or "none"))
    print("rollback group %s: %s" % (
        plan["rollback"]["group"], ", ".join(plan["rollback"]["serves"]),
    ))
    if plan.get("router"):
        print("router aliases: %s -> %s" % (
            ", ".join(plan["router"]["aliases"]), plan["router"]["tier"],
        ))
        print("router profile: %s" % plan["router"]["router_config"])
        print("router rollback profile: %s" % (
            plan["router"]["rollback_router_config"]
        ))
    for item in plan["unresolved"]:
        print("UNRESOLVED: %s state %s" % (item["serve"], item["state"]))


def _restore_split_stack(
    serves,
    plan,
    *,
    transition,
    _run,
    _open,
    _sleep,
    readiness_timeout=LIFECYCLE_READINESS_TIMEOUT_SECONDS,
    readiness_poll=LIFECYCLE_READINESS_POLL_SECONDS,
    skip_readmit_when_router_stopped=False,
):
    names = plan["rollback"]["serves"]
    rc = cmd_up(
        serves,
        names,
        ledger_serves=serves,
        wait_for_readiness=True,
        readiness_timeout=readiness_timeout,
        readiness_poll=readiness_poll,
        _run=_run,
        _open=_open,
        _sleep=_sleep,
    )
    if rc != 0:
        return rc
    tiers = [
        serve.get("router_tier") for serve in _select(serves, names)
        if serve.get("router_tier")
    ]
    if tiers and skip_readmit_when_router_stopped:
        router_state = docker_state(DEFAULT_ROUTER_CONTAINER, _run=_run)
        if router_state == "absent" or router_state in _STOPPED:
            print(
                "  router %s is %s; restored serves need no live readmit"
                % (DEFAULT_ROUTER_CONTAINER, router_state)
            )
            return 0
    failed = [tier for tier in dict.fromkeys(tiers) if transition("readmit", tier) != 0]
    if failed:
        print("  split restore remains quiesced for: %s" % ", ".join(failed))
        return 2
    return 0


def cmd_mode(
    serves,
    action,
    target_name,
    restore_group,
    *,
    confirm=False,
    dry_run=False,
    drain_timeout=EVICTION_DRAIN_TIMEOUT,
    preserve_on_failure=False,
    readiness_timeout=LIFECYCLE_READINESS_TIMEOUT_SECONDS,
    readiness_poll=LIFECYCLE_READINESS_POLL_SECONDS,
    router_url=None,
    _transition=None,
    _run=subprocess.run,
    _open=urllib.request.urlopen,
    _sleep=time.sleep,
    _install_config=None,
):
    """Preview, enter, leave, or report the exclusive TP=2 operating mode."""
    gpu_containers = [
        serve["container"] for serve in serves
        if reservations.is_gpu_inference(serve)
    ]
    states = docker_states(gpu_containers, _run=_run)

    def state_of(container):
        return states.get(container, "absent")

    if action == "status":
        summary = operating_mode_summary(serves, state_of)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["mode"] != "unresolved" else 1

    try:
        plan = operating_mode_plan(serves, target_name, restore_group, state_of)
        target = _select(serves, [target_name])[0]
        plan["router"] = _mode_router_plan(serves, target, plan)
    except ValueError as exc:
        print("mode transition refused: %s" % exc, file=sys.stderr)
        return 2
    _print_operating_mode_plan(plan)
    if plan["unresolved"]:
        print("mode transition refused before container mutation: unresolved GPU workload")
        return 1
    if action == "preview" or dry_run:
        return 0
    if not (confirm or guard.confirmation_authorized()):
        print("mode transition not applied; rerun with --confirm")
        return 2

    uses_default_managed_router = router_url is None and _transition is None
    managed_router_state = (
        docker_state(DEFAULT_ROUTER_CONTAINER, _run=_run)
        if uses_default_managed_router else None
    )
    router_is_offline = (
        uses_default_managed_router
        and (managed_router_state == "absent" or managed_router_state in _STOPPED)
    )
    if router_is_offline:
        def transition(transition_action, tier_id, timeout=None):
            del timeout
            print(
                "  router %s is %s; %s for tier %s is not applicable"
                % (
                    DEFAULT_ROUTER_CONTAINER,
                    managed_router_state,
                    transition_action,
                    tier_id,
                )
            )
            return 0
    else:
        transition = _transition or (
            lambda transition_action, tier_id, timeout=None: _transition_cli(
                router_url or DEFAULT_ROUTER_URL,
                transition_action,
                tier_id,
                timeout=timeout,
                reason="mode-transition",
                _run=_run,
            )
        )
    skip_offline_router_readmit = uses_default_managed_router
    if plan["router"] and router_is_offline:
        print(
            "mode transition refused before container mutation: routed exclusive "
            "mode requires the managed router to be running"
        )
        return 1
    install_config = _install_config or _install_router_config

    def install_router_profile(config_file):
        rc = install_config(config_file, _run=_run)
        if rc != 0:
            return rc
        gateway = (router_url or DEFAULT_ROUTER_URL).rstrip("/") + "/healthz"
        status = _await_gateway(
            gateway, 60, 1, _open=_open, _sleep=_sleep,
        )
        if status != 200:
            print("  router profile installed but gateway health returned HTTP %s" % status)
            return 1
        return 0

    def restore_split_with_router():
        config_rc = 0
        if plan["router"]:
            config_rc = install_router_profile(
                plan["router"]["rollback_router_config"]
            )
            if config_rc != 0:
                print("  CRITICAL: rollback router profile was not restored")
        stack_rc = _restore_split_stack(
            serves, plan, transition=transition, _run=_run,
            _open=_open, _sleep=_sleep,
            readiness_timeout=readiness_timeout,
            readiness_poll=readiness_poll,
            skip_readmit_when_router_stopped=skip_offline_router_readmit,
        )
        return 0 if config_rc == 0 and stack_rc == 0 else 1

    def stop_failed_target():
        if preserve_on_failure:
            cleanup_rc = cmd_down(
                serves, [target_name], keep_container=True, _run=_run,
            )
            preserved_state = docker_state(target["container"], _run=_run)
            safely_preserved = cleanup_rc == 0 and preserved_state in _STOPPED
            if safely_preserved:
                print(
                    "  preserved failed target %s in state %s; inspect with "
                    "`anvil-serving serves logs %s --manifest PATH`"
                    % (target["container"], preserved_state, target_name)
                )
                return
            print(
                "  WARNING: failed target could not be safely preserved "
                "(state %s); removing it before split restore" % preserved_state
            )
            cmd_down(serves, [target_name], force_remove=True, _run=_run)
            return
        cmd_down(serves, [target_name], _run=_run)

    if action == "enter":
        victims = []
        for name in plan["stop"]:
            serve = _select(serves, [name])[0]
            declared = reservations.reservations_of(serve)
            victims.append(
                declared[0] if declared else reservations.GpuReservation(
                    serve=serve["name"],
                    container=serve["container"],
                    gpu_role="<unassigned>",
                    vram_mib=0,
                    residency=serve.get("residency"),
                    state=states.get(serve["name"]),
                )
            )
        if victims and _evict_victims(
            serves,
            victims,
            drain_timeout=drain_timeout,
            transition=transition,
            _run=_run,
        ) != 0:
            print("mode entry failed while draining; restoring split stack")
            _restore_split_stack(
                serves, plan, transition=transition, _run=_run,
                _open=_open, _sleep=_sleep,
                readiness_timeout=readiness_timeout,
                readiness_poll=readiness_poll,
                skip_readmit_when_router_stopped=skip_offline_router_readmit,
            )
            return 1
        states = docker_states(gpu_containers, _run=_run)
        remaining = []
        for serve in serves:
            if serve["name"] == target_name or not reservations.is_gpu_inference(serve):
                continue
            state = state_of(serve["container"])
            if state in reservations.RESERVED_STATES or state in {"error", "unknown", "removing"}:
                remaining.append("%s=%s" % (serve["name"], state))
        if remaining:
            print("mode entry refused: GPU workloads remain: %s" % ", ".join(remaining))
            _restore_split_stack(
                serves, plan, transition=transition, _run=_run,
                _open=_open, _sleep=_sleep,
                readiness_timeout=readiness_timeout,
                readiness_poll=readiness_poll,
                skip_readmit_when_router_stopped=skip_offline_router_readmit,
            )
            return 1
        rc = cmd_up(
            serves,
            [target_name],
            ledger_serves=serves,
            wait_for_readiness=True,
            readiness_timeout=readiness_timeout,
            readiness_poll=readiness_poll,
            _allow_exclusive_target=True,
            _run=_run,
            _open=_open,
            _sleep=_sleep,
        )
        if rc == 0:
            if plan["router"]:
                config_rc = install_router_profile(plan["router"]["router_config"])
                readmit_rc = (
                    transition("readmit", plan["router"]["tier"])
                    if config_rc == 0 else 1
                )
                if config_rc != 0 or readmit_rc != 0:
                    print(
                        "mode entry failed while activating router tier %s; "
                        "restoring split stack" % plan["router"]["tier"]
                    )
                    stop_failed_target()
                    restore_split_with_router()
                    return 1
            print("mode entered: %s owns %s" % (
                target_name, ", ".join(plan["gpu_roles"]),
            ))
            return 0
        print("mode entry failed while starting target; restoring split stack")
        stop_failed_target()
        restore_split_with_router()
        return 1

    if action == "leave":
        if state_of(target["container"]) not in reservations.RESERVED_STATES:
            print("mode leave refused: %s is not the active exclusive owner" % target_name)
            return 1
        if plan["router"]:
            tier = plan["router"]["tier"]
            if transition("quiesce", tier) != 0 or transition(
                "drain", tier, timeout=drain_timeout
            ) != 0:
                print("mode leave refused: could not quiesce and drain %s" % tier)
                transition("readmit", tier)
                return 1
        if cmd_down(serves, [target_name], force_remove=True, _run=_run) != 0:
            print("mode leave failed: exclusive owner did not stop")
            if plan["router"]:
                transition("readmit", plan["router"]["tier"])
            return 1
        if restore_split_with_router() == 0:
            print("mode left: restored split group %s" % restore_group)
            return 0
        print("mode leave rollback: split restore failed; returning to exclusive owner")
        cmd_down(serves, plan["rollback"]["serves"], _run=_run)
        target_rc = cmd_up(
            serves,
            [target_name],
            ledger_serves=serves,
            wait_for_readiness=True,
            readiness_timeout=readiness_timeout,
            readiness_poll=readiness_poll,
            _allow_exclusive_target=True,
            _run=_run,
            _open=_open,
            _sleep=_sleep,
        )
        if plan["router"] and target_rc == 0:
            config_rc = install_router_profile(plan["router"]["router_config"])
            if config_rc == 0:
                transition("readmit", plan["router"]["tier"])
        return 1

    print("unknown mode action %r" % action, file=sys.stderr)
    return 2


def profile_transition_action(profile, mode_summary, *, apply=False):
    """Resolve a declared profile to its one safe mode transaction.

    Split profiles are intentionally reachable only by leaving their declared
    exclusive owner; an already-split host has no durable proof of which router
    profile was installed, so an apply refuses rather than silently treating an
    arbitrary split stack as the requested profile.
    """
    current = mode_summary["mode"]
    target = profile["exclusive_target"]
    if current == "unresolved":
        raise ServeProfileError("current serving mode is unresolved")
    if profile["mode"] == DUAL_GPU_EXCLUSIVE_MODE:
        if current == DUAL_GPU_EXCLUSIVE_MODE:
            if mode_summary["exclusive_owner"] == target:
                return "noop"
            raise ServeProfileError(
                "exclusive owner %r is active; profile %r requires split first"
                % (mode_summary["exclusive_owner"], profile["id"])
            )
        return "enter"
    if current == DUAL_GPU_EXCLUSIVE_MODE:
        if mode_summary["exclusive_owner"] != target:
            raise ServeProfileError(
                "exclusive owner %r is active; split profile %r declares %r"
                % (mode_summary["exclusive_owner"], profile["id"], target)
            )
        return "leave"
    if apply:
        raise ServeProfileError(
            "host is already in split mode; refuse to assume profile %r is installed"
            % profile["id"]
        )
    return "leave"


@contextmanager
def _defer_first_profile_interrupt(enabled):
    """Keep one accidental Ctrl-C from abandoning a live profile transaction.

    Lifecycle commands start detached containers and then wait on readiness.  A
    default SIGINT during that wait exits Python without undoing the detached
    work.  For a confirmed profile apply, defer the first SIGINT so the existing
    success-or-rollback path can finish.  A second SIGINT preserves the normal
    emergency-abort behavior and may leave a partial transition, which is stated
    explicitly in the warning.
    """
    if not enabled:
        yield
        return

    previous = signal.getsignal(signal.SIGINT)
    count = 0

    def handler(signum, frame):
        nonlocal count
        count += 1
        if count == 1:
            print(
                "profile transition still active; first Ctrl-C deferred so "
                "success or rollback can finish (press Ctrl-C again to force "
                "exit and then inspect `serves mode status`)",
                file=sys.stderr,
            )
            return
        if previous == signal.SIG_IGN:
            return
        if callable(previous):
            previous(signum, frame)
            return
        signal.default_int_handler(signum, frame)

    installed = False
    try:
        signal.signal(signal.SIGINT, handler)
        installed = True
    except ValueError:
        # Signal handlers can only be installed by the main Python thread.
        pass
    try:
        yield
    finally:
        if installed:
            signal.signal(signal.SIGINT, previous)


def cmd_profile(
    serves,
    profiles,
    action,
    profile_id=None,
    *,
    confirm=False,
    dry_run=False,
    drain_timeout=EVICTION_DRAIN_TIMEOUT,
    router_url=None,
    _run=subprocess.run,
):
    """List, preview, or apply a named topology profile through ``cmd_mode``."""
    if action == "list":
        for profile in profiles:
            print(
                "%s\t%s\texclusive=%s\trestore-group=%s"
                % (
                    profile["id"],
                    profile["mode"],
                    profile["exclusive_target"],
                    profile["restore_group"],
                )
            )
        return 0

    profile = select_serve_profile(profiles, profile_id)
    states = docker_states(
        [serve["container"] for serve in serves if reservations.is_gpu_inference(serve)],
        _run=_run,
    )
    summary = operating_mode_summary(serves, lambda container: states.get(container, "absent"))
    transition = profile_transition_action(
        profile, summary, apply=action == "apply" and not dry_run
    )
    print(
        "serving profile %s: %s (current=%s, exclusive-owner=%s)"
        % (profile["id"], transition, summary["mode"], summary["exclusive_owner"] or "none")
    )
    if transition == "noop":
        print("profile already active; no lifecycle or router change made")
        return 0
    with _defer_first_profile_interrupt(
        action == "apply" and not dry_run and confirm
    ):
        result = cmd_mode(
            serves,
            "preview" if action == "preview" else transition,
            profile["exclusive_target"],
            profile["restore_group"],
            confirm=confirm,
            dry_run=dry_run,
            drain_timeout=drain_timeout,
            readiness_timeout=profile["startup_timeout"],
            readiness_poll=profile["poll_interval"],
            router_url=router_url,
            _run=_run,
        )
        if result == 0 and action == "apply" and not dry_run:
            if not _record_lifecycle_event(
                "profile.enter" if transition == "enter" else "profile.leave",
                {
                    "profile": profile["id"],
                    "mode": profile["mode"],
                    "exclusive_target": profile["exclusive_target"],
                    "restore_group": profile["restore_group"],
                },
            ):
                return 1
        return result


def cmd_rm(serves, names, dry_run=False, assume_yes=False, _run=subprocess.run,
           _input=input):
    """Force-remove serve container(s) — `docker rm -f <container>`.

    THE key case: this works for a container that is NOT in the manifest — an experiment
    container squatting a serve's port. Each token is resolved independently: if it matches
    a manifest serve's name/container (via `_select`), that serve's container is removed;
    otherwise the token is treated LITERALLY as a container name. A container that's already
    'absent' is a no-op success ("nothing to remove"); an 'error' state (docker down /
    daemon unreachable) is NOT reported as success. Docker calls are argv lists (no shell).
    """
    if not names:
        print("no containers named to remove")
        return 1
    # resolve tokens -> container names: an EXACT single manifest match (name OR container)
    # wins; a token matching >1 serve is AMBIGUOUS — refuse it rather than remove a serve the
    # operator didn't target; 0 matches is a literal container name (the non-manifest squatter).
    containers, rc = [], 0
    for tok in names:
        matched = _select(serves, [tok])
        if len(matched) > 1:
            print("  %s: ambiguous -- matches serves %s; pass the exact container name to remove one"
                  % (tok, ", ".join(s["name"] for s in matched)))
            rc = 1
            continue
        c = matched[0]["container"] if matched else tok
        if c not in containers:
            containers.append(c)
    # Gate: `docker rm -f` is irreversible (container + its logs are gone), so
    # it requires an explicit yes — --yes for automation, [y/N] interactively.
    # One prompt for the whole batch (the list is printed), not one per
    # container; --dry-run previews without prompting.
    if containers and not dry_run:
        if not guard.confirm("force-remove %d container(s): %s?"
                             % (len(containers), ", ".join(containers)),
                             assume_yes=assume_yes, _input=_input):
            print("aborted (nothing removed); pass --yes to skip this prompt")
            return 1
    for container in containers:
        st = docker_state(container, _run=_run)
        if st == "error":
            print("  %s: cannot determine state (docker missing / daemon down / "
                  "permission?)" % container)
            rc = 1
            continue
        if st == "absent":
            print("  %s: absent (nothing to remove)" % container)
            continue
        print("  rm -f %s" % container)
        if dry_run:
            continue
        r = _run(["docker", "rm", "-f", container], capture_output=True, text=True)
        if r.returncode == 0:
            print("  removed %s" % container)
        else:
            print("  FAILED to remove %s: %s" % (container, (r.stderr or "").strip()))
            rc = 1
    return rc


def cmd_adopt(serves, names, dry_run=False, assume_yes=False, _run=subprocess.run,
              _input=input):
    """Bring externally-started (non-compose-managed) manifest serve(s) under compose
    management by recreating them via their manifest `up` — i.e. the `cmd_up` recreate
    path (`docker rm -f` + `up`). Use when a serve was started by hand / outside compose
    and you want compose to own its lifecycle going forward.
    """
    targets = _select(serves, names)
    if not targets:
        print("no matching serves in manifest")
        return 1
    for s in targets:
        print("  adopting %s under compose management "
              "(recreate via manifest `up`)" % s["name"])
    # Gate: adoption destroys the hand-started container (`docker rm -f`) before
    # recreating — same irreversibility as `rm`, same explicit-yes requirement.
    if not dry_run:
        if not guard.confirm("recreate %d serve(s) (docker rm -f + up): %s?"
                             % (len(targets), ", ".join(s["name"] for s in targets)),
                             assume_yes=assume_yes, _input=_input):
            print("aborted (nothing adopted); pass --yes to skip this prompt")
            return 1
    # reuse the recreate path: `docker rm -f` the hand-started container + fresh `up`.
    return cmd_up(serves, names, dry_run=dry_run, recreate=True, _run=_run)


def cmd_up_compose(compose_file, services, dry_run=False, _run=subprocess.run):
    """Bring up an ad-hoc/experiment serve from a compose file that is NOT in the manifest:
    `docker compose -f <file> up -d [service...]`. Fully independent of serves.toml — the
    file's services need not be declared there. argv list (no shell) for path/quoting safety.
    """
    argv = ["docker", "compose", "-f", compose_file, "up", "-d", *services]
    print("  compose up: %s" % " ".join(argv))
    if dry_run:
        return 0
    r = _run(argv, capture_output=True, text=True)
    if r.returncode != 0:
        print("  FAILED: %s" % (r.stderr or r.stdout or "").strip())
        return 1
    return 0


def _write_console_safe(stream, value):
    """Write subprocess text without crashing on a narrow Windows console codec."""
    if not value:
        return
    encoding = getattr(stream, "encoding", None) or "utf-8"
    safe = str(value).encode(
        encoding, errors="backslashreplace"
    ).decode(encoding)
    stream.write(safe)


def cmd_logs(serves, names, tail="200", since=None, follow=False, _run=subprocess.run):
    """`docker logs` for ONE model serve's container (resolved from its manifest name), so
    diagnosing a serve doesn't mean reaching for raw docker. `--follow` streams to the terminal."""
    # `logs` targets ONE serve, so a name is REQUIRED — don't inherit `_select`'s empty-means-all
    # (which would silently pick the sole serve on a 1-serve manifest but error on a 2-serve one).
    if not names:
        print("serves logs needs a serve name (e.g. `serves logs heavy`).", file=sys.stderr)
        return 2
    targets = _select(serves, names)
    if not targets:
        print("no matching serve in the manifest (names: %s)" % ", ".join(names), file=sys.stderr)
        return 1
    if len(targets) > 1:
        print("`logs` needs ONE serve; matched %d: %s -- name just one."
              % (len(targets), ", ".join(s["name"] for s in targets)), file=sys.stderr)
        return 2
    container = targets[0]["container"]
    st = docker_state(container, _run=_run)
    if st == "error":
        print("cannot read logs: docker not available / daemon down / permission?", file=sys.stderr)
        return 1
    if st == "absent":
        print("cannot read logs: container %s does not exist (bring it up first)." % container,
              file=sys.stderr)
        return 1
    argv = ["docker", "logs", "--tail", str(tail)]
    if since:
        argv += ["--since", since]
    if follow:
        argv.append("--follow")
    argv.append(container)
    try:
        if follow:
            return _run(argv).returncode  # stream to the terminal; capturing would block
        r = _run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        print("cannot read logs: docker not available", file=sys.stderr)
        return 1
    _write_console_safe(sys.stdout, r.stdout)
    # Serve startup errors normally arrive on stderr. Preserve them even when
    # the active Windows console cannot represent a progress-bar glyph.
    _write_console_safe(sys.stderr, r.stderr)
    return r.returncode


def deny_ad_hoc_compose_during_exclusive(manifest_path, _run=subprocess.run):
    """Guard the legacy ad-hoc Compose path against exclusive-mode bypass.

    A missing manifest preserves the intentionally independent experiment
    workflow.  Once a manifest declares an exclusive owner, however, active or
    unresolved ownership must block an unreserved GPU experiment before any
    container command runs.
    """
    path = os.path.expanduser(manifest_path)
    if not os.path.isfile(path):
        return None
    try:
        scope = load_manifest_set(path)
    except Exception as exc:
        return [
            "ad-hoc compose denied: cannot resolve operating mode from %s: %s"
            % (path, exc),
            "no container command was run",
        ]
    if not any(reservations.is_exclusive(serve) for serve in scope):
        return None
    synthetic = {
        "name": "ad-hoc-compose",
        "container": "<ad-hoc-compose>",
        "gpu_role": "<unmanaged>",
        "vram_mib": 1,
    }
    return reservations.deny_exclusive_conflict(
        scope,
        [synthetic],
        lambda container: docker_state(container, _run=_run),
    )


def _probe_json(url, payload=None, *, timeout=60, _open=urllib.request.urlopen):
    """Send one bounded JSON request and return its decoded object."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        runtime_url(url),
        data=data,
        headers={
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
        method="POST" if data is not None else "GET",
    )
    with _open(request, timeout=timeout) as response:
        raw = response.read(8 * 1024 * 1024)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("endpoint returned JSON that is not an object")
    return value


def probe_serve(
    serve,
    *,
    text="Anvil Serving release readiness probe.",
    image_path=None,
    timeout=60,
    _open=urllib.request.urlopen,
):
    """Functionally probe one declared serve and return bounded evidence.

    The engine label chooses the protocol. Lifecycle and HTTP health alone do
    not prove that an embedding, reranker, OCR, or ComfyUI workload can process
    its defining request.
    """
    engine = serve.get("engine")
    model = serve.get("served_name") or serve.get("model")
    base = "http://127.0.0.1:%s" % serve["port"]
    if engine == "embedding":
        endpoint = base + "/v1/embeddings"
        payload = {"model": model, "input": [text]}
        response = _probe_json(endpoint, payload, timeout=timeout, _open=_open)
        rows = response.get("data")
        vector = rows[0].get("embedding") if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
        if not isinstance(vector, list) or not vector:
            raise ValueError("embedding response did not contain a non-empty vector")
        return {
            "serve": serve["name"],
            "stack": serve.get("stack", DEFAULT_STACK),
            "engine": engine,
            "model": model,
            "endpoint": endpoint,
            "vectors": len(rows),
            "dimensions": len(vector),
        }
    if engine == "reranker":
        endpoint = base + "/v1/rerank"
        documents = [text, "This document is intentionally unrelated."]
        payload = {"model": model, "query": text, "documents": documents}
        response = _probe_json(endpoint, payload, timeout=timeout, _open=_open)
        rows = response.get("results")
        if not isinstance(rows, list) or len(rows) != len(documents):
            raise ValueError("reranker response did not score every document")
        scores = [
            row.get("relevance_score")
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("relevance_score"), numbers.Real)
        ]
        if len(scores) != len(rows) or any(not math.isfinite(float(score)) for score in scores):
            raise ValueError("reranker response contained a missing or non-finite score")
        return {
            "serve": serve["name"],
            "stack": serve.get("stack", DEFAULT_STACK),
            "engine": engine,
            "model": model,
            "endpoint": endpoint,
            "documents": len(rows),
            "top_index": rows[0].get("index"),
            "top_score": scores[0],
        }
    if engine == "image":
        endpoint = base + serve.get("health", "/system_stats")
        response = _probe_json(endpoint, timeout=timeout, _open=_open)
        if not isinstance(response.get("system"), dict):
            raise ValueError("image service did not return ComfyUI system metadata")
        devices = response.get("devices")
        return {
            "serve": serve["name"],
            "stack": serve.get("stack", DEFAULT_STACK),
            "engine": engine,
            "model": model,
            "endpoint": endpoint,
            "devices": len(devices) if isinstance(devices, list) else 0,
        }
    if image_path and engine in {"vllm", "sglang", "q36"}:
        resolved = os.path.abspath(os.path.expanduser(image_path))
        size = os.path.getsize(resolved)
        if size > 20 * 1024 * 1024:
            raise ValueError("probe image exceeds the 20 MiB safety limit")
        with open(resolved, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
        media_type = mimetypes.guess_type(resolved)[0] or "application/octet-stream"
        endpoint = base + "/v1/chat/completions"
        payload = {
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:%s;base64,%s" % (media_type, encoded)},
                    },
                ],
            }],
            "max_tokens": 256,
            "temperature": 0,
        }
        response = _probe_json(endpoint, payload, timeout=timeout, _open=_open)
        choices = response.get("choices")
        message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ValueError("image-language response did not contain recognized text")
        return {
            "serve": serve["name"],
            "stack": serve.get("stack", DEFAULT_STACK),
            "engine": engine,
            "model": model,
            "endpoint": endpoint,
            "image": resolved,
            "recognized_characters": len(content),
            "recognized_excerpt": content[:200],
        }
    raise ValueError(
        "no functional probe is defined for engine %r; use `eval preflight` "
        "for chat LLMs or `voice benchmark` for audio serves" % engine
    )


def cmd_probe(serves, names, *, text, image_path, timeout, _open=urllib.request.urlopen):
    """CLI wrapper for :func:`probe_serve`; library work returns dictionaries."""
    try:
        targets = _select(serves, names)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if len(targets) != 1:
        print("serves probe requires exactly one serve name", file=sys.stderr)
        return 2
    serve = targets[0]
    state = docker_state(serve["container"])
    if state != "running":
        print(
            "cannot probe %s: container %s is %s (run `serves up %s` first)"
            % (serve["name"], serve["container"], state, serve["name"]),
            file=sys.stderr,
        )
        return 1
    try:
        result = probe_serve(
            serve,
            text=text,
            image_path=image_path,
            timeout=timeout,
            _open=_open,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("serve probe failed: %s" % exc, file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


_ACTIONS = (
    "status", "probe", "up", "down", "rm", "adopt", "logs", "groups", "lint",
    "rollback-check", "up-for", "switch", "promote", "mode", "profile", "render",
)
# Actions that accept `--group NAME` (repeatable) — they act across the whole
# manifest set (serves*.toml in the manifest's dir), not just one file.
_GROUP_ACTIONS = frozenset({"up", "down", "status"})

_ACTION_DESCRIPTIONS = {
    "status": "Show docker and health state for manifest serves.",
    "probe": "Run one engine-aware functional request against a serve.",
    "up": "Start manifest serves or an ad-hoc compose service.",
    "down": "Stop and remove manifest serve containers.",
    "rm": "Remove serve containers after explicit confirmation.",
    "adopt": "Bring externally-started serves under compose management.",
    "logs": "Show bounded or streaming docker logs for one serve.",
    "groups": "List serve groups across the manifest set and their members.",
    "lint": "Report manifest defects that no other surface makes visible.",
    "rollback-check": "Prove every declared rollback is actually usable.",
    "up-for": "Resolve alias -> tier -> serve and start it with --confirm.",
    "switch": "Switch a deployment role to an activation-ready recipe.",
    "promote": "Promote a staged model recipe with preflight and full rollback.",
    "mode": "Preview or transact split and exclusive TP=2 operating modes.",
    "profile": "List, preview, or apply a declared serving topology profile.",
    "render": "Render tuned compose, manifest, and router-tier configuration.",
}


def _build_parser():
    p = argparse.ArgumentParser(
        prog="anvil-serving serves",
        description="Stop/start/inspect the local GPU model serves declared in a serves manifest.")
    sub = p.add_subparsers(dest="action", required=True)
    for action in _ACTIONS:
        sub.add_parser(action, help=_ACTION_DESCRIPTIONS[action], add_help=False)
    return p


def _build_action_parser(action):
    p = argparse.ArgumentParser(
        prog="anvil-serving serves %s" % action,
        description=_ACTION_DESCRIPTIONS[action],
        epilog=(
            "Examples:\n"
            "  anvil-serving serves switch primary\n"
            "  anvil-serving serves switch primary MODEL --dry-run\n"
            "  anvil-serving serves switch primary MODEL --confirm\n\n"
            "Preview resolves the effective Compose service and reports any deferred "
            "live-state refusal. Apply requires exact source router artifacts, takes an "
            "exclusive role lock plus the common promotion lock, journals evidence, "
            "and retains automatic rollback."
            if action == "switch" else None
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    if action == "mode":
        p.add_argument(
            "mode_action",
            choices=("status", "preview", "enter", "leave"),
            help="mode operation to perform",
        )
        p.add_argument(
            "target",
            nargs="?",
            metavar="TARGET",
            help="exclusive TP=2 serve (required except for status)",
        )
        p.set_defaults(names=[])
    elif action == "profile":
        p.add_argument(
            "profile_action",
            choices=("list", "preview", "apply"),
            help="profile operation to perform",
        )
        p.add_argument(
            "profile_id",
            nargs="?",
            metavar="PROFILE",
            help="declared profile id (required except for list)",
        )
        p.set_defaults(names=[])
    elif action == "promote":
        p.add_argument("names", nargs="*", metavar="PLAN_OR_TARGET",
                       help="the [[promotion]] plan name from the manifest; "
                            "or, with --derive, the TARGET and ROLLBACK serve names")
    elif action == "switch":
        p.add_argument("names", nargs=1, metavar="ROLE",
                       help="deployment role to switch (for example: heavy)")
        p.add_argument("recipe_selector", nargs="?", metavar="MODEL",
                       help="recipe model id or unique basename to activate; omit to list choices")
    elif action in {"logs", "probe"}:
        p.add_argument("names", nargs=1, metavar="NAME",
                       help="serve name/container to act on.")
    elif action == "up-for":
        p.add_argument("names", nargs=1, metavar="ALIAS",
                       help="configured chat alias to resolve (for example: llm.primary).")
    elif action in {"groups", "lint", "rollback-check"}:
        p.set_defaults(names=[])
    else:
        p.add_argument("names", nargs="*",
                       help="serve names/containers to act on (default: all in the manifest).")
    p.add_argument("--manifest",
                   help="path to the serves manifest TOML (default: config home, then ./serves.toml).")
    if action in _GROUP_ACTIONS:
        p.add_argument("--group", action="append", metavar="NAME", dest="groups",
                       help="act on every serve tagged NAME across the manifest set "
                            "(serves*.toml in the manifest's dir); repeatable, unions with "
                            "names; the reserved 'all' selects every serve.")
    else:
        p.set_defaults(groups=None)
    if action in {"groups", "lint", "rollback-check", "up-for"}:
        p.add_argument("--json", action="store_true", dest="json_out",
                       help="emit the report as JSON for tooling.")
    else:
        p.set_defaults(json_out=False)
    if action in {"up", "down", "rm", "adopt", "switch", "promote", "mode", "profile", "up-for"}:
        p.add_argument("--dry-run", action="store_true",
                       help="print what would run without touching any container.")
    else:
        p.set_defaults(dry_run=False)
    if action in {"rm", "adopt"}:
        p.add_argument("--yes", action="store_true",
                       help="skip the confirmation prompt (these actions docker rm -f containers).")
    else:
        p.set_defaults(yes=False)
    if action == "down":
        p.add_argument(
            "--keep-container",
            action="store_true",
            help="stop without removing the container, preserving its logs and "
                 "created configuration for inspection or restart.",
        )
    else:
        p.set_defaults(keep_container=False)
    if action == "up":
        p.add_argument("--compose", metavar="FILE",
                       help="bring up an ad-hoc/experiment serve from this compose file; names are compose service names.")
        p.add_argument("--recreate", action="store_true",
                       help="force `docker rm -f` + a fresh `up` for an existing container instead of `docker start`.")
        p.add_argument("--evict", action="store_true",
                       help="let an over-budget `on-demand` acquisition stop `evictable` reservations "
                            "on the same gpu_role via a drained ADR-0018 router transition (quiesce + "
                            "bounded drain before each stop); `resident` serves are never candidates.")
        p.add_argument("--drain-timeout", type=float, default=EVICTION_DRAIN_TIMEOUT,
                       metavar="SECONDS",
                       help="bounded wait for an evicted tier's in-flight requests to finish before "
                            "its container is stopped (default: %(default)s).")
        p.add_argument("--router-url", metavar="URL",
                       help="deployed router base URL for eviction quiesce/drain "
                            "(default: %s)." % DEFAULT_ROUTER_URL)
        p.add_argument("--no-router", action="store_true",
                       help="skip ensuring the deployed router is healthy first "
                            "(offline/serve-only workflows); by default `serves up` "
                            "brings the router up idempotently if it is not healthy.")
    else:
        p.set_defaults(compose=None, recreate=False, evict=False,
                       drain_timeout=EVICTION_DRAIN_TIMEOUT, router_url=None,
                       no_router=False)
    if action == "mode":
        p.add_argument(
            "--restore-group",
            metavar="NAME",
            help="explicit split-mode group restored on leave or entry failure",
        )
        p.add_argument(
            "--confirm",
            action="store_true",
            help="apply enter/leave after reviewing the printed transaction plan",
        )
        p.add_argument(
            "--drain-timeout",
            type=float,
            default=EVICTION_DRAIN_TIMEOUT,
            metavar="SECONDS",
            help="bounded router drain wait before stopping competing serves",
        )
        p.add_argument(
            "--preserve-on-failure",
            action="store_true",
            help="on failed mode entry, stop but retain the failed target "
                 "container and logs before restoring the split stack",
        )
        p.add_argument("--router-url", metavar="URL", help="router transition base URL")
    elif action == "profile":
        p.add_argument(
            "--profiles",
            metavar="PATH",
            help="serving-profile TOML (default: operator config home, then ./serve-profiles.toml)",
        )
        p.add_argument(
            "--confirm",
            action="store_true",
            help="apply the declared topology transaction after reviewing it",
        )
        p.add_argument(
            "--drain-timeout",
            type=float,
            default=EVICTION_DRAIN_TIMEOUT,
            metavar="SECONDS",
            help="bounded router drain wait before a profile transition",
        )
        p.add_argument("--router-url", metavar="URL", help="router transition base URL")
        p.set_defaults(restore_group=None, preserve_on_failure=False)
    elif action == "rollback-check":
        p.add_argument(
            "--restore-group",
            metavar="NAME",
            help="also verify the compose image of every serve in this split "
                 "restore group is present locally",
        )
        p.set_defaults(confirm=False, preserve_on_failure=False)
    elif action == "up-for":
        p.add_argument(
            "--config",
            metavar="PATH",
            help="router config TOML (default: operator config home, "
                 "same resolution as `router fleet-status`).",
        )
        p.add_argument(
            "--confirm",
            action="store_true",
            help="start the resolved serve (delegates to `serves up`); "
                 "without it, only the resolution is printed.",
        )
        p.set_defaults(restore_group=None, preserve_on_failure=False)
    else:
        p.set_defaults(
            confirm=False,
            restore_group=None,
            preserve_on_failure=False,
        )
    if action == "logs":
        p.add_argument("--tail", default="200",
                       help="trailing lines to show (default: %(default)s; 'all').")
        p.add_argument("--since",
                       help="only logs since a timestamp or relative time (e.g. 10m, 1h).")
        p.add_argument("--follow", action="store_true",
                       help="stream new output (Ctrl-C to stop).")
    else:
        p.set_defaults(tail="200", since=None, follow=False)
    if action == "probe":
        p.add_argument(
            "--text",
            default="Anvil Serving release readiness probe.",
            help="probe text or OCR instruction (default: %(default)s)",
        )
        p.add_argument("--image", help="image path for an OCR/vision probe")
        p.add_argument(
            "--timeout",
            type=float,
            default=60,
            metavar="SECONDS",
            help="HTTP deadline from 0.1 through 600 seconds (default: %(default)s)",
        )
    else:
        p.set_defaults(
            text="Anvil Serving release readiness probe.",
            image=None,
            timeout=60,
        )
    if action == "switch":
        p.add_argument("--recipe", metavar="MODEL",
                       help="recipe model id or unique basename to activate (compatibility form)")
        p.add_argument("--registry", metavar="PATH",
                       help="recipe registry TOML (default: configs/serve-recipes.toml, then operator config)")
        p.set_defaults(resume=False)
    elif action == "promote":
        p.add_argument("--rollback", action="store_true",
                       help="restore the plan's rollback serve and router state")
        p.add_argument("--resume", action="store_true",
                       help="resume an interrupted promotion from an already-running target")
        p.add_argument(
            "--derive", action="store_true",
            help="derive and print a [[promotion]] block from TARGET and "
                 "ROLLBACK instead of executing a plan",
        )
        p.add_argument(
            "--router-config", metavar="PATH",
            help="promoted-state router config TOML for --derive",
        )
        p.add_argument(
            "--rollback-router-config", metavar="PATH",
            help="rollback-state router config TOML for --derive",
        )
        p.add_argument(
            "--out", metavar="PATH",
            help="write the derived [[promotion]] block here; refuses to overwrite an existing file",
        )
    else:
        p.set_defaults(recipe=None, registry=None, rollback=False, resume=False,
                       derive=False, router_config=None, rollback_router_config=None,
                       out=None)
    if action in {"promote", "mode"}:
        p.add_argument(
            "--skip-preflight-checks",
            action="store_true",
            help="skip the implicit lint + rollback-check gate this transaction "
                 "runs before its first mutation (loudly logged to stderr; "
                 "`mode` only accepts this with `enter`).",
        )
    else:
        p.set_defaults(skip_preflight_checks=False)
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        _build_parser().parse_args(argv)
        return 0
    action = argv[0]
    if action not in _ACTIONS:
        _build_parser().parse_args([action])
        return 2
    if action == "render":
        from . import deploy
        return deploy.main(argv[1:], prog="anvil-serving serves render")
    # parse_intermixed_args (not parse_args): on py3.11 a `nargs="*"` positional that
    # follows an option-with-value (e.g. `up --compose FILE svc-a svc-b`) is dropped as
    # "unrecognized arguments" — py3.12 fixed plain parse_args, but intermixed is the
    # documented cross-version fix. No REMAINDER/subparsers here, so it's safe.
    p = _build_action_parser(action)
    if action != "up" and any(arg == "--compose" or arg.startswith("--compose=") for arg in argv[1:]):
        print("serves --compose is only valid with `up`.", file=sys.stderr)
        return 2
    try:
        a = p.parse_intermixed_args(argv[1:])
    except SystemExit as exc:
        if exc.code == 0:
            raise
        return int(exc.code or 2)
    a.action = action
    if not 0.1 <= a.timeout <= 600:
        print("--timeout must be between 0.1 and 600 seconds", file=sys.stderr)
        return 2

    # Reject conflicting selectors before resolving manifests or registries. This
    # is an argument error, so its result must not depend on which config files
    # happen to exist on the current host.
    if a.action == "switch" and a.recipe_selector and a.recipe:
        print(
            "choose either positional MODEL or --recipe MODEL, not both",
            file=sys.stderr,
        )
        return 2

    # `serves promote --derive` is a read-only derivation of a [[promotion]]
    # block from TARGET/ROLLBACK, not an execution of an existing plan --
    # keep its argument shape and the ordinary PLAN-name form from silently
    # accepting each other's options (issue #381, feature 16).
    if a.action == "promote":
        if a.derive:
            if len(a.names) != 2:
                print(
                    "serves promote --derive requires exactly two positionals: "
                    "TARGET ROLLBACK",
                    file=sys.stderr,
                )
                return 2
            rejected = [
                flag for flag, present in (
                    ("--rollback", a.rollback),
                    ("--resume", a.resume),
                    ("--dry-run", a.dry_run),
                    ("--skip-preflight-checks", a.skip_preflight_checks),
                )
                if present
            ]
            if rejected:
                print(
                    "serves promote --derive does not accept %s"
                    % ", ".join(rejected),
                    file=sys.stderr,
                )
                return 2
            if not a.router_config or not a.rollback_router_config:
                print(
                    "serves promote --derive requires --router-config and "
                    "--rollback-router-config",
                    file=sys.stderr,
                )
                return 2
        else:
            if len(a.names) != 1:
                print(
                    "serves promote requires exactly one positional: PLAN "
                    "(use --derive for TARGET ROLLBACK)",
                    file=sys.stderr,
                )
                return 2
            extra = [
                flag for flag, present in (
                    ("--router-config", a.router_config),
                    ("--rollback-router-config", a.rollback_router_config),
                    ("--out", a.out),
                )
                if present
            ]
            if extra:
                print(
                    "serves promote %s requires --derive" % ", ".join(extra),
                    file=sys.stderr,
                )
                return 2

    # Persistent host policy is validated before any covered operation can
    # start the router or touch a container. Ad-hoc Compose and switch-choice
    # listing are intentionally outside the v1 lifecycle boundary.
    cache_operation = None
    if a.action == "up" and not a.compose:
        cache_operation = "serves up"
    elif a.action == "adopt":
        cache_operation = "serves adopt"
    elif a.action == "promote" and not a.derive:
        cache_operation = "serves promote --rollback" if a.rollback else "serves promote"
    elif a.action == "switch" and (a.recipe_selector or a.recipe):
        cache_operation = "serves switch"
    elif a.action == "mode" and a.mode_action in {"enter", "leave"}:
        cache_operation = "serves mode %s" % a.mode_action
    elif a.action == "profile" and a.profile_action == "apply":
        cache_operation = "serves profile apply"
    elif a.action == "up-for" and a.confirm:
        cache_operation = "serves up-for"
    cache_policy = None
    cache_before = None
    if cache_operation is not None:
        try:
            cache_policy = host_ops.load_cache_reclaim_policy()
        except host_ops.HostConfigError as exc:
            print("[anvil-serving] %s" % exc, file=sys.stderr)
            return 2
        if a.dry_run:
            host_ops.render_cache_reclaim_plan(cache_policy, cache_operation)
        else:
            cache_before = host_ops.capture_cache_before(cache_policy)

    # The legacy ad-hoc Compose path has no reservation row of its own.  It must
    # still consult an operator manifest that declares exclusive mode, before
    # even the router ensure can issue a container command.
    if a.action == "up" and a.compose:
        mode_denial = deny_ad_hoc_compose_during_exclusive(
            resolve_manifest_path(a.manifest)
        )
        if mode_denial:
            for line in mode_denial:
                print("  " + line)
            return 1

    # `serves up` ensures the DEPLOYED router is healthy FIRST — serves are only
    # reachable behind it. Reuses the `router` verb's own status/up code paths;
    # idempotent (a healthy router is not restarted), honors --dry-run, and
    # --no-router skips it. Placed before BOTH up paths (ad-hoc --compose and
    # manifest) so either form gets the ensure. Non-gating: a failed router
    # bring-up is reported but still proceeds to the serves.
    if a.action == "up":
        ensure_router_healthy(no_router=a.no_router, dry_run=a.dry_run)

    # `up --compose <file>`: ad-hoc/experiment serve from a compose file that is NOT in the
    # manifest — independent of serves.toml, so we neither require nor load a manifest here.
    if a.action == "up" and a.compose:
        if a.groups:
            print("--group has no meaning with --compose (an ad-hoc compose serve is not "
                  "in the manifest set, so it carries no group tags)", file=sys.stderr)
            return 2
        if a.recreate:
            print("--recreate has no meaning with --compose (`docker compose up -d` already "
                  "recreates a service when its config changed)", file=sys.stderr)
            return 2
        if a.evict:
            print("--evict has no meaning with --compose (an ad-hoc compose serve declares "
                  "no reservation; the ledger only admits manifest serves)", file=sys.stderr)
            return 2
        return cmd_up_compose(a.compose, a.names, dry_run=a.dry_run)
    if a.compose:
        print("--compose is only valid with `up`", file=sys.stderr)
        return 2

    manifest_path = resolve_manifest_path(a.manifest)
    # A `--group` action (and `serves groups`) resolves targets across the whole
    # manifest SET. Plain positional-name operations keep selection scoped to
    # the named manifest, but admission/status still use the complete set so a
    # separate voice or ComfyUI manifest cannot become invisible GPU occupancy.
    use_set = bool(a.groups) or a.action in {
        "groups", "lint", "rollback-check", "mode", "profile", "up-for",
    }
    # `lint` reports defects that the strict loader refuses, so it must load
    # leniently -- otherwise the command an operator reaches for when blocked
    # is the one that cannot run.
    lenient = a.action == "lint"
    try:
        if a.action == "mode" and not os.path.isfile(os.path.expanduser(manifest_path)):
            raise FileNotFoundError(manifest_path)
        serves = (
            load_manifest_set(manifest_path, reject_duplicates=not lenient)
            if use_set else load_manifest(manifest_path)
        )
    except FileNotFoundError:
        search_hint = (
            a.manifest
            if a.manifest
            else ", ".join(default_manifest_candidates())
        )
        print(
            "manifest not found: %s (run `anvil-serving init` to generate one, "
            "place one in $ANVIL_SERVING_HOME, or pass --manifest to "
            "point at an existing serves.toml)" % search_hint,
            file=sys.stderr,
        )
        return 2
    except Exception as e:  # malformed manifest
        print("bad manifest %s: %s" % (manifest_path, e), file=sys.stderr)
        return 2
    try:
        ledger_serves = serves if use_set else load_manifest_set(manifest_path)
    except Exception as e:
        print("bad manifest set for %s: %s" % (manifest_path, e), file=sys.stderr)
        return 2

    if a.action == "groups":
        return cmd_groups(serves, as_json=a.json_out)
    if a.action == "lint":
        return cmd_lint(serves, as_json=a.json_out)
    if a.action == "rollback-check":
        # "Every declared rollback" means the whole manifest SET — a
        # [[promotion]] in serves.voice.toml is as much a declared rollback as
        # one in serves.toml. Mirrors load_manifest_set's per-file tolerance
        # of absent candidates.
        promotions = []
        for path in manifest_set_paths(manifest_path):
            try:
                promotions.extend(load_promotions(path))
            except FileNotFoundError:
                continue
            except Exception as exc:
                print("bad promotion plan in %s: %s" % (path, exc), file=sys.stderr)
                return 2
        return cmd_rollback_check(
            serves, promotions, restore_group=a.restore_group, as_json=a.json_out)
    if a.action == "profile":
        if a.profile_action == "list" and a.profile_id:
            print("serves profile list does not accept PROFILE", file=sys.stderr)
            return 2
        if a.profile_action != "list" and not a.profile_id:
            print(
                "serves profile %s requires PROFILE" % a.profile_action,
                file=sys.stderr,
            )
            return 2
        if a.profile_action != "apply" and a.confirm:
            print("--confirm is only valid with serves profile apply", file=sys.stderr)
            return 2
        profiles_path = resolve_serve_profiles_path(a.profiles)
        try:
            profiles = load_serve_profiles(profiles_path)
        except FileNotFoundError:
            print(
                "serve profiles not found: %s (pass --profiles PATH or add "
                "serve-profiles.toml to the operator config home)" % profiles_path,
                file=sys.stderr,
            )
            return 2
        except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
            print("bad serve profiles %s: %s" % (profiles_path, exc), file=sys.stderr)
            return 2
        if a.profile_action == "apply":
            try:
                profile = select_serve_profile(profiles, a.profile_id)
                states = docker_states(
                    [
                        serve["container"] for serve in serves
                        if reservations.is_gpu_inference(serve)
                    ]
                )
                summary = operating_mode_summary(
                    serves, lambda container: states.get(container, "absent")
                )
                transition = profile_transition_action(
                    profile, summary, apply=not a.dry_run
                )
            except ServeProfileError as exc:
                print("serving profile refused: %s" % exc, file=sys.stderr)
                return 2
            if transition == "enter":
                mode_promotions = []
                for path in manifest_set_paths(manifest_path):
                    try:
                        mode_promotions.extend(load_promotions(path))
                    except FileNotFoundError:
                        continue
                    except Exception as exc:
                        print("bad promotion plan in %s: %s" % (path, exc), file=sys.stderr)
                        return 2
                profile_involved = {profile["exclusive_target"]} | {
                    serve["name"] for serve in resolve_group(serves, profile["restore_group"])
                }
                gate_rc = _preflight_gate(
                    serves,
                    mode_promotions,
                    restore_group=profile["restore_group"],
                    involved=profile_involved,
                    label="profile apply",
                )
                if gate_rc is not None:
                    return gate_rc
        try:
            return cmd_profile(
                serves,
                profiles,
                a.profile_action,
                a.profile_id,
                confirm=a.confirm,
                dry_run=a.dry_run,
                drain_timeout=a.drain_timeout,
                router_url=a.router_url,
            )
        except ServeProfileError as exc:
            print("serving profile refused: %s" % exc, file=sys.stderr)
            return 2
    if a.action == "up-for":
        from .doctor import resolve_default_config_path
        from .router import config as router_config

        config_path = a.config or resolve_default_config_path()
        if not config_path:
            print("no router config found; pass --config PATH", file=sys.stderr)
            return 2
        try:
            router_cfg = router_config.load(config_path)
        except Exception as exc:  # noqa: BLE001 - surface the load failure verbatim
            print("could not load router config %s: %s" % (config_path, exc), file=sys.stderr)
            return 2
        rc = cmd_up_for(
            router_cfg, serves, a.names[0], config_path,
            as_json=a.json_out, confirm=a.confirm, dry_run=a.dry_run,
            ledger_serves=ledger_serves,
        )
        return _finish_cache_reclaim(
            rc, cache_policy, cache_before, cache_operation, dry_run=a.dry_run,
        )
    if a.action == "mode":
        if a.mode_action == "status":
            if a.target or a.restore_group:
                print("mode status does not accept TARGET or --restore-group", file=sys.stderr)
                return 2
        elif not a.target or not a.restore_group:
            print(
                "mode %s requires TARGET and --restore-group" % a.mode_action,
                file=sys.stderr,
            )
            return 2
        if a.mode_action in {"preview", "status"} and a.confirm:
            print("--confirm is only valid with mode enter/leave", file=sys.stderr)
            return 2
        if a.preserve_on_failure and a.mode_action != "enter":
            print(
                "--preserve-on-failure is only valid with mode enter",
                file=sys.stderr,
            )
            return 2
        if a.skip_preflight_checks and a.mode_action != "enter":
            print(
                "--skip-preflight-checks is only valid with mode enter",
                file=sys.stderr,
            )
            return 2
        if a.mode_action == "enter":
            # A first load: standalone `rollback-check` loads promotions itself
            # (mode's own dispatch never has), mirroring its per-file tolerance
            # of an absent candidate across the manifest SET.
            mode_promotions = []
            for path in manifest_set_paths(manifest_path):
                try:
                    mode_promotions.extend(load_promotions(path))
                except FileNotFoundError:
                    continue
                except Exception as exc:
                    print("bad promotion plan in %s: %s" % (path, exc), file=sys.stderr)
                    return 2
            # This transaction's blast radius: the exclusive target plus
            # every serve tagged with the restore group it will fail back to.
            mode_involved = {a.target} | {
                s["name"] for s in resolve_group(serves, a.restore_group)
            }
            gate_rc = _preflight_gate(
                serves, mode_promotions, restore_group=a.restore_group,
                skip=a.skip_preflight_checks, involved=mode_involved,
                label="mode enter",
            )
            if gate_rc is not None:
                return gate_rc
        rc = cmd_mode(
            serves,
            a.mode_action,
            a.target,
            a.restore_group,
            confirm=a.confirm,
            dry_run=a.dry_run,
            drain_timeout=a.drain_timeout,
            preserve_on_failure=a.preserve_on_failure,
            router_url=a.router_url,
        )
        return _finish_cache_reclaim(
            rc,
            cache_policy,
            cache_before,
            cache_operation,
            dry_run=a.dry_run,
        )

    # Resolve --group to concrete serves across the set; the union with positional
    # names becomes the target list. Print what each group resolved to before
    # acting (honoring --dry-run), so an operator sees the blast radius first.
    group_names = None
    if a.groups:
        group_names, unknown = resolve_group_targets(serves, a.groups, a.names)
        if unknown:
            print("unknown group(s): %s (no serve is tagged with them; see "
                  "`anvil-serving serves groups`)" % ", ".join(unknown), file=sys.stderr)
            return 2
        for group in a.groups:
            members = resolve_group(serves, group)
            print("group %r -> %s" % (
                group, ", ".join(m["name"] for m in members) or "(none)"))
        if not group_names:
            # Guard: an empty target list must never fall through to _select's
            # "empty means all" and silently act on every serve.
            print("no serves matched the requested group(s)/name(s)", file=sys.stderr)
            return 1

    if a.action == "status":
        status_names = (
            group_names
            if group_names is not None
            else (a.names or [s["name"] for s in _serving_path_scope(serves)])
        )
        selected = _select(serves, status_names) if status_names else []
        unknown_names = [
            name
            for name in a.names
            if not any(
                serve["name"] == name or serve["container"] == name
                for serve in selected
            )
        ]
        if unknown_names:
            print(
                "unknown serve(s): %s" % ", ".join(unknown_names),
                file=sys.stderr,
            )
            return 2
        return cmd_status(
            serves,
            names=status_names,
            ledger_serves=_serving_path_scope(ledger_serves, selected),
        )
    if a.action == "logs":
        return cmd_logs(serves, a.names, tail=a.tail, since=a.since, follow=a.follow)
    if a.action == "probe":
        return cmd_probe(
            serves,
            a.names,
            text=a.text,
            image_path=a.image,
            timeout=a.timeout,
        )
    if a.action == "down":
        return cmd_down(serves, group_names if group_names is not None else a.names,
                        dry_run=a.dry_run, keep_container=a.keep_container)
    if a.action == "up":
        target_names = group_names if group_names is not None else a.names
        rc = cmd_up(serves, target_names, dry_run=a.dry_run, recreate=a.recreate,
                    evict=a.evict, drain_timeout=a.drain_timeout,
                    router_url=a.router_url, wait_for_readiness=not a.dry_run,
                    ledger_serves=ledger_serves)
        return _finish_cache_reclaim(
            rc, cache_policy, cache_before, cache_operation, dry_run=a.dry_run,
            readiness_targets=_select(serves, target_names),
        )
    if a.action == "rm":
        return cmd_rm(serves, a.names, dry_run=a.dry_run, assume_yes=a.yes)
    if a.action == "adopt":
        rc = cmd_adopt(serves, a.names, dry_run=a.dry_run, assume_yes=a.yes)
        return _finish_cache_reclaim(
            rc, cache_policy, cache_before, cache_operation, dry_run=a.dry_run,
            readiness_targets=_select(serves, a.names),
        )
    if a.action == "promote":
        if a.derive:
            # Read-only: derives a fresh [[promotion]] block instead of
            # executing one, so it runs before `load_promotions` (a malformed
            # EXISTING plan elsewhere in the manifest must not block deriving
            # a new one) and before the preflight gate (nothing is mutated).
            return cmd_promote_derive(
                serves, a.names[0], a.names[1], a.router_config,
                a.rollback_router_config, out=a.out,
            )
        try:
            promotions = load_promotions(manifest_path)
        except Exception as exc:
            print("bad promotion plan in %s: %s" % (manifest_path, exc), file=sys.stderr)
            return 2
        # Preflight checks are read-only and cheap, so the gate covers every
        # promote invocation -- rollback and resume mutate too, and both
        # benefit from the same pre-mutation evidence a fresh promotion gets.
        # `ledger_serves` is already the manifest SET the same command loaded
        # above; `promotions` above is the same load `rollback-check` would
        # otherwise repeat, so neither is re-loaded for the gate.
        #
        # Resolve the plan the same way cmd_promote itself will (must match
        # exactly one [[promotion]] entry). An unknown/ambiguous plan name
        # skips the gate entirely and falls through to cmd_promote's own
        # "must match exactly one" refusal -- the gate has no plan to scope
        # to, and re-reporting that as a lint/rollback-check abort would be
        # misleading (issue #377 finding 8b).
        plan_matches = [p for p in promotions if p["name"] == a.names[0]]
        if len(plan_matches) == 1:
            plan = plan_matches[0]
            promote_involved = {
                name for name in (plan.get("target"), plan.get("rollback"))
                if name
            }
            # promotion-topology findings carry the PLAN name in their
            # `serve` field; including the resolved plan's name makes a
            # topology error on the plan being promoted block (exit 3)
            # instead of printing as advisory -- which for this plan it is
            # not.
            promote_involved.add(plan["name"])
            gate_rc = _preflight_gate(
                ledger_serves, promotions, restore_group=None,
                skip=a.skip_preflight_checks, involved=promote_involved,
                label="promote",
            )
            if gate_rc is not None:
                return gate_rc
        rc = cmd_promote(
            serves, promotions, a.names[0], os.path.abspath(manifest_path),
            rollback=a.rollback, resume=a.resume, dry_run=a.dry_run,
        )
        return _finish_cache_reclaim(
            rc, cache_policy, cache_before, cache_operation, dry_run=a.dry_run,
        )
    if a.action == "switch":
        selector = a.recipe_selector or a.recipe
        registry_path = resolve_recipe_registry_path(a.registry)
        try:
            promotions = load_promotions(manifest_path)
            registry = serve_recipes.load_registry(registry_path)
        except FileNotFoundError as exc:
            print(
                "switch input not found: %s (run `anvil-serving init`, or pass "
                "--manifest and --registry explicitly)" % exc.filename,
                file=sys.stderr,
            )
            return 2
        except Exception as exc:
            print("bad switch configuration: %s" % exc, file=sys.stderr)
            return 2
        if not selector:
            return cmd_switch_choices(
                serves, promotions, registry, a.names[0], registry_path,
            )
        rc = cmd_switch(
            serves, promotions, registry, a.names[0], selector,
            os.path.abspath(manifest_path), dry_run=a.dry_run,
        )
        return _finish_cache_reclaim(
            rc, cache_policy, cache_before, cache_operation, dry_run=a.dry_run,
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

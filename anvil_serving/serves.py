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

It reads a manifest (default search: `./serves.toml`, then
`~/.anvil-serving/serves.toml`; `deploy`/`init` write the current-directory file
and the shipped reference is `examples/fakoli-dark/serves.toml`) that declares
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
from contextlib import contextmanager, nullcontext
import copy
import hashlib
import json
import math
import numbers
import os
import re
import shlex
import subprocess
import tempfile
import time
from . import guard
from . import reservations
from . import serve_recipes
import sys
import urllib.request
import urllib.error

from .paths import config_path

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
CONFIG_HOME_MANIFEST = "~/.anvil-serving/serves.toml"
EXAMPLE_MANIFEST = os.path.join(REPO, "examples", "fakoli-dark", "serves.toml")
DEFAULT_RECIPE_REGISTRY = os.path.join("configs", "serve-recipes.toml")

# States meaning the container exists but is already stopped (nothing to free).
_STOPPED = ("exited", "created", "dead")
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
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# ADR-0017 GPU residency reservations: the residency vocabulary for a serve's
# declared VRAM reservation. "resident" is never evicted, "evictable" may be
# stopped to make room, "on-demand" is started per task and may evict
# "evictable" serves. (The VRAM types are reservations, never *Lease —
# AdmissionLease in router/admission.py is the request-admission layer.)
_RESIDENCIES = ("resident", "evictable", "on-demand")
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
_ENGINE_MARKERS = {
    "vllm": re.compile(r"(^|[^a-z0-9])vllm([^a-z0-9]|$)"),
    "sglang": re.compile(r"(^|[^a-z0-9])sglang([^a-z0-9]|$)"),
    "llamacpp": re.compile(r"(^|[^a-z0-9])llama(?:[._-]?cpp|[._-]server)([^a-z0-9]|$)"),
}


def default_manifest_candidates():
    """Manifest search path for operator commands when --manifest is omitted."""
    return [DEFAULT_MANIFEST, config_path("serves.toml")]


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
        "./serve-recipes.toml",
        DEFAULT_RECIPE_REGISTRY,
        config_path("serve-recipes.toml"),
        os.path.join(REPO, "configs", "serve-recipes.toml"),
        os.path.join(HERE, "_scaffold_templates", "serve-recipes.toml"),
    )
    for candidate in candidates:
        if os.path.isfile(os.path.expanduser(candidate)):
            return candidate
    return config_path("serve-recipes.toml")


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


def load_manifest_set(manifest_path):
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
    return list(by_container.values())


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


def _read_dotenv(path):
    """Read a simple KEY=VALUE .env file without logging values.

    Shell environment wins later; this only fills missing vars for lifecycle
    commands launched from a manifest directory.
    """
    values = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not _ENV_NAME_RE.match(name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        values[name] = value
    return values


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
    if "gpu_role" in s:
        gpu_role = s.get("gpu_role")
        if not isinstance(gpu_role, str) or not gpu_role.strip():
            raise ValueError(f"serve entry gpu_role must be a non-empty string: {raw!r}")
        s["gpu_role"] = gpu_role.strip()
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
            field for field in ("name", "container", "port")
            if field not in s or s.get(field) in ("", None)
        ]
        if not s.get("model") and not s.get("served_name"):
            missing.append("model/served_name")
        if missing:
            raise ValueError(
                "serve entry missing required field(s) "
                f"{', '.join(missing)}: {raw!r}"
            )
        if not isinstance(s.get("port"), int):
            raise ValueError(f"serve entry port must be an integer: {raw!r}")
        s["model"] = s.get("model") or s.get("served_name")
        s["served_name"] = s.get("served_name") or s["model"]
        up = shlex.split(s["up"]) if s.get("up") else None
        s["engine"] = _normalize_engine(s, up)
        _normalize_reservation(s, raw)
        _normalize_groups(s, raw)
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
    names both the promoted and rollback serve plus both router states, so a
    failed health/preflight/router gate can restore the complete deployment.
    """
    with open(path, "rb") as f:
        data = tomllib.load(f)
    mdir = os.path.dirname(os.path.abspath(path))
    required = (
        "name", "target", "rollback", "router_config", "router_profile",
        "rollback_router_config", "rollback_router_profile",
    )
    plans = []
    for raw in data.get("promotion", []):
        plan = dict(raw)
        missing = [field for field in required if not plan.get(field)]
        if missing:
            raise ValueError("promotion entry missing required field(s) %s: %r" % (
                ", ".join(missing), raw))
        for field in (
            "router_config", "router_profile", "rollback_router_config",
            "rollback_router_profile",
        ):
            value = str(plan[field]).replace("{dir}", mdir)
            if not os.path.isabs(value):
                value = os.path.join(mdir, value)
            plan[field] = os.path.abspath(value)
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
    """Prove affected tiers map only to the managed serve pair."""
    from urllib.parse import urlsplit
    from .router.config import load as load_router

    forward = load_router(plan["router_config"])
    rollback = load_router(plan["rollback_router_config"])
    target = _exact_serve(serves, plan["target"])
    old = _exact_serve(serves, plan["rollback"])
    affected = set(plan["affected_tiers"])
    forward_ids = {tier.id for tier in forward.tiers}
    rollback_ids = {tier.id for tier in rollback.tiers}
    if not affected <= forward_ids or not affected <= rollback_ids:
        raise ValueError("affected_tiers contains an unknown router tier")

    def _endpoint_hosts(serve):
        hosts = {
            "127.0.0.1",
            "host.docker.internal",
            str(serve["name"]).casefold(),
            str(serve["container"]).casefold(),
        }
        up = serve.get("up") or []
        try:
            up_index = up.index("up")
        except ValueError:
            pass
        else:
            hosts.update(
                token.casefold()
                for token in up[up_index + 1:]
                if token and not token.startswith("-")
            )
        return hosts

    def _owns_endpoint(tier, serve):
        parsed = urlsplit(tier.base_url)
        return (
            parsed.scheme == "http"
            and parsed.hostname is not None
            and parsed.hostname.casefold() in _endpoint_hosts(serve)
            and parsed.port == serve["port"]
        )

    def _matches(tier, serve):
        return (
            _owns_endpoint(tier, serve)
            and tier.model == serve["served_name"]
            and tier.model_identity
        )

    forward_owned = {
        tier.id for tier in forward.tiers if _owns_endpoint(tier, target)
    }
    rollback_owned = {
        tier.id for tier in rollback.tiers if _owns_endpoint(tier, old)
    }
    if affected != forward_owned or affected != rollback_owned:
        raise ValueError(
            "affected_tiers must exactly cover every tier owned by the managed endpoint"
        )

    for tier_id in affected:
        if not _matches(forward.tier(tier_id), target):
            raise ValueError(
                "affected tier does not map to the target serve with identity readiness"
            )
        if not _matches(rollback.tier(tier_id), old):
            raise ValueError(
                "affected tier does not map to the rollback serve with identity readiness"
            )
    for tier_id in forward_ids | rollback_ids:
        if tier_id in affected:
            continue
        if tier_id not in forward_ids or tier_id not in rollback_ids:
            raise ValueError("unaffected router tiers differ between promotion states")
        if forward.tier(tier_id) != rollback.tier(tier_id):
            raise ValueError("unaffected router tier configuration changed")
    return True


def _router_base_url(plan):
    from urllib.parse import urlsplit, urlunsplit

    health_url = str(plan.get("router_health_url", "http://127.0.0.1:8000/healthz"))
    parsed = urlsplit(health_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def _transition_cli(router_url, action, tier_id, *, timeout=None, _run=subprocess.run):
    """One ADR-0018 router transition step (quiesce/drain/readmit/
    transition-status) through the deployed router's authenticated CLI
    boundary. Shared by promotion plans and reservation eviction — both
    compose the SAME transition; neither grows a second state authority.
    """
    argv = [
        sys.executable, "-m", "anvil_serving.cli", "router", action,
        "--tier", tier_id, "--router-url", router_url,
    ]
    if timeout is not None:
        argv += ["--timeout", str(timeout)]
    if action in ("quiesce", "readmit"):
        argv.append("--confirm")
    print("  gate: %s" % " ".join(argv))
    return _run(argv, text=True).returncode


def _promotion_transition_cli(plan, action, tier_id, *, timeout=None, _run=subprocess.run):
    return _transition_cli(
        _router_base_url(plan), action, tier_id, timeout=timeout, _run=_run
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
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
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


def _promotion_cli(argv, *, _run=subprocess.run):
    command = [sys.executable, "-m", "anvil_serving.cli", *argv, "--confirm"]
    print("  gate: %s" % " ".join(command))
    result = _run(command, text=True)
    return result.returncode


def _gateway_status(url, *, _open=urllib.request.urlopen):
    try:
        with _open(url, timeout=5) as response:
            return getattr(response, "status", None) or response.getcode()
    except urllib.error.HTTPError as exc:
        return exc.code  # auth failures still prove the router is reachable
    except Exception:
        return None


def _promotion_transition(serves, plan, manifest_path, *, rollback=False,
                          dry_run=False, require_candidate=True, resume=False,
                          _run=subprocess.run,
                          _open=urllib.request.urlopen, _sleep=time.sleep):
    target = _exact_serve(serves, plan["rollback"] if rollback else plan["target"])
    displaced = _exact_serve(serves, plan["target"] if rollback else plan["rollback"])
    candidate = _exact_serve(serves, plan["candidate"]) if plan.get("candidate") else None
    config = plan["rollback_router_config"] if rollback else plan["router_config"]
    profile = plan["rollback_router_profile"] if rollback else plan["router_profile"]
    label = "rollback" if rollback else "promotion"

    for path in (config, profile):
        if not os.path.isfile(path):
            print("  %s refused: required router artifact is missing: %s" % (label, path))
            return 2
    # Validate BOTH deployable states with the deployed image before the first
    # container is stopped. A forward failure is only safely reversible when
    # its rollback config/profile are already known-loadable (and vice versa).
    if not dry_run:
        pairs = (
            (plan["router_config"], plan["router_profile"]),
            (plan["rollback_router_config"], plan["rollback_router_profile"]),
        )
        for pair_config, pair_profile in pairs:
            validate = [
                "router", "promote", "--config", pair_config, "--profile", pair_profile,
                "--validate-only",
            ]
            if _promotion_cli(validate, _run=_run) != 0:
                print("  %s refused: router artifacts failed deployed-loader validation" % label)
                return 2
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
    print("  %s plan: stop %s; start %s; %d preflight gate(s); promote router" % (
        label, ", ".join(stop_names), target["name"], len(gates)))
    if dry_run:
        for tier_id in plan["affected_tiers"]:
            print("  gate: quiesce router tier %s" % tier_id)
            print("  gate: drain router tier %s (timeout %ss)" % (
                tier_id, plan["drain_timeout"]))
        cmd_down(serves, stop_names, dry_run=True, _run=_run)
        cmd_up(serves, [target["name"]], dry_run=True, recreate=True, _run=_run)
        print("  gate: exact served-model identity for %s" % target["served_name"])
        for gate in gates:
            print("  gate %s: eval preflight --tier %s --checks %s --thinking-mode %s "
                  "--visible-answer-tokens %s --reasoning-headroom-tokens %s" % (
                      gate["name"], target["name"], gate["checks"], gate["thinking_mode"],
                      gate["visible_answer_tokens"], gate["reasoning_headroom_tokens"]))
        print("  gate: router promote --config %s --profile %s" % (config, profile))
        print("  verify: router gateway is reachable after reload")
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

    if cmd_down(serves, stop_names, _run=_run) != 0:
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
    promote = [
        "router", "promote", "--config", config, "--profile", profile,
    ]
    expected_artifacts = plan.get("_expected_artifact_digests")
    if expected_artifacts is not None:
        config_key = "rollback_router_config" if rollback else "router_config"
        profile_key = "rollback_router_profile" if rollback else "router_profile"
        desired_router = {
            "config": expected_artifacts[config_key],
            "profile": expected_artifacts[profile_key],
        }
        try:
            current_artifacts = _promotion_artifact_digests(plan)
        except (OSError, TypeError, ValueError) as exc:
            print("  %s refused: cannot recheck router artifacts: %s" % (label, exc))
            return 1
        if current_artifacts != expected_artifacts:
            print(
                "  %s refused: router config/profile files changed during the transaction"
                % label
            )
            return 1
    else:
        desired_router = {
            "config": _artifact_digest(config, "config"),
            "profile": _artifact_digest(profile, "profile"),
        }
    expected_router = plan.get("_expected_router_digests")
    if expected_router is not None:
        try:
            current_router = _deployed_router_digests(plan, _run=_run)
        except RuntimeError as exc:
            print("  %s refused: final router compare-and-swap check failed: %s" % (
                label, exc,
            ))
            return 4
        if current_router != expected_router:
            print(
                "  %s refused: router config/profile changed during the transaction" % label
            )
            return 4
    promote_rc = _promotion_cli(promote, _run=_run)
    if expected_router is not None:
        try:
            current_router = _deployed_router_digests(plan, _run=_run)
        except RuntimeError as exc:
            print("  %s failed: cannot resolve router state after promote: %s" % (
                label, exc,
            ))
            return 4
        if current_router == desired_router:
            # Advance the CAS token so any later automatic recovery expects
            # the state that was actually installed, not the stale source.
            plan["_expected_router_digests"] = dict(desired_router)
        elif current_router != expected_router:
            print(
                "  %s failed: router entered an unknown config/profile state; "
                "automatic recovery is blocked" % label
            )
            return 4
        if promote_rc != 0:
            return 1
        if current_router != desired_router:
            print("  %s failed: router promote returned success without installing artifacts" % label)
            return 1
    elif promote_rc != 0:
        return 1
    gateway_url = str(plan.get("router_health_url", "http://127.0.0.1:8000/healthz"))
    status = _gateway_status(gateway_url, _open=_open)
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
        _validate_promotion_configs(plan)
        _validate_promotion_profiles(plan)
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


def _validate_promotion_profiles(plan):
    """Prove profile policy is identical outside the declared affected tiers."""
    def entries(path):
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
        rows = document.get("entries", [])
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("router profile entries must be an array of objects")
        return {
            (row.get("tier_id"), row.get("work_class")): row
            for row in rows
        }

    forward = entries(plan["router_profile"])
    rollback = entries(plan["rollback_router_profile"])
    affected = set(plan["affected_tiers"])
    forward_unaffected = {key: value for key, value in forward.items() if key[0] not in affected}
    rollback_unaffected = {key: value for key, value in rollback.items() if key[0] not in affected}
    if forward_unaffected != rollback_unaffected:
        raise ValueError("unaffected router profile entries differ between promotion states")


def _validate_promotion_configs(plan):
    """Prove router config changes are limited to declared tier records."""
    def document(path):
        with open(path, "rb") as handle:
            value = tomllib.load(handle)
        router = value.get("router")
        if not isinstance(router, dict):
            raise ValueError("router config must contain a [router] table")
        tiers = router.get("tiers")
        if not isinstance(tiers, list) or not all(isinstance(row, dict) for row in tiers):
            raise ValueError("router tiers must be an array of tables")
        by_id = {}
        for row in tiers:
            tier_id = row.get("id")
            if not isinstance(tier_id, str) or not tier_id or tier_id in by_id:
                raise ValueError("router tiers must have unique non-empty ids")
            by_id[tier_id] = row
        comparable = copy.deepcopy(value)
        comparable_router = comparable["router"]
        comparable_router.pop("tiers", None)
        # mapping_version is deployment metadata; the actual routes, globals,
        # presets, purpose models, and server settings must remain identical.
        comparable_router.pop("mapping_version", None)
        return comparable, by_id

    forward_document, forward_tiers = document(plan["router_config"])
    rollback_document, rollback_tiers = document(plan["rollback_router_config"])
    if forward_document != rollback_document:
        raise ValueError(
            "router configs differ outside declared affected tier records"
        )
    if set(forward_tiers) != set(rollback_tiers):
        raise ValueError("router config tier sets differ between promotion states")
    affected = set(plan["affected_tiers"])
    if not affected or not affected <= set(forward_tiers):
        raise ValueError("affected_tiers must name existing router tiers")
    for tier_id in set(forward_tiers) - affected:
        if forward_tiers[tier_id] != rollback_tiers[tier_id]:
            raise ValueError(
                "unaffected router tier %r differs between promotion states" % tier_id
            )


def _artifact_digest(path, kind):
    with open(path, "rb") as handle:
        if kind == "config":
            document = tomllib.load(handle)
        else:
            document = json.load(handle)
    payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _promotion_artifact_digests(plan):
    return {
        "router_config": _artifact_digest(plan["router_config"], "config"),
        "router_profile": _artifact_digest(plan["router_profile"], "profile"),
        "rollback_router_config": _artifact_digest(
            plan["rollback_router_config"], "config"
        ),
        "rollback_router_profile": _artifact_digest(
            plan["rollback_router_profile"], "profile"
        ),
    }


def _deployed_router_digests(plan, *, _run=subprocess.run):
    """Read canonical config/profile digests from the running router container."""
    script = (
        "import hashlib,json,tomllib;"
        "c=tomllib.load(open('/etc/anvil/config.toml','rb'));"
        "p=json.load(open('/etc/anvil/profile.json','r',encoding='utf-8'));"
        "h=lambda x:hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest();"
        "print(json.dumps({'config':h(c),'profile':h(p)},sort_keys=True))"
    )
    container = str(plan.get("router_container", "anvil-router"))
    try:
        result = _run(
            ["docker", "exec", container, "python", "-c", script],
            capture_output=True, text=True,
        )
    except OSError as exc:
        raise RuntimeError("cannot inspect deployed router artifacts: %s" % exc) from exc
    if result.returncode != 0:
        raise RuntimeError(
            "cannot inspect deployed router artifacts: %s" % (
                (result.stderr or result.stdout or "docker exec failed").strip()
            )
        )
    try:
        value = json.loads(result.stdout)
        if set(value) != {"config", "profile"}:
            raise ValueError("unexpected keys")
        return value
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RuntimeError("deployed router returned invalid artifact digests") from exc


def _router_switch_state(plan, rollback, *, _run=subprocess.run):
    current = _deployed_router_digests(plan, _run=_run)
    forward = {
        "config": _artifact_digest(plan["router_config"], "config"),
        "profile": _artifact_digest(plan["router_profile"], "profile"),
    }
    reverse = {
        "config": _artifact_digest(plan["rollback_router_config"], "config"),
        "profile": _artifact_digest(plan["rollback_router_profile"], "profile"),
    }
    target, source = (reverse, forward) if rollback else (forward, reverse)
    if current == target:
        return "target"
    if current == source:
        return "source"
    return "drift"


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


def _snapshot_promotion_artifacts(plan, operation_dir):
    """Copy router artifacts into one operation-owned immutable input set."""
    os.makedirs(operation_dir, exist_ok=True)
    names = {
        "router_config": "router-forward.toml",
        "router_profile": "profile-forward.json",
        "rollback_router_config": "router-rollback.toml",
        "rollback_router_profile": "profile-rollback.json",
    }
    sources = {}
    for field, name in names.items():
        source = os.path.abspath(plan[field])
        target = os.path.join(operation_dir, name)
        sources[field] = source
        with open(source, "rb") as source_handle:
            payload = source_handle.read()
        with open(target, "xb") as target_handle:
            target_handle.write(payload)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        os.chmod(target, 0o400)
        plan[field] = target
    return sources


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
            return _cmd_promote_unlocked(
                serves, promotions, name, manifest_path,
                rollback=rollback, resume=resume, dry_run=dry_run,
                _run=_run, _open=_open, _sleep=_sleep,
            )
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
    _validate_promotion_configs(plan)
    _validate_promotion_profiles(plan)
    deployment = _compose_service_for_recipe(
        selected_serve, recipe, activation, _run=_run,
    )
    return recipe, plan_name, direction == "rollback", deployment


def cmd_switch(serves, promotions, registry, role, selector, manifest_path, *,
               resume=False, dry_run=False, _run=subprocess.run,
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
            if not dry_run:
                sources = _snapshot_promotion_artifacts(plan, operation["evidence_dir"])
                operation["source_router_artifacts"] = sources
                operation["router_artifact_snapshots"] = {
                    field: plan[field] for field in sources
                }
            _validate_promotion_topology(serves, plan)
            _validate_promotion_configs(plan)
            _validate_promotion_profiles(plan)
            try:
                router_state = _router_switch_state(plan, rollback, _run=_run)
            except (OSError, RuntimeError, ValueError) as exc:
                if not dry_run:
                    print("switch refused: %s" % exc, file=sys.stderr)
                    return 2
                router_state = "deferred"
                print("  deferred apply check: %s" % exc)
            if not dry_run and router_state in {"source", "target"}:
                plan["_expected_router_digests"] = _deployed_router_digests(plan, _run=_run)
                plan["_expected_artifact_digests"] = _promotion_artifact_digests(plan)
                operation["source_router_digests"] = dict(plan["_expected_router_digests"])
                operation["router_artifact_digests"] = dict(
                    plan["_expected_artifact_digests"]
                )
                operation["manifest_sha256"] = serve_recipes.registry_digest(manifest_path)
            if router_state == "drift":
                print(
                    "switch refused: deployed router config/profile matches neither the "
                    "expected source nor target state",
                    file=sys.stderr,
                )
                return 2
            target_name = plan["rollback" if rollback else "target"]
            target = _exact_serve(serves, target_name)
            if router_state == "target":
                state = docker_state(target["container"], _run=_run)
                if state == "running" and _health(
                    target["port"], target["health"], _open=_open
                ) == 200 and _serve_identity_ready(target, _open=_open) \
                        and _running_container_matches_recipe(
                            target, recipe, deployment, _run=_run
                        ):
                    print("  already active: router, container health, and exact model identity match")
                    return 0
                print("  target router state is active, but the serve needs guarded recovery")
            if not dry_run:
                operation["status"] = "running"
                _write_switch_journal(journal_path, operation)
                mutation_started = True
            rc = _cmd_promote_unlocked(
                serves, promotions, plan_name, manifest_path,
                rollback=rollback, resume=resume, dry_run=dry_run,
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


def docker_state(container, _run=subprocess.run):
    """Container state, distinguishing genuine absence from a docker error.

    Returns the raw docker status (running/exited/created/paused/restarting/...),
    or 'absent' (no such container), or 'error' (docker missing / daemon down /
    permission denied — i.e. we could NOT determine state, so callers must not
    claim success).
    """
    try:
        r = _run(["docker", "inspect", "-f", "{{.State.Status}}", container],
                 capture_output=True, text=True)
    except FileNotFoundError:
        return "error"  # docker not installed -> cannot manage containers
    if r.returncode != 0:
        return "absent" if "no such" in (r.stderr or "").lower() else "error"
    return (r.stdout or "").strip() or "unknown"


def _health(port, path, _open=urllib.request.urlopen):
    url = "http://127.0.0.1:%s%s" % (port, path)
    try:
        with _open(url, timeout=3) as resp:
            return getattr(resp, "status", None) or resp.getcode()
    except Exception:
        return None


def _gpu_lines(_run=subprocess.run):
    try:
        r = _run(["nvidia-smi", "--query-gpu=index,memory.used,memory.total",
                  "--format=csv,noheader,nounits"], capture_output=True, text=True)
    except FileNotFoundError:
        return []
    if r.returncode != 0:
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


def status_summary(serves, names=None, _run=subprocess.run, _open=urllib.request.urlopen):
    """Machine-readable serve status for MCP/automation.

    Mirrors :func:`cmd_status` without printing. The shape is intentionally
    simple and stable so agent tools do not scrape the human table.
    """
    selected = _select(serves, names or [])
    rows = []
    states = {}
    for s in selected:
        st = docker_state(s["container"], _run=_run)
        states[s["container"]] = st
        health = _health(s["port"], s.get("health", "/health"), _open=_open) if st == "running" else None
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
        })
    return {
        "serves": rows,
        "selected": [r["name"] for r in rows],
        "gpu_memory_lines": _gpu_lines(_run=_run),
        # The ledger spans the WHOLE manifest, not just `names`: committed
        # VRAM on a role comes from every declared serve, so a filtered view
        # of it would misreport `free`.
        "reservations": reservation_summary(serves, _run=_run, _states=states),
    }


def cmd_status(serves, names=None, _run=subprocess.run, _open=urllib.request.urlopen):
    # `names` (from positional selectors and/or --group) filters WHICH rows are
    # printed; the reservation ledger below still spans the WHOLE `serves` list,
    # because committed VRAM on a role comes from every declared serve — a
    # filtered ledger would misreport `free`. `names=None` prints every serve
    # (unchanged behavior). docker_state is memoized so a filtered view probes
    # only the rows it prints plus the reservation-declaring serves.
    selected = _select(serves, names) if names else list(serves)
    selected_containers = {s["container"] for s in selected}
    states = {}

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
    gpus = _gpu_lines(_run=_run)
    if gpus:
        print("\nGPU memory (index, used MiB, total MiB):")
        for g in gpus:
            print("  " + g)
    # ADR-0017 reservation ledger (T004): per-gpu_role capacity/reserve/
    # committed/free plus each declared reservation. Reuses the states probed
    # above (every manifest serve was just inspected), so this section adds no
    # docker calls; manifests without [[gpu_roles]] print nothing extra.
    budgets = reservations.budgets_of(serves)
    if budgets:
        ledger = reservations.build_ledger(serves, state_of, budgets=budgets)
        print("\nGPU reservations (ADR-0017, derived from docker state):")
        for _, role_ledger in sorted(ledger.items()):
            print("  " + role_ledger.describe())
            for r in role_ledger.reservations:
                print("    %s%s" % (r.describe(), "" if r.committed else " [not committed]"))
    return 0


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


def cmd_down(serves, names, dry_run=False, _run=subprocess.run):
    # ADR-0017: stopping a container IS the reservation release — the ledger is
    # derived from docker state, so no bookkeeping happens (or could drift) here.
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
        if st == "absent" or st in _STOPPED:
            print("  %s: %s (nothing to stop)" % (s["container"], st))
            continue
        # running / paused / restarting / removing / unknown -> stop (frees the GPU).
        # Honor --dry-run: `down` is state-changing (it frees GPUs / kills in-flight
        # serving), so a preview must NOT actually stop anything.
        print("  stop %s" % s["container"])
        if dry_run:
            continue
        r = _run(["docker", "stop", s["container"]], capture_output=True, text=True)
        if r.returncode == 0:
            # Verify the stop STUCK: a `restart: always` policy revives the
            # container immediately, silently un-freeing the GPU we just freed.
            # 'restarting' is the same revival caught mid-backoff — it will be
            # 'running' moments later, so it is NOT a clean stop either.
            st_after = docker_state(s["container"], _run=_run)
            if st_after in ("running", "restarting"):
                print("  WARNING: %s is %s again after stop (restart policy?) - "
                      "the GPU was NOT freed; `serves rm %s` removes it, or fix the "
                      "container's restart policy" % (s["container"], st_after, s["container"]))
                rc = 1
            else:
                print("  stopped %s" % s["container"])
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
    try:
        r = _run(["docker", "inspect", "-f", tmpl, container],
                 capture_output=True, text=True)
    except FileNotFoundError:
        return []
    if r.returncode != 0:
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


def cmd_up(serves, names, dry_run=False, recreate=False, _run=subprocess.run,
           evict=False, drain_timeout=EVICTION_DRAIN_TIMEOUT, router_url=None,
           _transition=None):
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
    state_of = lambda container: docker_state(container, _run=_run)  # noqa: E731
    # ADR-0017 reservation ledger admission: acquiring the targets' declared
    # VRAM reservations must fit their gpu_role budgets BEFORE any container
    # command runs — an over-budget request fails the whole batch with the
    # ledger printed, and nothing is started/recreated. Committed state is
    # derived from docker (running serves), so `serves down` releases a
    # reservation with no ledger bookkeeping. Read-only, so it also gates
    # --dry-run (the preview should show the same refusal the real run hits).
    # Serves/manifests without reservation fields skip this entirely.
    denial = reservations.deny_over_budget(serves, targets, state_of)
    if denial and evict:
        # ADR-0017 §5 eviction (gpu-reservations:T005): an over-budget
        # `on-demand` acquisition may stop committed `evictable` reservations
        # instead of failing — composing the ADR-0018 transition (quiesce +
        # bounded AdmissionLease drain) before each victim's container stops.
        # `resident` serves are never candidates; an impossible plan is the
        # same loud, ledger-printing refusal as plain admission.
        victims, lines = reservations.plan_eviction(serves, targets, state_of)
        if victims is None:
            for line in lines:
                print("  " + line)
            return 1
        if victims:
            transition = _transition or (
                lambda action, tier_id, timeout=None: _transition_cli(
                    router_url or DEFAULT_ROUTER_URL, action, tier_id,
                    timeout=timeout, _run=_run))
            evict_rc = _evict_victims(
                serves, victims, dry_run=dry_run, drain_timeout=drain_timeout,
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
            denial = reservations.deny_over_budget(serves, targets, state_of)
    if denial:
        for line in denial:
            print("  " + line)
        if not evict:
            victims, _ = reservations.plan_eviction(serves, targets, state_of)
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
            print("  %s: already running" % s["container"])
            continue
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
    return rc


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
        r = _run(argv, capture_output=True, text=True)
    except FileNotFoundError:
        print("cannot read logs: docker not available", file=sys.stderr)
        return 1
    sys.stdout.write(r.stdout or "")
    sys.stderr.write(r.stderr or "")  # serve startup errors go to stderr
    return r.returncode


_ACTIONS = (
    "status", "up", "down", "rm", "adopt", "logs", "groups", "switch",
    "promote", "render",
)
# Actions that accept `--group NAME` (repeatable) — they act across the whole
# manifest set (serves*.toml in the manifest's dir), not just one file.
_GROUP_ACTIONS = frozenset({"up", "down", "status"})

_ACTION_DESCRIPTIONS = {
    "status": "Show docker and health state for manifest serves.",
    "up": "Start manifest serves or an ad-hoc compose service.",
    "down": "Stop manifest serves and verify they stay stopped.",
    "rm": "Remove serve containers after explicit confirmation.",
    "adopt": "Bring externally-started serves under compose management.",
    "logs": "Show bounded or streaming docker logs for one serve.",
    "groups": "List serve groups across the manifest set and their members.",
    "switch": "Switch a deployment role to an activation-ready recipe.",
    "promote": "Promote a staged model recipe with preflight and full rollback.",
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
            "  anvil-serving serves switch heavy\n"
            "  anvil-serving serves switch heavy MODEL --dry-run\n"
            "  anvil-serving serves switch heavy MODEL --confirm\n\n"
            "Preview resolves the effective Compose service and reports any deferred "
            "live-state refusal. Apply requires exact source router artifacts, takes an "
            "exclusive role lock plus the common promotion lock, journals evidence, "
            "and retains automatic rollback."
            if action == "switch" else None
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    if action == "promote":
        p.add_argument("names", nargs=1, metavar="PLAN",
                       help="the [[promotion]] plan name from the manifest")
    elif action == "switch":
        p.add_argument("names", nargs=1, metavar="ROLE",
                       help="deployment role to switch (for example: heavy)")
        p.add_argument("recipe_selector", nargs="?", metavar="MODEL",
                       help="recipe model id or unique basename to activate; omit to list choices")
    elif action == "logs":
        p.add_argument("names", nargs=1, metavar="NAME",
                       help="serve name/container to read logs from.")
    elif action == "groups":
        p.set_defaults(names=[])
    else:
        p.add_argument("names", nargs="*",
                       help="serve names/containers to act on (default: all in the manifest).")
    p.add_argument("--manifest",
                   help="path to the serves manifest TOML (default: ./serves.toml if present, then ~/.anvil-serving/serves.toml).")
    if action in _GROUP_ACTIONS:
        p.add_argument("--group", action="append", metavar="NAME", dest="groups",
                       help="act on every serve tagged NAME across the manifest set "
                            "(serves*.toml in the manifest's dir); repeatable, unions with "
                            "names; the reserved 'all' selects every serve.")
    else:
        p.set_defaults(groups=None)
    if action == "groups":
        p.add_argument("--json", action="store_true", dest="json_out",
                       help="emit the group catalog as JSON for tooling.")
    else:
        p.set_defaults(json_out=False)
    if action in {"up", "down", "rm", "adopt", "switch", "promote"}:
        p.add_argument("--dry-run", action="store_true",
                       help="print what would run without touching any container.")
    else:
        p.set_defaults(dry_run=False)
    if action in {"rm", "adopt"}:
        p.add_argument("--yes", action="store_true",
                       help="skip the confirmation prompt (these actions docker rm -f containers).")
    else:
        p.set_defaults(yes=False)
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
    if action == "logs":
        p.add_argument("--tail", default="200",
                       help="trailing lines to show (default: %(default)s; 'all').")
        p.add_argument("--since",
                       help="only logs since a timestamp or relative time (e.g. 10m, 1h).")
        p.add_argument("--follow", action="store_true",
                       help="stream new output (Ctrl-C to stop).")
    else:
        p.set_defaults(tail="200", since=None, follow=False)
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
    else:
        p.set_defaults(recipe=None, registry=None, rollback=False, resume=False)
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

    # Reject conflicting selectors before resolving manifests or registries. This
    # is an argument error, so its result must not depend on which config files
    # happen to exist on the current host.
    if a.action == "switch" and a.recipe_selector and a.recipe:
        print(
            "choose either positional MODEL or --recipe MODEL, not both",
            file=sys.stderr,
        )
        return 2

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
    # A `--group` action (and `serves groups`) resolves across the whole manifest
    # SET (every serves*.toml in the manifest's dir), de-duped by container, so a
    # group can span serves.toml + serves.voice.toml + serves.comfyui.toml. Plain
    # positional-name operations keep loading the SINGLE manifest, unchanged.
    use_set = bool(a.groups) or a.action == "groups"
    try:
        serves = load_manifest_set(manifest_path) if use_set else load_manifest(manifest_path)
    except FileNotFoundError:
        search_hint = (
            a.manifest
            if a.manifest
            else ", ".join(default_manifest_candidates())
        )
        print(
            "manifest not found: %s (run `anvil-serving init` to generate one, "
            "place one at ~/.anvil-serving/serves.toml, or pass --manifest to "
            "point at an existing serves.toml)" % search_hint,
            file=sys.stderr,
        )
        return 2
    except Exception as e:  # malformed manifest
        print("bad manifest %s: %s" % (manifest_path, e), file=sys.stderr)
        return 2

    if a.action == "groups":
        return cmd_groups(serves, as_json=a.json_out)

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
        return cmd_status(serves, names=group_names)
    if a.action == "logs":
        return cmd_logs(serves, a.names, tail=a.tail, since=a.since, follow=a.follow)
    if a.action == "down":
        return cmd_down(serves, group_names if group_names is not None else a.names,
                        dry_run=a.dry_run)
    if a.action == "up":
        return cmd_up(serves, group_names if group_names is not None else a.names,
                      dry_run=a.dry_run, recreate=a.recreate,
                      evict=a.evict, drain_timeout=a.drain_timeout,
                      router_url=a.router_url)
    if a.action == "rm":
        return cmd_rm(serves, a.names, dry_run=a.dry_run, assume_yes=a.yes)
    if a.action == "adopt":
        return cmd_adopt(serves, a.names, dry_run=a.dry_run, assume_yes=a.yes)
    if a.action == "promote":
        try:
            promotions = load_promotions(manifest_path)
        except Exception as exc:
            print("bad promotion plan in %s: %s" % (manifest_path, exc), file=sys.stderr)
            return 2
        return cmd_promote(
            serves, promotions, a.names[0], os.path.abspath(manifest_path),
            rollback=a.rollback, resume=a.resume, dry_run=a.dry_run,
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
        return cmd_switch(
            serves, promotions, registry, a.names[0], selector,
            os.path.abspath(manifest_path), resume=a.resume, dry_run=a.dry_run,
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

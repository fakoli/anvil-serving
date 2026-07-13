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
import json
import math
import numbers
import os
import re
import shlex
import subprocess
import time
from . import guard
from . import reservations
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
# no OpenAI-compatible surface at all.
_ENGINES = {"vllm", "sglang", "llamacpp", "audio", "embedding", "reranker", "image"}
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# ADR-0017 GPU residency reservations: the residency vocabulary for a serve's
# declared VRAM reservation. "resident" is never evicted, "evictable" may be
# stopped to make room, "on-demand" is started per task and may evict
# "evictable" serves. (The VRAM types are reservations, never *Lease —
# AdmissionLease in router/admission.py is the request-admission layer.)
_RESIDENCIES = ("resident", "evictable", "on-demand")
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
    return serve["served_name"] in ids


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
    if _promotion_cli(promote, _run=_run) != 0:
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


def cmd_promote(serves, promotions, name, manifest_path, *, rollback=False,
                resume=False, dry_run=False, _run=subprocess.run, _open=urllib.request.urlopen,
                _sleep=time.sleep):
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


def cmd_status(serves, _run=subprocess.run, _open=urllib.request.urlopen):
    print("%-16s %-16s %-6s %-9s %s" % ("SERVE", "CONTAINER", "PORT", "DOCKER", "HEALTH"))
    states = {}
    for s in serves:
        st = docker_state(s["container"], _run=_run)
        states[s["container"]] = st
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
        ledger = reservations.build_ledger(
            serves,
            lambda container: states.get(container) or docker_state(container, _run=_run),
            budgets=budgets,
        )
        print("\nGPU reservations (ADR-0017, derived from docker state):")
        for _, role_ledger in sorted(ledger.items()):
            print("  " + role_ledger.describe())
            for r in role_ledger.reservations:
                print("    %s%s" % (r.describe(), "" if r.committed else " [not committed]"))
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
        print("  WARNING: %s was created serving %r but the manifest declares %r — "
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
            print("  evict %s: no router_tier declared — no router admission "
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
            print("  %s: in state %r — not auto-started; resolve manually" % (s["container"], st))
            rc = 1
            continue

        up = s.get("up")
        compose = _is_compose_up(up)

        if recreate:
            # Explicit clean recreate from `up` (compose OR script): force-remove the
            # existing container, then run the fresh-create `up`.
            if not up:
                print("  %s: --recreate requested but no `up` command in manifest — "
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
                print("  %s: absent and no `up` command in manifest — start it "
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
            print("  %s: ambiguous — matches serves %s; pass the exact container name to remove one"
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
        print("`logs` needs ONE serve; matched %d: %s — name just one."
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


_ACTIONS = ("status", "up", "down", "rm", "adopt", "logs", "promote", "render")

_ACTION_DESCRIPTIONS = {
    "status": "Show docker and health state for manifest serves.",
    "up": "Start manifest serves or an ad-hoc compose service.",
    "down": "Stop manifest serves and verify they stay stopped.",
    "rm": "Remove serve containers after explicit confirmation.",
    "adopt": "Bring externally-started serves under compose management.",
    "logs": "Show bounded or streaming docker logs for one serve.",
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
    )
    if action == "promote":
        p.add_argument("names", nargs=1, metavar="PLAN",
                       help="the [[promotion]] plan name from the manifest")
    elif action == "logs":
        p.add_argument("names", nargs=1, metavar="NAME",
                       help="serve name/container to read logs from.")
    else:
        p.add_argument("names", nargs="*",
                       help="serve names/containers to act on (default: all in the manifest).")
    p.add_argument("--manifest",
                   help="path to the serves manifest TOML (default: ./serves.toml if present, then ~/.anvil-serving/serves.toml).")
    if action in {"up", "down", "rm", "adopt", "promote"}:
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
    else:
        p.set_defaults(compose=None, recreate=False, evict=False,
                       drain_timeout=EVICTION_DRAIN_TIMEOUT, router_url=None)
    if action == "logs":
        p.add_argument("--tail", default="200",
                       help="trailing lines to show (default: %(default)s; 'all').")
        p.add_argument("--since",
                       help="only logs since a timestamp or relative time (e.g. 10m, 1h).")
        p.add_argument("--follow", action="store_true",
                       help="stream new output (Ctrl-C to stop).")
    else:
        p.set_defaults(tail="200", since=None, follow=False)
    if action == "promote":
        p.add_argument("--rollback", action="store_true",
                       help="restore the plan's rollback serve and router state")
        p.add_argument("--resume", action="store_true",
                       help="resume an interrupted promotion from an already-running target")
    else:
        p.set_defaults(rollback=False, resume=False)
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

    # `up --compose <file>`: ad-hoc/experiment serve from a compose file that is NOT in the
    # manifest — independent of serves.toml, so we neither require nor load a manifest here.
    if a.action == "up" and a.compose:
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
    try:
        serves = load_manifest(manifest_path)
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

    if a.action == "status":
        return cmd_status(serves)
    if a.action == "logs":
        return cmd_logs(serves, a.names, tail=a.tail, since=a.since, follow=a.follow)
    if a.action == "down":
        return cmd_down(serves, a.names, dry_run=a.dry_run)
    if a.action == "up":
        return cmd_up(serves, a.names, dry_run=a.dry_run, recreate=a.recreate,
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
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

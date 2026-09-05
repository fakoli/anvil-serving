"""Scaffold a local anvil-serving configuration.

`init` with NO flags scaffolds the FULL operational config set into the
operator config home (`~/.anvil-serving`, or `ANVIL_SERVING_HOME`) via
`scaffold_home()` — every router/config template,
manifest, compose file, topology, `.env.example`, and the tailnet-edge config —
so a fresh machine runs `anvil-serving serves up --group NAME` with zero
hand-assembly. Those files ship
as PACKAGE DATA under `anvil_serving/_scaffold_templates/` and resolve via
importlib.resources, so `init` works from a normal `pip`/`uv tool install`, not
just a source checkout (the #252 packaging regression this module's default path
now guards against).

`init --single-model` keeps the one-model quick start (genericity:T006): it
detects GPUs (`gpus.py`), picks a model from a `models sync` catalog (or an
explicit `--model`), and writes four mutually-consistent files into the CWD:

  ./docker-compose.yml   the SGLang/vLLM serve (via `deploy.render()`)
  ./serves.toml          the `[[serve]]` entry for `anvil-serving serves`
  ./router.toml          a `[router]` config: ONE tier and one capability alias
  ./operator-topology.toml  generic local command/resource ownership

The generated serving files agree on served-name and port — `tier.model == served-name`, the
tier's `base_url` port == the compose's published port, and `serves.toml`'s
container/port == the compose (the exact drift class genericity:T001/T009
exist to catch never has a chance to happen here, because `init` calls the
SAME `deploy.render()`/`deploy.append_serve_entry()`/`deploy.render_tier_stub()`
that `deploy` itself uses). Emitted serves bind loopback by default (T008).
GPU pinning falls back with a printed warning (never a silent mis-pin) when
`nvidia-smi` is absent (T007).
"""
import argparse
import importlib.resources as _resources
import ipaddress
import json
import ntpath
import os
import posixpath
import shlex
import subprocess
import sys
import tomllib

from . import deploy as _deploy
from . import edge as _edge
from . import guard
from . import gpus as _gpus
from .paths import config_home
from .topology import TopologyValidationError, parse_topology


class InitError(Exception):
    """Raised when `init` cannot proceed (no model found, etc.)."""


# --------------------------------------------------------------------------- #
# init (default): scaffold the FULL operational config set into ~/.anvil-serving
# --------------------------------------------------------------------------- #
# `init` with no flags scaffolds the whole operational set — every canonical
# router/config template, manifest, compose file, topology, .env template, and
# the tailnet-edge config — so a
# fresh machine can run `anvil-serving serves up --group voice` (or any group)
# with zero hand-assembly. (The single-model quick start moved behind
# `init --single-model`; see `run()` below.)
#
# Source of truth: the shipped reference instance under examples/fakoli-dark/
# (and the reference voice manifest under examples/voice/). Those canonical
# files are MIRRORED verbatim into this package's `_scaffold_templates/` data
# dir (so the set ships INSIDE the wheel and resolves via importlib.resources
# from any install location — not just a source checkout, the #252 regression).
# The mirror is kept byte-identical to examples/ by scripts/sync_scaffold_templates.py
# and guarded against drift by tests/test_init.py::test_scaffold_templates_match_examples.
#
# At scaffold time each template is read from the package and its real
# host-specific values (the reference machine's two GPU UUIDs and its tailnet
# address) are rewritten to clearly-marked placeholders; secrets are never
# written (the scaffold ships `.env.example`, whose values are empty, never
# `.env`).
_TEMPLATES_PACKAGE = "anvil_serving._scaffold_templates"

# Public synthetic UUID fixtures are mapped to explicit operator placeholders.
# Applied to every scaffolded file so no machine identity rides onto a fresh host.
_SANITIZE = (
    ("GPU-11111111-1111-1111-1111-111111111111", "GPU-REPLACE-WITH-COMPUTE-A-UUID"),
    ("GPU-22222222-2222-2222-2222-222222222222", "GPU-REPLACE-WITH-COMPUTE-B-UUID"),
    # Preserve the third synthetic fixture for files derived from the removed
    # RTX 5090 reference slot; generated operator files still receive Compute B.
    ("GPU-33333333-3333-3333-3333-333333333333", "GPU-REPLACE-WITH-COMPUTE-B-UUID"),
    ("GPU-00000000-0000-0000-0000-000000000002", "GPU-REPLACE-WITH-COMPUTE-A-UUID"),
    ("GPU-00000000-0000-0000-0000-000000000001", "GPU-REPLACE-WITH-COMPUTE-B-UUID"),
    ("100.64.0.10", "REPLACE-WITH-YOUR-TAILNET-IP"),
    ("192.0.2.20", "REPLACE-WITH-YOUR-TAILNET-IP"),
    ("192.0.2.10", "REPLACE-WITH-YOUR-MINI-TAILNET-IP"),
)

_COMPUTE_A_GPU_PLACEHOLDER = "GPU-REPLACE-WITH-COMPUTE-A-UUID"
_COMPUTE_B_GPU_PLACEHOLDER = "GPU-REPLACE-WITH-COMPUTE-B-UUID"
_TAILNET_IP_PLACEHOLDER = "REPLACE-WITH-YOUR-TAILNET-IP"
_MINI_TAILNET_IP_PLACEHOLDER = "REPLACE-WITH-YOUR-MINI-TAILNET-IP"
_TAILNET_IPV4 = ipaddress.ip_network("100.64.0.0/10")

# (destination filename in the scaffold, template filename in _scaffold_templates/,
#  canonical source path under the repo root the mirror is synced from).
# `edge.toml` is generated from edge.py's canonical routes (see below), so it is
# not listed here. The source-path column is used only by the drift-guard test
# and the sync script; runtime resolution reads the middle column as PACKAGE
# DATA via importlib.resources — never `__file__`/../examples (the #252 bug).
_SCAFFOLD_TEMPLATES = (
    # Canonical product configs. `router.toml` is the operator-friendly default
    # alias; the example-named copies preserve the documented config family.
    ("router.toml", "router.toml", "configs/example.toml"),
    ("example.toml", "example.toml", "configs/example.toml"),
    ("example-docker.toml", "example-docker.toml", "configs/example-docker.toml"),
    ("host.toml", "host.toml", "configs/host.example.toml"),
    ("serve-recipes.toml", "serve-recipes.toml", "configs/serve-recipes.toml"),
    ("serves.toml", "serves.toml", "examples/fakoli-dark/serves.toml"),
    ("services.toml", "services.toml", "examples/fakoli-dark/services.toml"),
    ("anvil-router.deepseek-pi.toml", "anvil-router.deepseek-pi.toml",
     "examples/fakoli-dark/anvil-router.deepseek-pi.toml"),
    ("anvil-router.live.toml", "anvil-router.live.toml",
     "examples/fakoli-dark/anvil-router.live.toml"),
    ("anvil-router.qwen35-rollback.toml", "anvil-router.qwen35-rollback.toml",
     "examples/fakoli-dark/anvil-router.qwen35-rollback.toml"),
    ("serves.voice.toml", "serves.voice.toml", "examples/fakoli-dark/serves.voice.toml"),
    ("serves.comfyui.toml", "serves.comfyui.toml", "examples/fakoli-dark/serves.comfyui.toml"),
    ("docker-compose.yml", "docker-compose.yml", "examples/fakoli-dark/docker-compose.yml"),
    ("Dockerfile.omni-small", "Dockerfile.omni-small",
     "examples/fakoli-dark/Dockerfile.omni-small"),
    ("docker-compose.voice-audio.yml", "docker-compose.voice-audio.yml",
     "examples/fakoli-dark/docker-compose.voice-audio.yml"),
    ("docker-compose.voice-proxy.yml", "docker-compose.voice-proxy.yml",
     "examples/fakoli-dark/docker-compose.voice-proxy.yml"),
    ("docker-compose.comfyui.yml", "docker-compose.comfyui.yml",
     "examples/fakoli-dark/docker-compose.comfyui.yml"),
    ("Dockerfile.comfyui", "Dockerfile.comfyui",
     "examples/fakoli-dark/Dockerfile.comfyui"),
    ("install-comfyui-node.sh", "install-comfyui-node.sh",
     "examples/fakoli-dark/install-comfyui-node.sh"),
    ("operator-topology.toml", "operator-topology.toml",
     "examples/fakoli-dark/operator-topology.toml"),
    (".env.example", "env.example", "examples/fakoli-dark/.env.example"),
    ("voice.toml", "voice.toml", "examples/fakoli-dark/voice.toml"),
)


def _templates_root():
    """The packaged `_scaffold_templates/` dir as an importlib.resources Traversable.

    Resolves relative to the INSTALLED `anvil_serving` package — works identically
    from a `pip`/`uv tool install`ed wheel and a source checkout, which is the
    whole point of shipping the set as package data (fixes #252, where init read
    `__file__/../examples`, a path that only exists in a source checkout)."""
    return _resources.files(_TEMPLATES_PACKAGE)


def _read_template(template_name):
    """Read one packaged scaffold template as text (UTF-8)."""
    return _templates_root().joinpath(template_name).read_text(encoding="utf-8")


def _sanitize(text):
    """Rewrite the reference machine's host-specific values to placeholders."""
    for real, placeholder in _SANITIZE:
        text = text.replace(real, placeholder)
    return text


def render_edge_config():
    """Render an ADR-0019 tailnet-edge config from edge.py's canonical route map.

    Generated (not copied) so the scaffolded `edge.toml` stays in lockstep with
    `anvil-serving edge {render,up,down}` — the routes come straight from
    `edge.DEFAULT_ROUTES`.
    """
    lines = [
        "# anvil-serving tailnet edge config (ADR-0019) — generated by `anvil-serving init`.",
        "# `anvil-serving edge {render,status,up,down}` path-routes the host's single",
        "# MagicDNS name to local services. Additive + idempotent; `down` removes only",
        "# the mounts it manages, never an operator-set `tailscale serve` mapping.",
        "# See docs/adr/0019-anvil-serving-owns-the-tailnet-edge.md.",
        "[edge]",
        f"https_port = {_edge.DEFAULT_HTTPS_PORT}",
        f'host = "{_edge.DEFAULT_TARGET_HOST}"   # default target host for port-only routes (loopback)',
        "",
        "[edge.routes]",
    ]
    for mount, port in _edge.DEFAULT_ROUTES:
        lines.append(f'"{mount}" = {port}')
    lines.append('# "/dashboard" = 8766   # extensible: add future dashboards under the same name')
    return "\n".join(lines) + "\n"


def _empty_host_discovery():
    return {
        "compute_a_gpu": None,
        "compute_b_gpu": None,
        "tailnet_ip": None,
        "tailnet_source": None,
        "topology_host": None,
    }


def _canonical_tailnet_ipv4(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise InitError(f"tailnet IP {value!r} is not a valid IP address") from exc
    if address.version != 4 or address not in _TAILNET_IPV4:
        raise InitError(
            f"tailnet IP {value!r} must be an IPv4 address in 100.64.0.0/10"
        )
    return str(address)


def _detect_tailnet_ipv4(_run=subprocess.check_output):
    """Return this node's Tailscale IPv4 address, or ``None`` when unavailable."""
    try:
        output = _run(
            ["tailscale", "ip", "-4"],
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            timeout=10,
        )
    except Exception:
        return None
    for line in output.splitlines():
        try:
            return _canonical_tailnet_ipv4(line.strip())
        except InitError:
            continue
    return None


def discover_host(
    *,
    compute_a_gpu_uuid=None,
    compute_b_gpu_uuid=None,
    primary_gpu_uuid=None,
    auxiliary_gpu_uuid=None,
    tailnet_ip=None,
    probe=True,
    _gpu_run=subprocess.check_output,
    _tailscale_run=subprocess.check_output,
):
    """Discover stable host values used by the full operator scaffold.

    Explicit values win. Otherwise Compute A receives the largest observed
    card and Compute B the smallest. Equal-VRAM cards are ordered by canonical
    UUID, never volatile runtime index, so their persisted role identity stays
    stable across reboots. A single-GPU host fills Compute A only rather than
    silently scheduling two concurrent roles onto one device.

    ``primary_gpu_uuid`` and ``auxiliary_gpu_uuid`` are compatibility aliases
    for pre-symmetric callers. New code and generated configuration use the
    compute-role vocabulary.
    """
    if compute_a_gpu_uuid and primary_gpu_uuid and compute_a_gpu_uuid != primary_gpu_uuid:
        raise InitError("Compute A and legacy Primary GPU overrides disagree")
    if compute_b_gpu_uuid and auxiliary_gpu_uuid and compute_b_gpu_uuid != auxiliary_gpu_uuid:
        raise InitError("Compute B and legacy Auxiliary GPU overrides disagree")
    compute_a_gpu_uuid = compute_a_gpu_uuid or primary_gpu_uuid
    compute_b_gpu_uuid = compute_b_gpu_uuid or auxiliary_gpu_uuid
    try:
        explicit_compute_a = (
            _gpus.canonical_gpu_uuid(compute_a_gpu_uuid) if compute_a_gpu_uuid else None
        )
        explicit_compute_b = (
            _gpus.canonical_gpu_uuid(compute_b_gpu_uuid)
            if compute_b_gpu_uuid
            else None
        )
    except _gpus.GpuRoleResolutionError as exc:
        raise InitError(str(exc)) from exc
    if explicit_compute_a and explicit_compute_a == explicit_compute_b:
        raise InitError(
            "Compute A and Compute B GPU UUID overrides must identify distinct GPUs"
        )

    observed = []
    observed_uuids = set()
    gpu_rows = _gpus.list_gpus_with_memory(_run=_gpu_run) if probe else ()
    for row in gpu_rows:
        try:
            uuid = _gpus.canonical_gpu_uuid(row["uuid"])
        except (KeyError, _gpus.GpuRoleResolutionError):
            continue
        if uuid in observed_uuids or row["memory_total_mib"] <= 0:
            continue
        observed_uuids.add(uuid)
        observed.append({**row, "uuid": uuid})
    observed.sort(
        key=lambda row: (-row["memory_total_mib"], row["uuid"]),
    )
    observed_by_uuid = {row["uuid"]: row for row in observed}
    selected = {uuid for uuid in (explicit_compute_a, explicit_compute_b) if uuid}

    def role_value(explicit, candidates):
        if explicit:
            row = observed_by_uuid.get(explicit)
            return {
                "uuid": explicit,
                "name": row["name"] if row else None,
                "memory_total_mib": row["memory_total_mib"] if row else None,
                "source": "override",
            }
        for row in candidates:
            if row["uuid"] in selected:
                continue
            selected.add(row["uuid"])
            return {**row, "source": "detected"}
        return None

    compute_a_gpu = role_value(explicit_compute_a, observed)
    compute_b_gpu = role_value(explicit_compute_b, reversed(observed))
    if observed:
        for label, explicit in (
            ("Compute A", explicit_compute_a),
            ("Compute B", explicit_compute_b),
        ):
            if explicit and explicit not in observed_by_uuid:
                raise InitError(
                    f"{label} GPU override {explicit!r} was not reported by nvidia-smi"
                )
    if tailnet_ip:
        resolved_tailnet_ip = _canonical_tailnet_ipv4(tailnet_ip)
        tailnet_source = "override"
    elif probe:
        resolved_tailnet_ip = _detect_tailnet_ipv4(_run=_tailscale_run)
        tailnet_source = "detected" if resolved_tailnet_ip else None
    else:
        resolved_tailnet_ip = None
        tailnet_source = None
    topology_host = None
    if compute_a_gpu or compute_b_gpu:
        topology_host = "fakoli-dark"
    elif sys.platform == "darwin":
        topology_host = "fakoli-mini"
    return {
        "compute_a_gpu": compute_a_gpu,
        "compute_b_gpu": compute_b_gpu,
        "tailnet_ip": resolved_tailnet_ip,
        "tailnet_source": tailnet_source,
        "topology_host": topology_host,
    }


def _personalize_home_text(text, discovery):
    replacements = [
        (_COMPUTE_A_GPU_PLACEHOLDER,
         discovery["compute_a_gpu"]["uuid"] if discovery["compute_a_gpu"] else None),
        (_COMPUTE_B_GPU_PLACEHOLDER,
         discovery["compute_b_gpu"]["uuid"] if discovery["compute_b_gpu"] else None),
    ]
    if discovery["topology_host"] == "fakoli-dark":
        replacements.append((_TAILNET_IP_PLACEHOLDER, discovery["tailnet_ip"]))
        text = text.replace(
            'command_host = "host:fakoli-mini"',
            'command_host = "host:fakoli-dark"',
        ).replace(
            'command_runtime = "runtime:mini-native"',
            'command_runtime = "runtime:dark-native"',
        )
    elif discovery["topology_host"] == "fakoli-mini":
        replacements.append((_MINI_TAILNET_IP_PLACEHOLDER, discovery["tailnet_ip"]))
    for placeholder, value in replacements:
        if value:
            text = text.replace(placeholder, value)
    return text


def _copy_env_command(out_dir, platform_name=None):
    platform_name = os.name if platform_name is None else platform_name
    if platform_name == "nt":
        source = ntpath.join(out_dir, ".env.example")
        destination = ntpath.join(out_dir, ".env")

        def quote(value):
            return "'" + value.replace("'", "''") + "'"

        return "Copy-Item -LiteralPath %s -Destination %s" % (
            quote(source),
            quote(destination),
        )
    source = posixpath.join(out_dir, ".env.example")
    destination = posixpath.join(out_dir, ".env")
    return "cp -- %s %s" % (shlex.quote(source), shlex.quote(destination))


def _home_plan(discovery=None):
    """Build the ordered (dest_name, text) list for the home scaffold.

    Reads + sanitizes every packaged template, then appends the generated
    `edge.toml`. Raises InitError if any packaged template is missing — that
    would mean a broken install (the templates ship as package data), so fail
    loud rather than write a partial set.
    """
    root = _templates_root()
    missing = [tmpl for _dest, tmpl, _src in _SCAFFOLD_TEMPLATES
               if not root.joinpath(tmpl).is_file()]
    if missing:
        raise InitError(
            "cannot scaffold the home config set — the packaged reference templates "
            "are not available in this install (missing: %s). This indicates a broken "
            "anvil-serving install; reinstall the package."
            % ", ".join(sorted(missing)))
    host = discovery or _empty_host_discovery()
    plan = [
        (dest_name, _personalize_home_text(_sanitize(_read_template(tmpl)), host))
        for dest_name, tmpl, _src in _SCAFFOLD_TEMPLATES
    ]
    plan.append(("edge.toml", _personalize_home_text(render_edge_config(), host)))
    topology_text = dict(plan)["operator-topology.toml"]
    if "GPU-REPLACE-WITH-" not in topology_text:
        try:
            parse_topology(tomllib.loads(topology_text))
        except (tomllib.TOMLDecodeError, TopologyValidationError) as exc:
            raise InitError(
                "could not generate a valid operator topology (%s)" % exc
            ) from exc
    return plan


def _scaffold_config(out_dir, discovery=None):
    """Scaffold the complete canonical config set into an explicit directory.

    Keeping the write path explicit prevents destination selection from leaking
    into the safety-critical backup behavior.
    """
    target = os.path.abspath(os.path.expanduser(out_dir))
    plan = _home_plan(discovery)  # validate + read everything before touching the target

    os.makedirs(target, exist_ok=True)
    written, backed_up, unchanged = [], [], []
    for dest_name, text in plan:
        dest = os.path.join(target, dest_name)
        if os.path.exists(dest):
            try:
                with open(dest, encoding="utf-8") as existing:
                    if existing.read() == text:
                        unchanged.append(dest)
                        continue
            except OSError as exc:
                raise InitError(
                    "could not compare existing %s before rewriting it (%s); "
                    "fix or remove the file and re-run"
                    % (dest_name, exc)
                ) from exc
        try:
            bak = guard.backup_file(dest)
        except OSError as e:
            raise InitError(
                "could not back up existing %s before rewriting it (%s); fix or "
                "remove the file and re-run — refusing to overwrite without a backup"
                % (dest_name, e)) from e
        if bak:
            backed_up.append((dest, bak))
        with open(dest, "w", encoding="utf-8") as f:
            f.write(text)
        written.append(dest)

    return {
        "out_dir": target,
        "written": written,
        "backed_up": backed_up,
        "unchanged": unchanged,
    }


def scaffold_home(
    out_dir=None,
    *,
    detect_host=True,
    compute_a_gpu_uuid=None,
    compute_b_gpu_uuid=None,
    primary_gpu_uuid=None,
    auxiliary_gpu_uuid=None,
    tailnet_ip=None,
    _gpu_run=subprocess.check_output,
    _tailscale_run=subprocess.check_output,
):
    """Scaffold the complete canonical config set into the machine-wide home.

    `out_dir` defaults to the operator config home (`~/.anvil-serving`, honoring
    ANVIL_SERVING_HOME) — the default search dir for `serves up` etc. — so a
    fresh machine is operational with no path juggling; pass an explicit dir to
    override (e.g. a temp dir for verification).

    Every file is backed up (guard.backup_file → numbered `.anvil.bak.N`) before
    it is overwritten, exactly like `init --single-model`: an existing operator file
    is NEVER clobbered silently, and a backup that cannot be written aborts the
    whole scaffold rather than proceeding without a revert path. Returns a dict
    describing what was written for the CLI to report and tests to assert on.
    """
    discovery = discover_host(
        compute_a_gpu_uuid=compute_a_gpu_uuid,
        compute_b_gpu_uuid=compute_b_gpu_uuid,
        primary_gpu_uuid=primary_gpu_uuid,
        auxiliary_gpu_uuid=auxiliary_gpu_uuid,
        tailnet_ip=tailnet_ip,
        probe=detect_host,
        _gpu_run=_gpu_run,
        _tailscale_run=_tailscale_run,
    )
    result = _scaffold_config(out_dir or config_home(), discovery)
    result["discovery"] = discovery
    return result


def _read_catalog(catalog_dir):
    """[{**card fields}, ...] read from `<catalog_dir>/cards/*.json` (written
    by `anvil-serving models sync`). [] if the catalog dir/cards are absent —
    never raises (a missing catalog just means "nothing to auto-pick")."""
    cards_dir = os.path.join(catalog_dir, "cards")
    if not os.path.isdir(cards_dir):
        return []
    out = []
    for fn in sorted(os.listdir(cards_dir)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(cards_dir, fn), encoding="utf-8") as f:
                out.append(json.load(f))
        except Exception:
            continue
    return out


def pick_model(catalog, explicit_model=None):
    """Choose a model to deploy.

    `explicit_model` (a local dir path) always wins. Otherwise pick the
    largest `sglang_loadable` (and not sm_120-hazardous) catalog entry — a
    coding harness wants the biggest model that safely loads — falling back
    to any safetensors entry if none is marked loadable. Returns a facts dict
    (`local_path` at minimum) or None if nothing qualifies.
    """
    if explicit_model:
        name = os.path.basename(os.path.normpath(explicit_model)) or explicit_model
        return {"local_path": explicit_model, "id": name}
    candidates = [
        c for c in catalog
        if c.get("local_path") and c.get("sglang_loadable") and not c.get("sm120_caveat")
    ]
    if not candidates:
        candidates = [c for c in catalog if c.get("local_path") and c.get("format") == "safetensors"]
    if not candidates:
        return None
    candidates.sort(key=lambda c: c.get("size_gb") or 0, reverse=True)
    return candidates[0]


def _served_name(model_facts):
    raw = model_facts.get("id") or model_facts.get("repo") or model_facts.get("local_path") or "local-specialist"
    base = os.path.basename(str(raw).rstrip("/\\")) or "local-specialist"
    return _deploy._slug(base).lower()


def render_router_config(
    tier_id, served_name, port, context_limit=131072, disable_thinking=False
):
    """Render a complete capability-alias router config for one local serve."""
    tier_block = _deploy.render_tier_stub(
        tier_id, served_name, port, context_limit=context_limit,
        disable_thinking=disable_thinking)
    return (
        "# anvil-serving router config — generated by `anvil-serving init`.\n"
        "# See configs/example.toml for the full annotated reference.\n"
        "# Add aliases only when each alias has one explicit local owner.\n"
        "[router]\n"
        f"{tier_block}\n"
        "[router.model_routes]\n"
        f'"llm.primary" = "{tier_id}"\n'
    )


def render_starter_topology(port=30000):
    """Render deterministic local ownership without inspecting machine identity.

    OS and GPU identity intentionally remain absent. A deployment overlay must
    declare those facts before OS-specific repair or GPU-bound target resolution.
    """
    return (
        "# Generic local topology generated by `anvil-serving init`.\n"
        "# This base file contains no machine identity, GPU UUIDs, or credentials.\n"
        "# Add deployment-specific addresses, host OS, GPU roles, and authenticated\n"
        "# controllers in a separate overlay; do not infer them from the current host.\n"
        "schema_version = 1\n"
        'id = "local-starter"\n'
        'command_host = "host:local-host"\n'
        'command_runtime = "runtime:local-native"\n\n'
        "[[capacity_policies]]\n"
        'id = "local-model-capable"\n'
        "allow_model_workloads = true\n\n"
        "[[hosts]]\n"
        'id = "local-host"\n'
        'roles = ["operator", "router", "serve", "media"]\n'
        'address = "127.0.0.1"\n'
        'capacity_policy = "local-model-capable"\n\n'
        "[[runtimes]]\n"
        'id = "local-native"\n'
        'host = "local-host"\n'
        'role = "native"\n\n'
        "[[runtimes]]\n"
        'id = "local-docker"\n'
        'host = "local-host"\n'
        'role = "docker"\n\n'
        "[[resources]]\n"
        'id = "local-host-resource"\n'
        'role = "host"\n'
        'host = "local-host"\n'
        'runtime = "local-native"\n'
        'workload = "service"\n\n'
        "[[resources]]\n"
        'id = "local-media-gateway"\n'
        'role = "media-gateway"\n'
        'host = "local-host"\n'
        'runtime = "local-docker"\n'
        'endpoint = "http://127.0.0.1:8000"\n'
        'endpoint_kind = "host-relative-loopback"\n'
        'workload = "service"\n\n'
        "[[resources]]\n"
        'id = "local-media-worker"\n'
        'role = "media-worker"\n'
        'host = "local-host"\n'
        'runtime = "local-docker"\n'
        'endpoint = "http://127.0.0.1:8188"\n'
        'endpoint_kind = "host-relative-loopback"\n'
        'workload = "media"\n\n'
        "[[resources]]\n"
        'id = "local-router"\n'
        'role = "router"\n'
        'host = "local-host"\n'
        'runtime = "local-docker"\n'
        'endpoint = "http://127.0.0.1:8000/v1"\n'
        'endpoint_kind = "host-relative-loopback"\n'
        'workload = "service"\n\n'
        "[[resources]]\n"
        'id = "local-model-serve"\n'
        'role = "model-serve"\n'
        'host = "local-host"\n'
        'runtime = "local-docker"\n'
        f'endpoint = "http://127.0.0.1:{port}/v1"\n'
        'endpoint_kind = "host-relative-loopback"\n'
        'workload = "llm"\n\n'
        "[[resources]]\n"
        'id = "local-model-catalog"\n'
        'role = "model-catalog"\n'
        'host = "local-host"\n'
        'runtime = "local-native"\n'
        'workload = "model"\n\n'
        "[[resources]]\n"
        'id = "local-evaluation"\n'
        'role = "evaluation"\n'
        'host = "local-host"\n'
        'runtime = "local-native"\n'
        'workload = "service"\n'
    )


def run(model=None, gpu="0", catalog_dir="./model-library", out_dir=".",
        served_name=None, tier_id=None, port=30000, context=131072,
        engine=None, disable_thinking=False, bind=_deploy.LOOPBACK_BIND,
        _run=None):
    """Generate compose, manifests, router config, and topology into `out_dir`.

    Returns a dict describing what was written, for the CLI to report and for
    tests to assert against without parsing stdout. Raises `InitError` if no
    model can be resolved.
    """
    catalog = _read_catalog(catalog_dir)
    facts = pick_model(catalog, model)
    if facts is None:
        raise InitError(
            f"no model found: pass --model <local dir>, or run "
            f"`anvil-serving models sync --out {catalog_dir}` first to build a catalog."
        )
    model_path = model or facts["local_path"]
    served = served_name or _served_name(facts)
    tid = tier_id or served
    disable_th = disable_thinking or bool(facts.get("thinking_default"))
    render_kwargs = {}
    if _run is not None:
        render_kwargs["_run"] = _run
    eng = engine or _deploy._infer_engine(model_path)
    topology_text = render_starter_topology(port=port)
    try:
        parse_topology(tomllib.loads(topology_text))
    except (tomllib.TOMLDecodeError, TopologyValidationError) as exc:
        raise InitError(f"could not generate a valid starter topology ({exc})") from exc

    os.makedirs(out_dir, exist_ok=True)
    compose_out = os.path.join(out_dir, "docker-compose.yml")
    manifest_out = os.path.join(out_dir, "serves.toml")
    router_out = os.path.join(out_dir, "router.toml")
    topology_out = os.path.join(out_dir, "operator-topology.toml")

    # Guard: these are operator-editable configs — never clobber hand edits
    # without a numbered .anvil.bak.N sibling to revert to (same convention as
    # `host wsl-config`). Fail CLOSED but clean: if the backup cannot be
    # written, refuse to overwrite rather than proceeding without a revert
    # path (or dying with a raw traceback).
    for existing in (compose_out, router_out, topology_out):
        try:
            bak = guard.backup_file(existing)
        except OSError as e:
            raise InitError(
                f"could not back up existing {os.path.basename(existing)} before "
                f"rewriting it ({e}); fix or remove the file and re-run — refusing "
                f"to overwrite without a backup")
        if bak:
            print(f"[anvil-serving] backed up existing {os.path.basename(existing)} "
                  f"-> {os.path.basename(bak)}")

    compose_text = _deploy.render(
        model_path, gpu, context, served, port=port, bind=bind, engine=eng,
        disable_thinking=disable_th, **render_kwargs)
    with open(compose_out, "w", encoding="utf-8") as f:
        f.write(compose_text)

    service = "vllm" if eng == "vllm" else "sglang"
    container = f"vllm-{_deploy._slug(served)}" if eng == "vllm" else "sglang"
    # forward-slash: spliced into a TOML string then shlex-split (T009 note).
    compose_rel = compose_out.replace(os.sep, "/")
    up = f"docker compose -f {compose_rel} up -d {service}"
    _deploy.append_serve_entry(manifest_out, tid, container, port, served, up, engine=eng)

    router_text = render_router_config(
        tid, served, port, context_limit=context, disable_thinking=disable_th)
    with open(router_out, "w", encoding="utf-8") as f:
        f.write(router_text)

    try:
        with open(topology_out, "w", encoding="utf-8") as f:
            f.write(topology_text)
    except OSError as exc:
        raise InitError(f"could not write the starter topology ({exc})") from exc

    return {
        "model_path": model_path, "served_name": served, "tier_id": tid,
        "engine": eng, "port": port, "compose": compose_out,
        "manifest": manifest_out, "router": router_out, "topology": topology_out,
        "disable_thinking": disable_th,
    }


def main(argv):
    ap = argparse.ArgumentParser(
        prog="anvil-serving init",
        description="Scaffold the FULL operational config set into the config home "
                    "so `serves up --group NAME` works with zero "
                    "hand-assembly. Use --single-model for a one-model quick bring-up "
                    "into the CWD instead.")
    ap.add_argument("--model", default=None,
                    help="local model dir mounted into the container "
                         "(default: pick the biggest loadable entry from --catalog-dir)")
    ap.add_argument("--catalog-dir", default="./model-library",
                    help="`anvil-serving models sync` output dir to auto-pick a "
                         "model from (default: %(default)s)")
    ap.add_argument("--gpu", default="0", help="GPU index or GPU-UUID (default: %(default)s)")
    ap.add_argument("--served-name", default=None,
                    help="served-model-name (default: derived from the model id)")
    ap.add_argument("--tier-id", default=None, help="router tier id (default: --served-name)")
    ap.add_argument("--port", type=int, default=30000)
    ap.add_argument("--context", type=int, default=131072)
    ap.add_argument("--engine", choices=["sglang", "vllm"], default=None,
                    help="serving engine (default: inferred from the model's weight format)")
    ap.add_argument("--disable-thinking", action="store_true",
                    help="force-disable a thinking-by-default model (default: "
                         "auto from the catalog's thinking_default)")
    ap.add_argument("--bind", default=None, help="publish address (default: 127.0.0.1)")
    ap.add_argument("--expose-lan", action="store_true", help="shorthand for --bind 0.0.0.0")
    ap.add_argument("--single-model", action="store_true",
                    help="scaffold a single-model quick bring-up (docker-compose.yml + "
                         "serves.toml + router.toml + operator-topology.toml) into the "
                         "CWD instead of the full operational config home. The --model/"
                         "--catalog-dir/--gpu/--port/etc. flags apply to this mode.")
    # Retained as a hidden compatibility alias for the default home scaffold.
    ap.add_argument("--home", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--out-dir", default=None,
                    help="where to write the files (default: the config home "
                         "with --single-model, the CWD)")
    ap.add_argument(
        "--compute-a-gpu-uuid",
        default=None,
        help="Compute A GPU UUID (default: highest-VRAM GPU; UUID breaks equal-VRAM ties)",
    )
    ap.add_argument(
        "--compute-b-gpu-uuid",
        default=None,
        help="Compute B GPU UUID (default: next distinct GPU; UUID breaks equal-VRAM ties)",
    )
    ap.add_argument("--primary-gpu-uuid", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--auxiliary-gpu-uuid", default=None, help=argparse.SUPPRESS)
    ap.add_argument(
        "--tailnet-ip",
        default=None,
        help="this host's Tailscale IPv4 address (default: `tailscale ip -4`)",
    )
    ap.add_argument(
        "--no-detect-host",
        action="store_true",
        help="leave GPU UUID and tailnet IP placeholders instead of probing this host",
    )
    a = ap.parse_args(argv)

    if not a.single_model:
        if a.home:
            print("[anvil-serving] note: `--home` is now the default; run `init` with no flag "
                  "(use `--single-model` for the CWD single-model bring-up).", file=sys.stderr)
        return _main_home(a)

    bind = a.bind or (_deploy.LAN_BIND if a.expose_lan else _deploy.LOOPBACK_BIND)

    try:
        result = run(
            model=a.model, gpu=a.gpu, catalog_dir=a.catalog_dir, out_dir=a.out_dir or ".",
            served_name=a.served_name, tier_id=a.tier_id, port=a.port,
            context=a.context, engine=a.engine, disable_thinking=a.disable_thinking,
            bind=bind)
    except InitError as e:
        print(f"[anvil-serving] {e}", file=sys.stderr)
        return 2

    print("wrote:")
    print("  " + result["compose"])
    print("  " + result["manifest"])
    print("  " + result["router"])
    print("  " + result["topology"])
    print()
    print("model:  " + result["model_path"])
    print("engine: " + result["engine"])
    print("tier:   %s (served-model-name %r, port %s)"
          % (result["tier_id"], result["served_name"], result["port"]))
    print()
    print("Remaining manual steps:")
    print("  1. Add a deployment overlay for real host OS, addresses, GPU roles, or controllers.")
    print("     Keep operator-topology.toml as the generic base; validate both with `topology validate`.")
    print("  2. anvil-serving serves up            # docker compose up -d, per serves.toml")
    print("  3. anvil-serving serves status         # wait for it to report healthy")
    print("  4. anvil-serving router run --config %s" % result["router"])
    print("  5. Point your harness at http://127.0.0.1:8000 (ANTHROPIC_BASE_URL) and set")
    print("     your client model to the capability alias llm.primary.")
    print("     For OpenClaw: see docs/OPENCLAW-INTEGRATION-SPEC.md for the provider adapter.")
    return 0


def _main_home(a):
    """Scaffold every canonical config into the operator config home."""
    try:
        result = scaffold_home(
            out_dir=a.out_dir,
            detect_host=not a.no_detect_host,
            compute_a_gpu_uuid=a.compute_a_gpu_uuid,
            compute_b_gpu_uuid=a.compute_b_gpu_uuid,
            primary_gpu_uuid=a.primary_gpu_uuid,
            auxiliary_gpu_uuid=a.auxiliary_gpu_uuid,
            tailnet_ip=a.tailnet_ip,
        )
    except InitError as e:
        print(f"[anvil-serving] {e}", file=sys.stderr)
        return 2

    if result["written"]:
        print("scaffolded or updated the config set in %s:" % result["out_dir"])
        for path in result["written"]:
            print("  " + os.path.basename(path))
    else:
        print("configuration already up to date in %s." % result["out_dir"])
    if result["unchanged"]:
        print(
            "%s unchanged file(s) left in place; no backup or rewrite needed."
            % len(result["unchanged"])
        )
    if result["backed_up"]:
        print()
        print("backed up existing operator files before overwrite:")
        for path, bak in result["backed_up"]:
            print("  %s -> %s" % (os.path.basename(path), os.path.basename(bak)))
    discovery = result["discovery"]
    print()
    print("Host discovery:")
    for label, key in (
        ("Compute A GPU", "compute_a_gpu"),
        ("Compute B GPU", "compute_b_gpu"),
    ):
        gpu = discovery[key]
        if gpu:
            details = gpu["uuid"]
            if gpu.get("name"):
                details = "%s - %s" % (gpu["name"], details)
            if gpu.get("memory_total_mib") is not None:
                details += " (%s MiB)" % gpu["memory_total_mib"]
            print("  %s: %s [%s]" % (label, details, gpu["source"]))
        else:
            print("  %s: not detected; placeholder preserved" % label)
    if discovery["tailnet_ip"]:
        print("  Tailnet IPv4: %s [%s]"
              % (discovery["tailnet_ip"], discovery["tailnet_source"]))
    else:
        print("  Tailnet IPv4: not detected; placeholder preserved")
    if discovery["topology_host"]:
        print("  Topology command host: %s [detected]" % discovery["topology_host"])
    else:
        print("  Topology command host: not detected; reference default preserved")
    print("  Secrets: copy .env.example to .env and fill it in (never committed).")
    print()
    print("Next steps (zero hand-assembly):")
    print("  1. %s   # then fill in secrets" % _copy_env_command(result["out_dir"]))
    print("  2. anvil-serving serves groups          # see the resolvable groups")
    print("  3. anvil-serving serves up --group voice   # (or llm-stack / comfy / ...)")
    print("  4. anvil-serving serves status")
    router_path = os.path.join(result["out_dir"], "router.toml")
    if a.out_dir:
        print("  5. anvil-serving router run --config %s"
              "   # front door on 127.0.0.1:8000" % router_path)
    else:
        print("  5. anvil-serving router run"
              "   # uses the config-home router.toml; front door on 127.0.0.1:8000")
    edge_path = os.path.join(result["out_dir"], "edge.toml")
    print("  6. Optional tailnet edge (ADR-0019): anvil-serving edge render "
          "--config %s" % edge_path)
    return 0

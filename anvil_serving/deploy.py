"""Render a tuned SGLang docker-compose for a given GPU + model (the hard-won defaults baked in).

GPU pinning (genericity:T007): `--gpu` accepts an index (``0``, ``"0"``) or a
``GPU-...`` UUID. It is resolved to a stable UUID via ``anvil_serving.gpus``
(shared with `multiplexer`) so the emitted compose pins the card the reliable
way — `CUDA_DEVICE_ORDER=PCI_BUS_ID` + `CUDA_VISIBLE_DEVICES=<uuid>` — because
Docker Desktop's WSL2 backend ignores `device_ids`-only pinning (CLAUDE.md
gotcha #13). When `nvidia-smi` is absent, resolution falls back to the bare
index/spec with a printed warning instead of crashing.

Loopback by default (genericity:T008): the rendered compose publishes on
`127.0.0.1:{port}` — the SGLang endpoint is unauthenticated, so a LAN/public
bind hands any peer on the network unauthenticated model access. Pass
`--expose-lan` (or `--bind <addr>`) to opt in to `0.0.0.0`; both print a
security warning pointing at SECURITY.md (CLAUDE.md gotcha #1).

Full artifact set (genericity:T009): one `deploy` invocation also appends a
`[[serve]]` entry to a serves manifest (`--manifest-out`, default
`./serves.toml`) and prints a `[[router.tiers]]` stub — both agreeing with the
compose file on served-name and port, so wiring a new local tier into the
router + `serves` lifecycle verb never drifts from what was actually deployed.
"""
import ipaddress
import os
import subprocess
import sys
import argparse

from . import gpus as _gpus
from . import serves as _serves

TEMPLATE = os.path.join(os.path.dirname(__file__), "..", "templates", "docker-compose.yml.tmpl")

LOOPBACK_BIND = "127.0.0.1"
LAN_BIND = "0.0.0.0"


def _is_loopback_bind(bind):
    """True if `bind` is a confirmed loopback address; non-numeric hostnames
    and wildcard binds ("", "0.0.0.0", "::") are treated as NOT loopback."""
    if bind in ("", LAN_BIND, "::"):
        return False
    try:
        return ipaddress.ip_address(bind).is_loopback
    except ValueError:
        return False


def _warn_if_public_bind(bind):
    if _is_loopback_bind(bind):
        return
    print(
        f"\n[anvil-serving] WARNING: publishing on {bind!r} exposes the "
        f"unauthenticated model endpoint on the network — any peer can send "
        f"inference requests and consume your GPU.\n"
        f"  Keep the default (127.0.0.1) unless you have placed your own "
        f"authentication/network controls in front of it. See SECURITY.md.\n",
        file=sys.stderr,
        flush=True,
    )


def _env_block(uuid):
    """Compose `environment:` block pinning by UUID, or "" when unresolved."""
    if not uuid:
        return ""
    return (
        "    environment:\n"
        "      CUDA_DEVICE_ORDER: PCI_BUS_ID\n"
        f"      CUDA_VISIBLE_DEVICES: {uuid}\n"
    )


def render(model_path, gpu=0, context=131072, served_name="local-specialist",
           kv_dtype="fp8_e5m2", max_running=16, mem_fraction=0.88, image="lmsysorg/sglang:latest",
           reasoning_parser="qwen3", tool_call_parser="qwen3_coder", language_only=True, port=30000,
           bind=LOOPBACK_BIND, _run=subprocess.check_output):
    tmpl = open(TEMPLATE, encoding="utf-8").read() if os.path.isfile(TEMPLATE) else _FALLBACK
    extra = []
    if reasoning_parser: extra.append(f"      --reasoning-parser {reasoning_parser}")
    if tool_call_parser: extra.append(f"      --tool-call-parser {tool_call_parser}")
    if language_only:    extra.append("      --language-only")
    uuid, warning = _gpus.resolve_gpu(gpu, _run=_run)
    if warning:
        print(f"[anvil-serving] WARNING: {warning}", file=sys.stderr)
    _warn_if_public_bind(bind)
    device_id = uuid or str(gpu)
    return tmpl.format(image=image, port=port, model=model_path, bind=bind,
                       kv=kv_dtype, ctx=context, maxrun=max_running, memfrac=mem_fraction,
                       served=served_name, extra_flags="\n".join(extra),
                       env_block=_env_block(uuid), device_id=device_id)

_FALLBACK = """services:
  sglang:
    image: {image}
    container_name: sglang
    restart: unless-stopped
    shm_size: "16g"
    ports: ["{bind}:{port}:{port}"]
    volumes: ["{model}:/models/local"]
{env_block}    deploy: {{resources: {{reservations: {{devices: [{{driver: nvidia, device_ids: ["{device_id}"], capabilities: [gpu]}}]}}}}}}
    command: >
      python3 -m sglang.launch_server
      --model-path /models/local
      --weight-loader-disable-mmap
      --kv-cache-dtype {kv}
{extra_flags}
      --context-length {ctx}
      --max-running-requests {maxrun}
      --mem-fraction-static {memfrac}
      --enable-metrics
      --served-model-name {served}
      --host 0.0.0.0 --port {port}
"""

def render_serve_entry(name, container, port, served_name, up, health="/health"):
    """A `[[serve]]` TOML block for the `anvil-serving serves` manifest —
    container/port/model MUST agree with the compose just rendered (T009 AC)."""
    return (
        f'\n[[serve]]\n'
        f'name = "{name}"\n'
        f'container = "{container}"\n'
        f'port = {port}\n'
        f'model = "{served_name}"\n'
        f'health = "{health}"\n'
        f'up = "{up}"\n'
    )


def append_serve_entry(manifest_path, name, container, port, served_name, up, health="/health"):
    """Append a `[[serve]]` block to `manifest_path`, creating it (with a
    header comment) if absent. A repeated `deploy` for the same `name` does
    NOT duplicate the block — it prints a note and leaves the manifest alone
    (edit it by hand to update an existing entry)."""
    entry = render_serve_entry(name, container, port, served_name, up, health)
    if os.path.isfile(manifest_path):
        try:
            existing = _serves.load_manifest(manifest_path)
        except Exception:
            existing = []
        if any(s.get("name") == name for s in existing):
            print(
                f"[anvil-serving] serve {name!r} already present in "
                f"{manifest_path}; not duplicating (edit it by hand to update).",
                file=sys.stderr,
            )
            return False
        with open(manifest_path, "a", encoding="utf-8") as f:
            f.write(entry)
    else:
        header = (
            "# Declarative serves manifest — see `anvil-serving serves --help`.\n"
            "# Generated by `anvil-serving deploy`; entries can also be added by hand.\n"
        )
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write(header + entry)
    return True


def render_tier_stub(tier_id, served_name, port, dialect="openai", context_limit=131072,
                      privacy="local", tool_support=True, auth_env=None):
    """A `[[router.tiers]]` TOML stub for `configs/*.toml` — `model` MUST equal
    the serve's `--served-model-name` and the port MUST equal the compose's
    published port (T009 AC), so pasting this in never 404s (genericity:R001)."""
    auth_env = auth_env or ("ANVIL_" + tier_id.upper().replace("-", "_") + "_KEY")
    return (
        f'\n[[router.tiers]]\n'
        f'id            = "{tier_id}"\n'
        f'base_url      = "http://127.0.0.1:{port}/v1"\n'
        f'model         = "{served_name}"\n'
        f'dialect       = "{dialect}"\n'
        f'context_limit = {context_limit}\n'
        f'privacy       = "{privacy}"\n'
        f'tool_support  = {"true" if tool_support else "false"}\n'
        f'auth_env      = "{auth_env}"\n'
    )


def main(argv):
    ap = argparse.ArgumentParser(prog="anvil-serving deploy")
    ap.add_argument("--model", required=True, help="local model dir mounted into the container")
    ap.add_argument("--gpu", default="0", help="GPU index (e.g. 1) or GPU-UUID to pin the serve to")
    ap.add_argument("--context", type=int, default=131072)
    ap.add_argument("--served-name", default="local-specialist")
    ap.add_argument("--port", type=int, default=30000)
    ap.add_argument("--out", default="docker-compose.yml")
    ap.add_argument("--bind", default=None,
                    help="publish address (default 127.0.0.1; loopback-only). "
                         "Pass 0.0.0.0 (or --expose-lan) to LAN-expose the "
                         "unauthenticated endpoint — see SECURITY.md.")
    ap.add_argument("--expose-lan", action="store_true",
                    help="shorthand for --bind 0.0.0.0")
    ap.add_argument("--tier-id", default=None,
                    help="serves-manifest name / router-tier id (default: --served-name)")
    ap.add_argument("--manifest-out", default="./serves.toml",
                    help="serves manifest to append a [[serve]] entry to "
                         "(default: %(default)s; run `anvil-serving serves "
                         "status` afterward to see it)")
    ap.add_argument("--no-manifest", action="store_true",
                    help="skip appending to the serves manifest / printing the router-tier stub")
    a = ap.parse_args(argv)
    bind = a.bind or (LAN_BIND if a.expose_lan else LOOPBACK_BIND)
    open(a.out, "w", encoding="utf-8").write(
        render(a.model, a.gpu, a.context, a.served_name, port=a.port, bind=bind))
    print("wrote", a.out, "\nLaunch:  docker compose -f", a.out, "up -d")

    if a.no_manifest:
        return 0

    tier_id = a.tier_id or a.served_name
    service = "sglang"
    # forward-slash the compose path: it's spliced into a TOML basic string
    # (backslash-escape rules apply) and then shlex-split (which ALSO treats
    # backslash as an escape char) — a raw Windows path would corrupt both.
    compose_path = a.out.replace(os.sep, "/")
    up = f"docker compose -f {compose_path} up -d {service}"
    if append_serve_entry(a.manifest_out, tier_id, service, a.port, a.served_name, up):
        print(f"appended [[serve]] {tier_id!r} to {a.manifest_out}")

    print(
        "\nRouter tier stub (paste into [router.tiers] in your config):\n"
        + render_tier_stub(tier_id, a.served_name, a.port, context_limit=a.context)
    )
    return 0

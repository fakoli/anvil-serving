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

vLLM engine (genericity:T010): `--engine vllm` renders a vLLM compose
(`ipc: host`, `VLLM_USE_V2_MODEL_RUNNER=0` — WSL2 exposes no UVA, CLAUDE.md
gotcha #14). Its serve argv is built by `multiplexer.build_cmd()` — the SAME
function that launches a vLLM backend for the multiplexer — so the two paths
can never drift apart. `--engine sglang` (the default) is unchanged.

Thinking-disable at generation time (genericity:T011): `--disable-thinking`
(or `--model-facts <card.json>` reporting `thinking_default: true`, as
written by `models sync`) injects the engine-appropriate
`--chat-template-kwargs '{"enable_thinking": false}'` into the serve command
— CLAUDE.md gotcha #6: a thinking-by-default model on a small `max_tokens`
budget otherwise burns it reasoning and returns EMPTY content.

Engine-enforced reservation budgets (gpu-reservations:T003, ADR-0017 §4):
`--gpu-role` + `--vram-mib` declare the serve's GPU residency reservation. When
the serves manifest (`--manifest-out`) declares a matching `[[gpu_roles]]`
capacity row, the engine's memory fraction is DERIVED from
`vram_mib / (capacity - reserve)` — `--gpu-memory-utilization` for vLLM,
`--mem-fraction-static` for SGLang — so the declared reservation is what the
engine actually respects, and the reservation fields are written into the
appended `[[serve]]` entry so `serves up` admission (T002) sees them. Serves
without a reservation render byte-for-byte unchanged.
"""
import ipaddress
import json
import os
import re
import shlex
import subprocess
import sys
import argparse
import tomllib

from . import gpus as _gpus
from . import multiplexer as _multiplexer
from . import reservations as _reservations
from . import serves as _serves

HERE = os.path.dirname(__file__)
TEMPLATE = os.path.join(HERE, "..", "templates", "docker-compose.yml.tmpl")
TEMPLATE_VLLM = os.path.join(HERE, "..", "templates", "docker-compose.vllm.yml.tmpl")

LOOPBACK_BIND = "127.0.0.1"
LAN_BIND = "0.0.0.0"
DEFAULT_IMAGE = {"sglang": "lmsysorg/sglang:latest", "vllm": "vllm/vllm-openai:latest"}

_SLUG_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _slug(name):
    return _SLUG_RE.sub("-", str(name)).strip("-") or "local"


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


def _env_block(uuid, extra=()):
    """Compose `environment:` block: CUDA pinning by UUID (if resolved) plus
    any engine-specific `extra` `"KEY: value"` lines. "" when both are empty."""
    lines = []
    if uuid:
        lines.append("CUDA_DEVICE_ORDER: PCI_BUS_ID")
        lines.append(f"CUDA_VISIBLE_DEVICES: {uuid}")
    lines.extend(extra)
    if not lines:
        return ""
    body = "\n".join("      " + line for line in lines)
    return "    environment:\n" + body + "\n"


def render(model_path, gpu=0, context=131072, served_name="local-specialist",
           kv_dtype="fp8_e5m2", max_running=16, mem_fraction=0.88, image=None,
           reasoning_parser="qwen3", tool_call_parser="qwen3_coder", language_only=True, port=30000,
           bind=LOOPBACK_BIND, engine="sglang", disable_thinking=False, gpu_mem_util=0.90,
           _run=subprocess.check_output):
    uuid, warning = _gpus.resolve_gpu(gpu, _run=_run)
    if warning:
        print(f"[anvil-serving] WARNING: {warning}", file=sys.stderr)
    _warn_if_public_bind(bind)
    device_id = uuid or str(gpu)

    if engine == "vllm":
        return _render_vllm(model_path, device_id, uuid, context, served_name,
                            image or DEFAULT_IMAGE["vllm"], port, bind,
                            disable_thinking, gpu_mem_util)
    return _render_sglang(model_path, device_id, uuid, context, served_name,
                          kv_dtype, max_running, mem_fraction,
                          image or DEFAULT_IMAGE["sglang"], reasoning_parser,
                          tool_call_parser, language_only, port, bind, disable_thinking)


def _render_sglang(model_path, device_id, uuid, context, served_name, kv_dtype,
                    max_running, mem_fraction, image, reasoning_parser,
                    tool_call_parser, language_only, port, bind, disable_thinking):
    tmpl = open(TEMPLATE, encoding="utf-8").read() if os.path.isfile(TEMPLATE) else _FALLBACK
    extra = []
    if reasoning_parser: extra.append(f"      --reasoning-parser {reasoning_parser}")
    if tool_call_parser: extra.append(f"      --tool-call-parser {tool_call_parser}")
    if language_only:    extra.append("      --language-only")
    if disable_thinking: extra.append(f"      {_thinking_disable_flag()}")
    return tmpl.format(image=image, port=port, model=model_path, bind=bind,
                       kv=kv_dtype, ctx=context, maxrun=max_running, memfrac=mem_fraction,
                       served=served_name, extra_flags="\n".join(extra),
                       env_block=_env_block(uuid), device_id=device_id)


def _thinking_disable_flag():
    """Engine-appropriate CLI flag disabling a thinking-by-default model at
    generation time (CLAUDE.md gotcha #6): on a small `max_tokens` budget it
    otherwise burns the budget reasoning and returns EMPTY content — a silent
    failure `verify.NonEmptyContent` exists specifically to catch. Both
    SGLang's and vLLM's OpenAI-compatible servers accept `--chat-template-kwargs`."""
    return "--chat-template-kwargs '{\"enable_thinking\": false}'"


def _render_vllm(model_path, device_id, uuid, context, served_name, image, port,
                  bind, disable_thinking, gpu_mem_util):
    tmpl = open(TEMPLATE_VLLM, encoding="utf-8").read() if os.path.isfile(TEMPLATE_VLLM) else _FALLBACK_VLLM
    args = ["--gpu-memory-utilization", str(gpu_mem_util), "--max-model-len", str(context),
            "--reasoning-parser", "qwen3", "--enable-auto-tool-choice",
            "--tool-call-parser", "qwen3_coder"]
    if disable_thinking:
        args += ["--chat-template-kwargs", '{"enable_thinking": false}']
    # SAME build_cmd() the multiplexer uses to launch a vLLM backend — one
    # engine-argv source, so `deploy` and `multiplexer` never drift (T010 AC).
    entry = {"name": served_name, "model_path": "/models/local", "port": port,
             "engine": "vllm", "args": args}
    argv = _multiplexer.build_cmd(entry)  # ["vllm", "serve", "/models/local", *args, *common]
    command_tokens = argv[1:]  # drop the entrypoint marker; the image's own ENTRYPOINT is vllm
    command = "\n      ".join(shlex.quote(t) for t in command_tokens)
    container = f"vllm-{_slug(served_name)}"
    env_block = _env_block(uuid, extra=['VLLM_USE_V2_MODEL_RUNNER: "0"'])
    return tmpl.format(image=image, container=container, port=port, model=model_path,
                       bind=bind, device_id=device_id, command=command, env_block=env_block)

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

_FALLBACK_VLLM = """services:
  vllm:
    image: {image}
    container_name: {container}
    restart: unless-stopped
    ipc: host
    ports: ["{bind}:{port}:{port}"]
    volumes: ["{model}:/models/local"]
{env_block}    deploy: {{resources: {{reservations: {{devices: [{{driver: nvidia, device_ids: ["{device_id}"], capabilities: [gpu]}}]}}}}}}
    command: >
      {command}
"""

def _toml_str(value):
    """A valid TOML basic string for `value`. `json.dumps` escaping is a strict
    subset of TOML basic-string syntax, so a name/path containing `"` or `\\`
    (a Windows `up` command line, say) can't corrupt the manifest."""
    return json.dumps(str(value))


def render_serve_entry(name, container, port, served_name, up, health="/health", engine="sglang",
                       gpu_role=None, vram_mib=None, residency=None):
    """A `[[serve]]` TOML block for the `anvil-serving serves` manifest —
    container/port/model MUST agree with the compose just rendered (T009 AC).

    Optional ADR-0017 reservation fields (`gpu_role`/`vram_mib`/`residency`)
    are emitted only when given, so `serves up` admission (T002) sees the same
    reservation the compose's engine budget was derived from — and a render
    without them keeps the exact pre-reservation block."""
    reservation = ""
    if gpu_role is not None:
        reservation += f'gpu_role = {_toml_str(gpu_role)}\n'
    if vram_mib is not None:
        reservation += f'vram_mib = {int(vram_mib)}\n'
    if residency is not None:
        reservation += f'residency = {_toml_str(residency)}\n'
    return (
        f'\n[[serve]]\n'
        f'name = {_toml_str(name)}\n'
        f'container = {_toml_str(container)}\n'
        f'port = {port}\n'
        f'model = {_toml_str(served_name)}\n'
        f'engine = {_toml_str(engine)}\n'
        f'health = {_toml_str(health)}\n'
        f'{reservation}'
        f'up = {_toml_str(up)}\n'
    )


def append_serve_entry(
    manifest_path, name, container, port, served_name, up, health="/health", engine="sglang",
    gpu_role=None, vram_mib=None, residency=None,
):
    """Append a `[[serve]]` block to `manifest_path`, creating it (with a
    header comment) if absent. A repeated `deploy` for the same `name` does
    NOT duplicate the block — it prints a note and leaves the manifest alone
    (edit it by hand to update an existing entry)."""
    entry = render_serve_entry(name, container, port, served_name, up, health, engine,
                               gpu_role=gpu_role, vram_mib=vram_mib, residency=residency)
    if os.path.isfile(manifest_path):
        try:
            existing = _serves.load_manifest(manifest_path)
        except Exception as e:
            # A manifest that no longer parses is already broken; appending
            # another entry to it just compounds the damage AND hides the
            # breakage. Skip and tell the operator to repair it first.
            print(
                f"[anvil-serving] {manifest_path} is unreadable "
                f"({type(e).__name__}: {e}); NOT appending — repair the "
                f"manifest first.",
                file=sys.stderr,
            )
            return False
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
            "# Generated by `anvil-serving serves render`; entries can also be added by hand.\n"
        )
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write(header + entry)
    return True


def render_tier_stub(tier_id, served_name, port, dialect="openai", context_limit=131072,
                      privacy="local", tool_support=True, auth_env=None,
                      disable_thinking=False, health_path="/health"):
    """A `[[router.tiers]]` TOML stub for `configs/*.toml` — `model` MUST equal
    the serve's `--served-model-name` and the port MUST equal the compose's
    published port (T009 AC), so pasting this in never 404s (genericity:R001).

    `disable_thinking=True` adds an advisory TOML comment (not a live field —
    `Tier` has no `extra_body` yet, tracked as genericity:R003) so an operator
    knows to set `chat_template_kwargs:{enable_thinking:false}` at the request
    layer too if the serve-side `--chat-template-kwargs` flag isn't enough."""
    auth_env = auth_env or ("ANVIL_" + tier_id.upper().replace("-", "_") + "_KEY")
    comment = (
        "# thinking-by-default model: the serve command already disables it "
        "(--chat-template-kwargs); once Tier grows `extra_body` (genericity:R003) "
        "also set chat_template_kwargs = {enable_thinking = false} here.\n"
        if disable_thinking else ""
    )
    return (
        f'\n{comment}[[router.tiers]]\n'
        f'id            = {_toml_str(tier_id)}\n'
        f'base_url      = "http://127.0.0.1:{port}/v1"\n'
        f'model         = {_toml_str(served_name)}\n'
        f'dialect       = {_toml_str(dialect)}\n'
        f'context_limit = {context_limit}\n'
        f'privacy       = {_toml_str(privacy)}\n'
        f'tool_support  = {"true" if tool_support else "false"}\n'
        f'auth_env      = {_toml_str(auth_env)}\n'
        f'health_path   = {_toml_str(health_path)}\n'
    )


def read_thinking_default(model_facts_path):
    """Read `thinking_default` from a `models sync` card JSON (T011), or
    False if `model_facts_path` is absent/unreadable/missing the key. Never
    raises — a missing/malformed facts file just means "don't disable"."""
    if not model_facts_path:
        return False
    try:
        with open(model_facts_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    return bool(data.get("thinking_default"))


def _infer_engine(model_path):
    """Best-effort default `--engine` from the model's on-disk `config.json`
    (T010 AC: "default inferable from the model's weight format"). NVFP4
    checkpoints are the vLLM-preferred quant on Blackwell (CLAUDE.md gotcha
    #10: served via FlashInfer CUTLASS NVFP4 kernels); everything else keeps
    the SGLang default this repo has always shipped. Never raises — a
    missing/unreadable config.json just keeps the sglang default."""
    try:
        with open(os.path.join(model_path, "config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return "sglang"
    quant = cfg.get("quantization_config") or {}
    qsig = " ".join(str(quant.get(k, "")) for k in ("quant_method", "format")).lower()
    return "vllm" if ("nvfp4" in qsig or "fp4" in qsig) else "sglang"


def _load_gpu_role_budgets(manifest_path):
    """The serves manifest's `[[gpu_roles]]` capacity rows (read-only lookup).

    Returns `{}` when the manifest doesn't exist yet (a first render has no
    capacity table to derive from). Raises when the manifest exists but its
    TOML or `[[gpu_roles]]` rows don't validate — deriving an engine budget
    against garbage capacity would be worse than failing loudly (same
    fail-at-parse philosophy as `reservations.parse_gpu_roles`)."""
    if not manifest_path or not os.path.isfile(manifest_path):
        return {}
    with open(manifest_path, "rb") as f:
        data = tomllib.load(f)
    return _reservations.parse_gpu_roles(data)


def main(argv, *, prog="anvil-serving serves render"):
    ap = argparse.ArgumentParser(prog=prog)
    ap.add_argument("--model", required=True, help="local model dir mounted into the container")
    ap.add_argument("--gpu", default="0", help="GPU index (e.g. 1) or GPU-UUID to pin the serve to")
    ap.add_argument("--context", type=int, default=131072)
    ap.add_argument("--served-name", default="local-specialist")
    ap.add_argument("--port", type=int, default=30000)
    ap.add_argument("--out", default="docker-compose.yml")
    ap.add_argument("--engine", choices=["sglang", "vllm"], default=None,
                    help="serving engine (default: inferred from the model's "
                         "config.json weight format, else sglang)")
    ap.add_argument("--gpu-mem-util", type=float, default=0.90,
                    help="--gpu-memory-utilization for the vLLM engine (ignored "
                         "for sglang); OVERRIDDEN by the value derived from a "
                         "declared reservation (--gpu-role/--vram-mib) when the "
                         "manifest declares that role's [[gpu_roles]] capacity")
    ap.add_argument("--gpu-role", default=None,
                    help="gpu_role of this serve's ADR-0017 VRAM reservation "
                         "(requires --vram-mib); when --manifest-out declares a "
                         "matching [[gpu_roles]] capacity row, the engine memory "
                         "fraction is derived from vram_mib / (capacity - reserve) "
                         "and the reservation is written into the [[serve]] entry")
    ap.add_argument("--vram-mib", type=int, default=None,
                    help="declared VRAM reservation of this serve in MiB "
                         "(requires --gpu-role)")
    ap.add_argument("--residency", choices=list(_serves._RESIDENCIES), default=None,
                    help="ADR-0017 residency of the reservation written to the "
                         "[[serve]] entry (resident: never evicted; evictable: may "
                         "be stopped to make room; on-demand: may evict evictable)")
    ap.add_argument("--disable-thinking", action="store_true",
                    help="inject the engine-appropriate flag to disable a "
                         "thinking-by-default model (CLAUDE.md gotcha #6: "
                         "otherwise it burns a small max_tokens budget "
                         "reasoning and returns EMPTY content); auto-set when "
                         "--model-facts reports thinking_default=true")
    ap.add_argument("--model-facts", default=None,
                    help="path to a `models sync` card JSON for this model "
                         "(reads thinking_default; see `anvil-serving models sync`)")
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
    engine = a.engine or _infer_engine(a.model)
    disable_thinking = a.disable_thinking or read_thinking_default(a.model_facts)

    # Engine-enforced reservation budget (gpu-reservations:T003, ADR-0017 §4):
    # a declared reservation whose gpu_role has a [[gpu_roles]] capacity row in
    # the manifest derives the engine memory fraction from
    # vram_mib / (capacity - reserve) — the engine then respects the ledger's
    # budget instead of a hand-tuned fraction. A role without a declared
    # capacity row stays underivable (same rule as the ledger: no budget row,
    # no participation), so the explicit/default fraction is kept with a
    # warning. No reservation flags -> everything below is a no-op.
    if (a.gpu_role is None) != (a.vram_mib is None):
        ap.error("--gpu-role and --vram-mib must be given together "
                 "(a reservation needs both; see ADR-0017)")
    if a.vram_mib is not None and a.vram_mib <= 0:
        ap.error("--vram-mib must be a positive integer (MiB)")
    derived_util = None
    if a.gpu_role is not None:
        try:
            budgets = _load_gpu_role_budgets(a.manifest_out)
        except (tomllib.TOMLDecodeError, ValueError) as e:
            print(f"[anvil-serving] ERROR: cannot read [[gpu_roles]] capacity "
                  f"from {a.manifest_out}: {e}", file=sys.stderr)
            return 2
        budget = budgets.get(a.gpu_role)
        if budget is None:
            print(f"[anvil-serving] WARNING: gpu_role {a.gpu_role!r} has no "
                  f"[[gpu_roles]] capacity row in {a.manifest_out}; engine "
                  f"memory fraction NOT derived (keeping "
                  f"--gpu-mem-util/defaults). Declare [[gpu_roles]] capacity "
                  f"to engine-enforce the reservation (ADR-0017).",
                  file=sys.stderr)
        else:
            try:
                derived_util = _reservations.derive_gpu_memory_utilization(
                    a.vram_mib, budget)
            except ValueError as e:
                print(f"[anvil-serving] ERROR: {e}", file=sys.stderr)
                return 2
            print(f"[anvil-serving] reservation: derived engine memory "
                  f"fraction {derived_util} = {a.vram_mib} MiB / "
                  f"({budget.vram_mib} - {budget.reserve_mib}) MiB "
                  f"for gpu_role {a.gpu_role!r}", file=sys.stderr)

    render_kwargs = {}
    if derived_util is not None:
        render_kwargs["mem_fraction"] = derived_util   # sglang --mem-fraction-static
    gpu_mem_util = derived_util if derived_util is not None else a.gpu_mem_util
    open(a.out, "w", encoding="utf-8").write(
        render(a.model, a.gpu, a.context, a.served_name, port=a.port, bind=bind,
              engine=engine, gpu_mem_util=gpu_mem_util,
              disable_thinking=disable_thinking, **render_kwargs))
    print("wrote", a.out, "\nLaunch:  docker compose -f", a.out, "up -d")

    if a.no_manifest:
        return 0

    tier_id = a.tier_id or a.served_name
    # the compose SERVICE key ("up -d <service>") vs. the docker CONTAINER
    # name (what `serves.py` docker-inspects/stops) differ for vllm: the
    # service key is fixed "vllm", but container_name is served-name-derived
    # (mirrors examples/fakoli-dark, e.g. container "vllm-gptoss").
    service = "vllm" if engine == "vllm" else "sglang"
    container = f"vllm-{_slug(a.served_name)}" if engine == "vllm" else "sglang"
    # forward-slash the compose path: it's spliced into a TOML basic string
    # (backslash-escape rules apply) and then shlex-split (which ALSO treats
    # backslash as an escape char) — a raw Windows path would corrupt both.
    compose_path = a.out.replace(os.sep, "/")
    up = f"docker compose -f {compose_path} up -d {service}"
    if append_serve_entry(
        a.manifest_out, tier_id, container, a.port, a.served_name, up, engine=engine,
        gpu_role=a.gpu_role, vram_mib=a.vram_mib, residency=a.residency,
    ):
        print(f"appended [[serve]] {tier_id!r} to {a.manifest_out}")

    print(
        "\nRouter tier stub (paste into [router.tiers] in your config):\n"
        + render_tier_stub(tier_id, a.served_name, a.port, context_limit=a.context,
                          disable_thinking=disable_thinking)
    )
    return 0

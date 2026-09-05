"""Validate and render portable tailnet endpoint bundles offline.

The renderer intentionally supports only a Docker Compose host/VM target.  It
does not contact a provider, enroll a Tailscale node, read secrets, or write the
rendered files.  Provider containers that cannot run Compose receive a typed
unsupported-target result instead of a misleading deployment plan.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA = "anvil-serving.edge-bundle/v1"
MAX_MANIFEST_BYTES = 256 * 1024
TAILNET_DNS_PLACEHOLDER = "REPLACE-WITH-TAILNET-DNS-NAME"

_SLUG = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_HOSTNAME = re.compile(r"^[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_TAG = re.compile(r"^tag:[a-z][a-z0-9-]{0,62}$")
_ALIAS = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
_IMAGE_COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
_IMAGE = re.compile(
    rf"^(?P<repository>{_IMAGE_COMPONENT}(?:/{_IMAGE_COMPONENT})*)"
    r"(?::[A-Za-z0-9_][A-Za-z0-9._-]{0,127})?"
    r"@sha256:[0-9a-f]{64}$"
)
_REGISTRY_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*\Z")
_VOLUME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_MODEL_SEGMENT = r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,94}[A-Za-z0-9_])?"
_MODEL = re.compile(rf"^{_MODEL_SEGMENT}/{_MODEL_SEGMENT}$")
_REVISION = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_CACHE_MOUNT = "/root/.cache/huggingface"
_PARSER_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class EdgeBundleError(ValueError):
    """The portable endpoint manifest is invalid."""


class UnsupportedTargetError(EdgeBundleError):
    """The manifest names a deployment shape this renderer cannot make safe."""

    def __init__(self, target: str) -> None:
        self.target = target
        super().__init__(
            f"target {target!r} is unsupported: use a Compose-capable VM/host; "
            "standard Vast.ai rentals are single containers without nested Docker"
        )


def _closed_mapping(value: object, *, name: str, fields: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EdgeBundleError(f"{name} must be an object")
    unknown = set(value) - fields
    if unknown:
        raise EdgeBundleError(f"unknown {name} fields")
    missing = fields - set(value)
    if missing:
        raise EdgeBundleError(f"missing {name} fields: {', '.join(sorted(missing))}")
    return value


def _matched(value: object, pattern: re.Pattern[str], *, name: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise EdgeBundleError(f"{name} is invalid")
    return value


def _text(value: object, *, name: str, maximum: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(char) < 32 for char in value)
    ):
        raise EdgeBundleError(f"{name} must contain 1-{maximum} printable characters")
    return value


def _image(value: object, *, name: str) -> str:
    """Pinned Docker repository reference; registry ports/IPv6 are unsupported."""
    if not isinstance(value, str) or len(value) > 456:
        raise EdgeBundleError(f"{name} is invalid")
    match = _IMAGE.fullmatch(value)
    if match is None or len(match["repository"]) > 255:
        raise EdgeBundleError(f"{name} is invalid")
    repository = match["repository"]
    first = repository.split("/", 1)[0]
    if "/" in repository and "." in first and not _REGISTRY_HOST.fullmatch(first):
        raise EdgeBundleError(f"{name} is invalid")
    return value


def _integer(value: object, *, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise EdgeBundleError(f"{name} must be an integer from {minimum} to {maximum}")
    return value


def _extra_args(value: object) -> tuple[str, ...]:
    """Allow only explicit adapter tuning flags; no config files or aliases."""
    if not isinstance(value, list) or len(value) > 32 or any(not isinstance(v, str) for v in value):
        raise EdgeBundleError("inference.extra_args must be an array of at most 32 strings")
    switches = {"--enable-auto-tool-choice", "--enable-request-id-headers"}
    values = {"--tensor-parallel-size", "--gpu-memory-utilization", "--dtype", "--tool-call-parser"}
    seen = set()
    index = 0
    while index < len(value):
        flag = value[index]
        if flag not in switches | values or flag in seen:
            raise EdgeBundleError("inference.extra_args contains an unsupported or duplicate flag")
        seen.add(flag)
        index += 1
        if flag in switches:
            continue
        if index == len(value):
            raise EdgeBundleError("inference.extra_args flag requires a value")
        argument = value[index]
        index += 1
        valid = False
        if flag == "--tensor-parallel-size":
            valid = bool(re.fullmatch(r"[0-9]{1,2}", argument)) and 1 <= int(argument) <= 16
        elif flag == "--gpu-memory-utilization":
            valid = bool(re.fullmatch(r"0\.[0-9]{1,4}|1(?:\.0{1,4})?", argument)) and 0 < float(argument) <= 1
        elif flag == "--dtype":
            valid = argument in {"auto", "half", "float16", "bfloat16", "float", "float32"}
        elif flag == "--tool-call-parser":
            valid = bool(_PARSER_NAME.fullmatch(argument))
        if not valid:
            raise EdgeBundleError("inference.extra_args has an invalid tuning value")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class TailnetConfig:
    image: str
    hostname: str
    auth_key_env: str
    tag: str
    state_volume: str


@dataclass(frozen=True, slots=True)
class InferenceConfig:
    image: str
    runtime: str
    extra_args: tuple[str, ...]
    port: int
    served_model: str
    model_revision: str
    api_key_env: str
    api_key_container_env: str
    cache_volume: str
    cache_mount: str
    gpu_count: int


@dataclass(frozen=True, slots=True)
class RouterConfig:
    tier_id: str
    alias: str
    context_limit: int
    max_output_tokens: int
    tool_support: bool


@dataclass(frozen=True, slots=True)
class EdgeBundle:
    name: str
    tailnet: TailnetConfig
    inference: InferenceConfig
    router: RouterConfig
    schema: str = SCHEMA
    target: str = "compose"

    @classmethod
    def from_mapping(cls, value: object) -> "EdgeBundle":
        root = _closed_mapping(
            value,
            name="manifest",
            fields={"schema", "target", "name", "tailnet", "inference", "router"},
        )
        if root["schema"] != SCHEMA:
            raise EdgeBundleError(f"schema must be {SCHEMA!r}")
        target = _text(root["target"], name="target", maximum=32)
        if target == "vast-container":
            raise UnsupportedTargetError(target)
        if target != "compose":
            raise EdgeBundleError("target must be 'compose'")

        tailnet = _closed_mapping(
            root["tailnet"],
            name="tailnet",
            fields={"image", "hostname", "auth_key_env", "tag", "state_volume"},
        )
        inference = _closed_mapping(
            root["inference"],
            name="inference",
            fields={
                "image",
                "runtime",
                "extra_args",
                "port",
                "served_model",
                "model_revision",
                "api_key_env",
                "api_key_container_env",
                "cache_volume",
                "cache_mount",
                "gpu_count",
            },
        )
        router = _closed_mapping(
            root["router"],
            name="router",
            fields={"tier_id", "alias", "context_limit", "max_output_tokens", "tool_support"},
        )

        if inference["runtime"] != "vllm-openai":
            raise EdgeBundleError("inference.runtime must be 'vllm-openai'")
        extra_args = _extra_args(inference["extra_args"])
        port = _integer(inference["port"], name="inference.port", minimum=1, maximum=65535)
        served_model = _matched(
            inference["served_model"], _MODEL, name="inference.served_model"
        )
        if "--" in served_model or ".." in served_model or served_model.endswith(".git"):
            raise EdgeBundleError("inference.served_model is invalid")
        model_revision = _matched(
            inference["model_revision"], _REVISION, name="inference.model_revision"
        )
        cache_mount = inference["cache_mount"]
        if cache_mount != _CACHE_MOUNT:
            raise EdgeBundleError("inference.cache_mount must use the adapter-owned Hugging Face cache path")
        if inference["api_key_container_env"] != "VLLM_API_KEY":
            raise EdgeBundleError("inference.api_key_container_env must be VLLM_API_KEY")
        tool_support = router["tool_support"]
        if type(tool_support) is not bool:
            raise EdgeBundleError("router.tool_support must be a boolean")
        if tool_support and not {"--enable-auto-tool-choice", "--tool-call-parser"}.issubset(extra_args):
            raise EdgeBundleError("router.tool_support requires explicit auto-tool-choice and tool-call-parser flags")

        auth_key_env = _matched(
            tailnet["auth_key_env"], _ENV_NAME, name="tailnet.auth_key_env"
        )
        api_key_env = _matched(
            inference["api_key_env"], _ENV_NAME, name="inference.api_key_env"
        )
        state_volume = _matched(
            tailnet["state_volume"], _VOLUME, name="tailnet.state_volume"
        )
        cache_volume = _matched(
            inference["cache_volume"], _VOLUME, name="inference.cache_volume"
        )
        context_limit = _integer(
            router["context_limit"],
            name="router.context_limit",
            minimum=1,
            maximum=10_000_000,
        )
        max_output_tokens = _integer(
            router["max_output_tokens"],
            name="router.max_output_tokens",
            minimum=1,
            maximum=1_000_000,
        )
        if auth_key_env == api_key_env:
            raise EdgeBundleError("tailnet and inference credentials must use different env names")
        if state_volume == cache_volume:
            raise EdgeBundleError("tailnet state and model cache must use different volumes")
        if max_output_tokens > context_limit:
            raise EdgeBundleError("router.max_output_tokens must not exceed router.context_limit")
        gpu_count = _integer(inference["gpu_count"], name="inference.gpu_count", minimum=1, maximum=16)
        if "--tensor-parallel-size" in extra_args and int(extra_args[extra_args.index("--tensor-parallel-size") + 1]) > gpu_count:
            raise EdgeBundleError("tensor parallel size must not exceed the reserved GPU count")

        return cls(
            name=_matched(root["name"], _SLUG, name="name"),
            tailnet=TailnetConfig(
                image=_image(tailnet["image"], name="tailnet.image"),
                hostname=_matched(tailnet["hostname"], _HOSTNAME, name="tailnet.hostname"),
                auth_key_env=auth_key_env,
                tag=_matched(tailnet["tag"], _TAG, name="tailnet.tag"),
                state_volume=state_volume,
            ),
            inference=InferenceConfig(
                image=_image(inference["image"], name="inference.image"),
                runtime="vllm-openai",
                extra_args=extra_args,
                port=port,
                served_model=served_model,
                model_revision=model_revision,
                api_key_env=api_key_env,
                api_key_container_env=_matched(
                    inference["api_key_container_env"],
                    _ENV_NAME,
                    name="inference.api_key_container_env",
                ),
                cache_volume=cache_volume,
                cache_mount=cache_mount,
                gpu_count=gpu_count,
            ),
            router=RouterConfig(
                tier_id=_matched(router["tier_id"], _SLUG, name="router.tier_id"),
                alias=_matched(router["alias"], _ALIAS, name="router.alias"),
                context_limit=context_limit,
                max_output_tokens=max_output_tokens,
                tool_support=tool_support,
            ),
        )


def load_bundle(path: str | Path) -> EdgeBundle:
    """Load one bounded, strict JSON manifest without resolving any secrets."""

    target = Path(path)
    with target.open("rb") as handle:
        raw = handle.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise EdgeBundleError("manifest exceeds 256 KiB")
    try:
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_members)
    except (ValueError, UnicodeError, RecursionError):
        raise EdgeBundleError("manifest is not a valid bounded JSON document") from None
    return EdgeBundle.from_mapping(parsed)


def _unique_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise EdgeBundleError("manifest contains duplicate object members")
        result[key] = value
    return result


def _required_env(name: str) -> str:
    return "${" + name + ":?required}"


def _router_fragment(bundle: EdgeBundle) -> str:
    tier = bundle.router
    inference = bundle.inference
    quote = json.dumps
    return "\n".join(
        (
            "# Replace the synthetic DNS placeholder after the node joins the tailnet.",
            "[[router.tiers]]",
            f"id = {quote(tier.tier_id)}",
            f"base_url = {quote(f'https://{TAILNET_DNS_PLACEHOLDER}/v1')}",
            f"model = {quote(inference.served_model)}",
            'dialect = "openai"',
            f"context_limit = {tier.context_limit}",
            'privacy = "local"',
            f"tool_support = {str(tier.tool_support).lower()}",
            f"auth_env = {quote(inference.api_key_env)}",
            'health_path = "/v1/models"',
            "model_identity = true",
            f"max_output_tokens = {tier.max_output_tokens}",
            "",
            "[router.model_routes]",
            f"{quote(tier.alias)} = {quote(tier.tier_id)}",
            "",
        )
    )


def render_bundle(bundle: EdgeBundle) -> dict[str, Any]:
    """Return a deterministic, non-applying Compose/Tailscale/router preview."""

    tailnet = bundle.tailnet
    inference = bundle.inference
    inference_command = [
        "--model",
        inference.served_model,
        "--revision",
        inference.model_revision,
        "--tokenizer-revision",
        inference.model_revision,
        "--served-model-name",
        inference.served_model,
        "--host",
        "127.0.0.1",
        "--port",
        str(inference.port),
        "--max-model-len",
        str(bundle.router.context_limit),
        "--download-dir",
        inference.cache_mount,
        *inference.extra_args,
    ]
    serve_config = {
        "TCP": {"443": {"HTTPS": True}},
        "Web": {
            "${TS_CERT_DOMAIN}:443": {
                "Handlers": {
                    # Tailscale strips the matched mount before joining the proxy URL.
                    # Retaining /v1 on the target preserves /v1/models and /v1/chat/...
                    "/v1": {"Proxy": f"http://127.0.0.1:{inference.port}/v1"}
                }
            }
        },
    }
    compose = {
        "name": bundle.name,
        "services": {
            "tailscale": {
                "image": tailnet.image,
                "hostname": tailnet.hostname,
                "environment": {
                    "TS_AUTHKEY": _required_env(tailnet.auth_key_env),
                    "TS_AUTH_ONCE": "true",
                    "TS_EXTRA_ARGS": f"--advertise-tags={tailnet.tag}",
                    "TS_SERVE_CONFIG": "/config/serve.json",
                    "TS_STATE_DIR": "/var/lib/tailscale",
                    "TS_USERSPACE": "true",
                },
                "volumes": [
                    f"{tailnet.state_volume}:/var/lib/tailscale",
                    "./tailscale-config:/config:ro",
                ],
            },
            "inference": {
                "image": inference.image,
                "command": inference_command,
                "network_mode": "service:tailscale",
                "depends_on": {"tailscale": {"condition": "service_started"}},
                "environment": {
                    inference.api_key_container_env: _required_env(inference.api_key_env)
                },
                "volumes": [f"{inference.cache_volume}:{inference.cache_mount}"],
                "deploy": {
                    "resources": {
                        "reservations": {
                            "devices": [
                                {
                                    "driver": "nvidia",
                                    "count": inference.gpu_count,
                                    "capabilities": ["gpu"],
                                }
                            ]
                        }
                    }
                },
            },
        },
        "volumes": {
            tailnet.state_volume: {},
            inference.cache_volume: {},
        },
    }
    return {
        "ok": True,
        "status": "preview",
        "schema": "anvil-serving.edge-bundle-render/v1",
        "target": "compose",
        "provider_deployment_tested": False,
        "files": {
            "compose.json": compose,
            "tailscale-config/serve.json": serve_config,
            "router-tier.toml": _router_fragment(bundle),
        },
        "required_environment": [tailnet.auth_key_env, inference.api_key_env],
        "checks": {
            "public_ports_published": False,
            "funnel_enabled": False,
            "tailnet_state_persistent": True,
            "tailnet_auth_key_expected_ephemeral_and_tagged": True,
            "upstream_health": "/v1/models",
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anvil-serving edge bundle",
        description="Validate or render a portable tailnet endpoint bundle offline.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action, help_text in (
        ("validate", "Validate a strict endpoint manifest without network access."),
        ("render", "Render Compose, Tailscale Serve, and router fragments without applying."),
    ):
        command = subparsers.add_parser(action, help=help_text)
        command.add_argument("--manifest", required=True, help="Path to the JSON manifest.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        bundle = load_bundle(args.manifest)
        if args.action == "validate":
            result = {
                "ok": True,
                "status": "valid",
                "schema": SCHEMA,
                "target": bundle.target,
                "name": bundle.name,
            }
        else:
            result = render_bundle(bundle)
        print(json.dumps(result, sort_keys=True))
        return 0
    except UnsupportedTargetError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "status": "unsupported",
                    "error": {
                        "code": "unsupported-target",
                        "target": exc.target,
                        "message": str(exc),
                    },
                },
                sort_keys=True,
            )
        )
        return 3
    except EdgeBundleError as exc:
        print(json.dumps({"ok": False, "status": "invalid", "error": str(exc)}, sort_keys=True))
        return 2
    except (OSError, TypeError, ValueError):
        print(json.dumps({"ok": False, "status": "invalid", "error": "manifest could not be read or decoded"}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

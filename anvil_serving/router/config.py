"""Router/tier config schema + loader (stdlib-only).

Loads the ``[router]`` block of an anvil-serving TOML config into a frozen,
validated :class:`RouterConfig`. Every tier names an env-var for its auth
secret (``auth_env``); the secret literal is never stored here and is never
read at load time, so a config can be loaded with no secrets present.

Also loads the optional top-level ``[server]`` table (:func:`load_server_config`
-> :class:`ServerConfig`) — front-door token auth (ADR-0004). Same contract:
``[server].auth_env`` names an env var, never a secret literal; absent means
auth is off.
"""
from __future__ import annotations

import ipaddress
import json
import math
import os
import re
import sys
import tomllib
import urllib.parse
from dataclasses import dataclass, field
from functools import cached_property
from types import MappingProxyType
from typing import Any, Mapping, Optional


# Tier dialect + privacy enums as NAMED constants, defined once here so the bare
# string literals don't scatter across the router (backends, serve, policy). The
# VALID_* sets stay the validation source of truth, now built from these names.
DIALECT_OPENAI = "openai"
DIALECT_ANTHROPIC = "anthropic"
VALID_DIALECTS = {DIALECT_OPENAI, DIALECT_ANTHROPIC}

PRIVACY_LOCAL = "local"
VALID_PRIVACY = {PRIVACY_LOCAL}

METADATA_CONFIGURED = "configured"
METADATA_UPSTREAM = "upstream"
VALID_METADATA_SOURCES = {METADATA_CONFIGURED, METADATA_UPSTREAM}

CONTEXT_ADMISSION_ESTIMATE = "estimate"
CONTEXT_ADMISSION_UPSTREAM = "upstream"
VALID_CONTEXT_ADMISSION = {
    CONTEXT_ADMISSION_ESTIMATE,
    CONTEXT_ADMISSION_UPSTREAM,
}

# Purpose-model kinds (ADR-0017 §7 / gpu-reservations:T010): non-chat inference
# surfaces the front door routes by MODEL NAME (never through the chat
# intent/policy pipeline). Each kind maps to exactly one front-door endpoint.
PURPOSE_EMBEDDING = "embedding"
PURPOSE_RERANK = "rerank"
VALID_PURPOSE_KINDS = {PURPOSE_EMBEDDING, PURPOSE_RERANK}

# Audio gateway route purposes.  These routes are deliberately separate from
# both chat tiers and purpose models: they normalize a configured, operator-
# owned STT/TTS serve behind the router's authenticated /v1/audio/* surface.
AUDIO_STT = "stt"
AUDIO_TTS = "tts"
VALID_AUDIO_PURPOSES = {AUDIO_STT, AUDIO_TTS}

# An auth reference must be an ENV-VAR NAME, not a secret literal.
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Some credential literals are all-caps alphanumeric and so also fit the env-name
# charset (e.g. an AWS access key id ``AKIA…`` / ``ASIA…``). Reject those shapes
# explicitly as defense-in-depth so a pasted key id can't masquerade as a name.
_SECRET_SHAPED_RE = re.compile(r"^(AKIA|ASIA)[0-9A-Z]{16}$")


def _validate_auth_env(value: object, label: str, *, detailed: bool = True) -> None:
    """Raise :class:`ConfigError` unless ``value`` is a plausible env-var NAME.

    Shared by every ``auth_env`` field ([server], tier, purpose model, audio
    route): it must match the env-var name shape and must not itself look like
    a credential literal — never the secret, only a reference to where it
    lives. ``label`` identifies the field in the error message (e.g.
    ``"tier 'primary': auth_env"``); ``detailed`` toggles the longer
    "store a secret reference..." guidance some call sites include and others
    keep terse.
    """
    if not isinstance(value, str) or not _ENV_NAME_RE.fullmatch(value):
        suffix = "; store a secret reference, never the secret itself" if detailed else ""
        raise ConfigError(
            f"{label} must name an ENV VAR matching ^[A-Z][A-Z0-9_]*$ "
            f"(got {value!r}){suffix}"
        )
    if _SECRET_SHAPED_RE.fullmatch(value):
        suffix = "; store the env-var NAME, never the secret" if detailed else ""
        raise ConfigError(
            f"{label} {value!r} is shaped like a credential literal, "
            f"not an env-var name{suffix}"
        )


# Keep optional per-audio-route limits no larger than the front door's default
# body cap without importing front_door (which imports this module).
_MAX_AUDIO_GATEWAY_BYTES = 32 * 1024 * 1024

_REQUIRED_TIER_KEYS = (
    "id",
    "base_url",
    "dialect",
    "privacy",
    "tool_support",
    "auth_env",
)
_TIER_KEYS = frozenset({
    *_REQUIRED_TIER_KEYS,
    "context_limit",
    "model",
    "extra_body",
    "extra_body_defaults",
    "engine",
    "quantization",
    "params",
    "timeout",
    "max_concurrency",
    "max_output_tokens",
    "health_path",
    "model_identity",
    "metadata_source",
    "context_admission",
})
_ROUTER_KEYS = frozenset({
    "tiers",
    "model_routes",
    "exhaustion_status",
    "relay_timeout",
    "availability_probe_interval",
    "availability_probe_timeout",
    "availability_probe_max_bytes",
    "purpose_models",
    "audio_routes",
    "audio_max_input_bytes",
    "audio_max_output_bytes",
    "audio_max_text_chars",
    "audio_max_concurrency",
})


def _reject_unknown_keys(
    raw: Mapping[str, object], allowed: frozenset[str], label: str
) -> None:
    """Reject stale or misspelled schema fields instead of silently ignoring them."""
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError(
            f"{label} contains unknown field(s): {', '.join(unknown)}"
        )


def normalize_model_alias(value: str) -> str:
    """Return the wire-normalized spelling of a configured chat alias.

    The vocabulary is closed: the normalized result must be a key in
    ``RouterConfig.model_routes`` to be servable.
    """
    return str(value).strip().lower()


class ConfigError(ValueError):
    """Raised for any router-config validation failure."""


@dataclass(frozen=True)
class Tier:
    """A single serving endpoint the router may route to."""

    id: str
    base_url: str
    dialect: str
    context_limit: int
    privacy: str
    tool_support: bool
    auth_env: str  # NAME of the env var holding the secret, never the secret
    model: Optional[str] = None  # concrete provider model id (e.g. "claude-opus-4-20250514")
    # ``configured`` keeps ``model`` and ``context_limit`` authoritative in
    # router TOML. ``upstream`` delegates both values to the inference
    # service's bounded runtime metadata and keeps this dataclass's zero/None
    # placeholders out of admission and public discovery.
    metadata_source: str = METADATA_CONFIGURED
    # ``estimate`` keeps the router's stdlib-only conservative token estimate
    # as the text context gate. ``upstream`` is an explicit opt-in for an
    # exact-identity local inference service whose own tokenizer enforces the
    # declared context window. The selected tier remains direct-only; this
    # never permits fallback or substitution.
    context_admission: str = CONTEXT_ADMISSION_ESTIMATE
    # Optional inline-table of extra JSON-serialisable keys merged verbatim into the
    # upstream request body (genericity:T003) -- e.g. a local vLLM/SGLang server's
    # `chat_template_kwargs: {enable_thinking: false}` to defend against the
    # thinking-budget-starvation gotcha (CLAUDE.md gotcha #6/#9). Never overrides the
    # keys the router itself sets (model/messages/stream/...); it is applied via
    # ``body.update(extra_body)`` in backends/relay.py, so a key here CAN clobber a
    # router-set key if the operator explicitly configures it that way -- that is
    # intentional passthrough, not a bug. Kept ``hash=False`` because a dict is
    # unhashable; Tier is never used as a dict/set key.
    extra_body: Optional[Mapping[str, Any]] = field(default=None, hash=False)
    # Like ``extra_body`` but applied as a DEFAULT the request can override (via
    # ``body.setdefault``, not ``update``): e.g. a tier's ``reasoning_effort`` becomes a
    # default a caller can dial per request. A key present in BOTH -> ``extra_body`` (the
    # hard override) still wins. ``hash=False`` for the same reason as ``extra_body``.
    extra_body_defaults: Optional[Mapping[str, Any]] = field(default=None, hash=False)
    # ---- flexibility:T007 — additive, default-unset descriptive/tuning fields ----
    # None of these is REQUIRED (none appears in ``_REQUIRED_TIER_KEYS``); every
    # existing config parses unchanged with all four reading as ``None``.
    #
    # ``engine`` / ``quantization``: free-form descriptive labels for the serving
    # backend behind this tier (e.g. ``"vllm"``/``"sglang"``, ``"nvfp4"``/``"awq"``).
    # Advisory metadata only — the router does not route on them today; they document
    # what a tier is and give future tooling (fingerprinting, dashboards) a home.
    engine: Optional[str] = None
    quantization: Optional[str] = None
    # ``params``: an inline-table of arbitrary JSON-serialisable tuning knobs for
    # this tier. Distinct from ``extra_body`` (which is merged into the UPSTREAM
    # request body): ``params`` is descriptive tier metadata, NOT forwarded to the
    # provider. Kept ``hash=False`` for the same reason as ``extra_body`` above (a
    # dict is unhashable; Tier is never used as a dict/set key).
    params: Optional[Mapping[str, Any]] = field(default=None, hash=False)
    # ``timeout``: per-tier transport timeout in seconds. When set it OVERRIDES the
    # global ``RouterConfig.relay_timeout`` for THIS tier's backend (threaded
    # through ``serve.build_backends``); absent -> the tier uses the global default.
    timeout: Optional[float] = None
    # ---- flexibility:T009 (ADR-0010 Phase 3) — optional per-tier concurrency cap --
    # ``max_concurrency``: the maximum number of requests DISPATCHED to this tier
    # that may be in flight at once, enforced by a per-tier stdlib
    # ``threading.BoundedSemaphore`` around that tier's backend in
    # ``serve.RoutingBackend``. Absent -> None -> NO per-tier cap: only the
    # process-global front-door limiter applies, exactly as today. Sized (from
    # ``benchmark``) for a low-throughput specialized-engine tier that must not be
    # hit by more than N simultaneous requests; every OTHER tier is unaffected —
    # its dispatch stays bounded only by the global limiter. Additive and
    # default-unset (NOT in ``_REQUIRED_TIER_KEYS``), so existing configs parse
    # unchanged with it reading as ``None``.
    max_concurrency: Optional[int] = None
    # Optional per-tier completion ceiling. Requests above this value are
    # clamped before relay and receive client-visible warning headers. Absent
    # preserves caller/upstream behavior. This is a runtime safety envelope,
    # distinct from the full input+output ``context_limit``.
    max_output_tokens: Optional[int] = None
    # Optional readiness path on the same scheme/authority as ``base_url``.
    # When set on a local tier, the router probes it before dispatch and keeps
    # an unavailable container out of the candidate pool. Absent preserves the
    # pre-readiness behavior with no additional network call.
    health_path: Optional[str] = None
    # Opt-in exact identity readiness for promotion-managed local tiers.  The
    # expected name is the existing ``model`` field, keeping one source of truth.
    model_identity: bool = False


@dataclass(frozen=True)
class PurposeModel:
    """One purpose-model serve the front door routes by MODEL NAME (T010).

    ADR-0017 §7: embedding/reranker serves are ordinary ``[[serve]]`` entries;
    the front door grows ``/v1/embeddings`` (and ``/v1/rerank``) and routes them
    by the request's ``model`` field. A purpose model is deliberately NOT a
    :class:`Tier`: it never enters the chat intent/policy/fallback pipeline, has
    no work-class quality profile, and an unknown model name is a clean caller
    error — never a fallthrough to chat routing.

    ``auth_env`` follows the tier contract: it names an ENV VAR (never the
    secret) and is OPTIONAL — local vLLM/SGLang pooling serves usually need no
    auth. ``model`` is the serve's ``--served-model-name`` — the exact string
    callers send in the request ``model`` field.
    """

    id: str
    kind: str  # "embedding" | "rerank" (VALID_PURPOSE_KINDS)
    model: str  # served-model-name; the routing key for this surface
    base_url: str  # OpenAI-style base, e.g. "http://127.0.0.1:30005/v1"
    auth_env: Optional[str] = None  # NAME of the env var holding the secret
    timeout: Optional[float] = None  # per-model transport timeout override


@dataclass(frozen=True)
class AudioRoute:
    """One Dark-owned audio serve behind the normalized router gateway.

    Audio routes never enter the quality-profile chat pipeline and never have
    provider fallback.  ``purpose`` selects the fixed request/response
    normalization (multipart STT or raw-PCM TTS), while ``id`` permits an
    explicit operator-selected route without disclosing a host to callers.
    ``source_sample_rate`` is required for TTS because its raw PCM response has
    no self-describing container.
    """

    id: str
    purpose: str  # "stt" | "tts" (VALID_AUDIO_PURPOSES)
    model: str  # concrete upstream model name; never caller-selected
    base_url: str  # upstream OpenAI-style base, e.g. http://host.docker.internal:30010/v1
    source_sample_rate: Optional[int] = None  # required for TTS raw PCM16
    timeout: Optional[float] = None
    auth_env: Optional[str] = None  # optional upstream bearer env-var name
    default: bool = False


@dataclass(frozen=True)
class RouterConfig:
    """Validated direct-serving topology.

    ``model_routes`` is the complete chat model vocabulary.  Every normalized
    caller alias maps to exactly one configured local tier; there are no
    presets, inferred intent classes, cloud escalation, or fallback pools.
    """

    tiers: tuple[Tier, ...]
    model_routes: Mapping[str, str] = field(hash=False)
    exhaustion_status: int = 503
    # Transport timeout (seconds) used for local relay backends.
    relay_timeout: float = 20.0
    # Runtime readiness probe controls for local tiers that declare
    # ``health_path``. Results are cached for the interval; each probe is
    # individually bounded by the timeout. Both are additive config fields.
    availability_probe_interval: float = 5.0
    availability_probe_timeout: float = 1.0
    availability_probe_max_bytes: int = 64 * 1024
    # gpu-reservations:T010 (ADR-0017 §7) — purpose-model serves routed by model
    # name on /v1/embeddings and /v1/rerank. Additive and default-empty: an
    # absent [[router.purpose_models]] list leaves the front door exactly as
    # before (those endpoints 404).
    purpose_models: tuple["PurposeModel", ...] = ()
    # Optional request/response audio gateway.  An absent list leaves
    # /v1/audio/transcriptions and /v1/audio/speech unavailable, preserving the
    # existing router surface.  Audio stays operator-owned and has no cloud
    # fallback path.
    audio_routes: tuple["AudioRoute", ...] = ()
    # Decoded STT input and normalized TTS output caps.  The encoded JSON body
    # is also covered by the front-door-wide MAX_BODY_BYTES cap.
    audio_max_input_bytes: int = 4 * 1024 * 1024
    audio_max_output_bytes: int = 4 * 1024 * 1024
    audio_max_text_chars: int = 16 * 1024
    audio_max_concurrency: int = 4

    @cached_property
    def _tiers_by_id(self) -> Mapping[str, Tier]:
        """Lazy id -> Tier index for direct route resolution."""
        return MappingProxyType({t.id: t for t in self.tiers})

    def tier(self, tier_id: str) -> Tier:
        """Return the tier with ``tier_id`` or raise :class:`ConfigError`."""
        t = self._tiers_by_id.get(tier_id)
        if t is None:
            raise ConfigError(f"unknown tier id: {tier_id!r}")
        return t

    def route_tier(self, model: str) -> Optional[Tier]:
        """Resolve a caller model alias to its one configured local tier."""
        tier_id = self.model_routes.get(normalize_model_alias(model))
        return self._tiers_by_id.get(tier_id) if tier_id is not None else None


@dataclass(frozen=True)
class ServerConfig:
    """Optional ``[server]`` table: front-door token-auth configuration (ADR-0004).

    ``auth_env`` names the env var holding the bearer/``x-api-key`` token that
    incoming requests are compared against (constant-time, ``hmac.compare_digest``
    in :mod:`front_door`). **Absent -> auth is OFF**, identical to today's
    loopback-only default — full back-compat. The secret literal is NEVER
    stored here, only the env-var NAME, mirroring the ``Tier.auth_env``
    contract above.

    ``admission_state_path`` and ``decision_log_path`` are the opt-in ADR-0033
    durability sinks: persisted tier-quiesce intent restored at boot, and an
    append-only metadata-only JSONL of decision records. Both absent -> no
    file I/O, identical to the pre-ADR-0033 router.
    """

    auth_env: Optional[str] = None
    admission_state_path: Optional[str] = None
    decision_log_path: Optional[str] = None


_SERVER_KEYS = frozenset({"auth_env", "admission_state_path", "decision_log_path"})


def load_server_config(path: str) -> ServerConfig:
    """Load + validate the optional ``[server]`` table of the TOML config at ``path``.

    No ``[server]`` table, or one with no ``auth_env`` key, yields
    ``ServerConfig(auth_env=None)`` — auth OFF. Never reads ``os.environ``:
    only the env-var NAME shape is validated here (same rules as a tier's
    ``auth_env``), never the secret literal.
    """
    path = os.path.expanduser(path)
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except OSError as e:
        raise ConfigError(f"cannot read router config {path!r}: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in router config {path!r}: {e}") from e

    server = data.get("server")
    if server is None:
        return ServerConfig()
    if not isinstance(server, dict):
        raise ConfigError(f"[server] must be a table in {path}")
    _reject_unknown_keys(server, _SERVER_KEYS, "[server]")

    auth_env = server.get("auth_env")
    if auth_env is not None:
        _validate_auth_env(auth_env, "[server].auth_env")

    paths: dict[str, Optional[str]] = {}
    for key in ("admission_state_path", "decision_log_path"):
        value = server.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ConfigError(f"[server].{key} must be a non-empty file path")
        paths[key] = os.path.expanduser(value) if isinstance(value, str) else None

    return ServerConfig(
        auth_env=auth_env,
        admission_state_path=paths["admission_state_path"],
        decision_log_path=paths["decision_log_path"],
    )


def _parse_tier(raw: object) -> Tier:
    if not isinstance(raw, dict):
        raise ConfigError(f"tier entry must be a table, got {type(raw).__name__}")
    _reject_unknown_keys(raw, _TIER_KEYS, "tier entry")

    missing = [k for k in _REQUIRED_TIER_KEYS if k not in raw]
    if missing:
        tid = raw.get("id", "<no id>")
        raise ConfigError(f"tier {tid!r} missing required keys: {', '.join(missing)}")

    tid = raw["id"]
    if not isinstance(tid, str) or not tid:
        raise ConfigError(f"tier id must be a non-empty string, got {tid!r}")

    dialect = raw["dialect"]
    if not isinstance(dialect, str) or dialect not in VALID_DIALECTS:
        raise ConfigError(
            f"tier {tid!r}: dialect {dialect!r} not in {sorted(VALID_DIALECTS)}"
        )

    privacy = raw["privacy"]
    if not isinstance(privacy, str) or privacy not in VALID_PRIVACY:
        raise ConfigError(
            f"tier {tid!r}: privacy {privacy!r} not in {sorted(VALID_PRIVACY)}"
        )

    metadata_source = raw.get("metadata_source", METADATA_CONFIGURED)
    if metadata_source not in VALID_METADATA_SOURCES:
        raise ConfigError(
            f"tier {tid!r}: metadata_source {metadata_source!r} not in "
            f"{sorted(VALID_METADATA_SOURCES)}"
        )

    raw_context_limit = raw.get("context_limit")
    if metadata_source == METADATA_UPSTREAM:
        if "context_limit" in raw:
            raise ConfigError(
                f"tier {tid!r}: metadata_source='upstream' forbids "
                "context_limit; the inference service is authoritative"
            )
        context_limit = 0
    else:
        # bool is a subclass of int; reject it explicitly.
        if (
            isinstance(raw_context_limit, bool)
            or not isinstance(raw_context_limit, int)
            or raw_context_limit <= 0
        ):
            raise ConfigError(
                f"tier {tid!r}: context_limit must be a positive int when "
                f"metadata_source='configured', got {raw_context_limit!r}"
            )
        context_limit = raw_context_limit

    tool_support = raw["tool_support"]
    if not isinstance(tool_support, bool):
        raise ConfigError(
            f"tier {tid!r}: tool_support must be a bool, got {tool_support!r}"
        )

    base_url = raw["base_url"]
    if not isinstance(base_url, str) or not base_url.lower().startswith(
        ("http://", "https://")
    ):
        raise ConfigError(
            f"tier {tid!r}: base_url must be an http:// or https:// URL "
            f"(got {base_url!r}); file://, ftp://, and other schemes are "
            f"rejected to prevent SSRF and local-file access"
        )

    auth_env = raw["auth_env"]
    _validate_auth_env(auth_env, f"tier {tid!r}: auth_env")

    # Optional: concrete provider model id to forward upstream instead of the
    # routing token.  Absent or None -> fall back to request.model at dispatch time.
    tier_model = raw.get("model")
    if tier_model is not None and not isinstance(tier_model, str):
        raise ConfigError(
            f"tier {tid!r}: model must be a string or absent, got {tier_model!r}"
        )

    if metadata_source == METADATA_UPSTREAM and tier_model is not None:
        raise ConfigError(
            f"tier {tid!r}: metadata_source='upstream' forbids model; the "
            "inference service is authoritative"
        )

    # A configured local tier without an explicit served-model-name is a
    # footgun: the
    # caller alias is forwarded upstream as the model id, and vLLM/SGLang reject
    # an unknown model with HTTP 404.
    # Warn (non-fatal) at load so a misconfigured local tier is caught here, not
    # as a confusing per-request 404. (genericity:R001)
    if (
        privacy == PRIVACY_LOCAL
        and metadata_source == METADATA_CONFIGURED
        and tier_model is None
    ):
        print(
            f"[anvil-serving] WARNING: local tier {tid!r} has no `model` set; the "
            f"request's routing token will be forwarded upstream as the model id "
            f"and the serve will 404. Set model = \"<served-model-name>\" (the "
            f"serve's --served-model-name).",
            file=sys.stderr,
            flush=True,
        )

    # Optional: extra keys merged verbatim into the upstream request body
    # (genericity:T003), e.g. a local server's thinking-disable knob. Absent ->
    # None (no-op; body is unchanged, matching today's behaviour exactly).
    def _parse_body(raw_val: object, field_name: str) -> Optional[Mapping[str, Any]]:
        if raw_val is None:
            return None
        if not isinstance(raw_val, dict):
            raise ConfigError(
                f"tier {tid!r}: {field_name} must be a table (inline dict), got "
                f"{type(raw_val).__name__}"
            )
        try:
            json.dumps(raw_val)
        except (TypeError, ValueError) as e:
            raise ConfigError(
                f"tier {tid!r}: {field_name} must be JSON-serialisable: {e}"
            ) from e
        return MappingProxyType(dict(raw_val))

    extra_body = _parse_body(raw.get("extra_body"), "extra_body")
    extra_body_defaults = _parse_body(raw.get("extra_body_defaults"), "extra_body_defaults")

    # Optional (flexibility:T007): additive, default-unset descriptive/tuning
    # fields. None is required, so an absent field reads as None and existing
    # configs parse unchanged.
    def _parse_str_field(raw_val: object, field_name: str) -> Optional[str]:
        if raw_val is None:
            return None
        if not isinstance(raw_val, str):
            raise ConfigError(
                f"tier {tid!r}: {field_name} must be a string or absent, got {raw_val!r}"
            )
        return raw_val

    engine = _parse_str_field(raw.get("engine"), "engine")
    quantization = _parse_str_field(raw.get("quantization"), "quantization")
    if metadata_source == METADATA_UPSTREAM and (
        engine is not None or quantization is not None
    ):
        raise ConfigError(
            f"tier {tid!r}: metadata_source='upstream' forbids engine and "
            "quantization; report only values observed from the inference service"
        )

    # ``params``: inline-table of JSON-serialisable tuning knobs (advisory tier
    # metadata; NOT forwarded upstream -- that is extra_body). Absent -> None.
    raw_params = raw.get("params")
    params: Optional[Mapping[str, Any]] = None
    if raw_params is not None:
        if not isinstance(raw_params, dict):
            raise ConfigError(
                f"tier {tid!r}: params must be a table (inline dict), got "
                f"{type(raw_params).__name__}"
            )
        try:
            json.dumps(raw_params)
        except (TypeError, ValueError) as e:
            raise ConfigError(
                f"tier {tid!r}: params must be JSON-serialisable: {e}"
            ) from e
        params = MappingProxyType(dict(raw_params))
        if (
            metadata_source == METADATA_UPSTREAM
            and raw_params.get("fingerprint") is not None
        ):
            raise ConfigError(
                f"tier {tid!r}: metadata_source='upstream' forbids "
                "params.fingerprint; report only identity observed from the "
                "inference service"
            )

    # ``timeout``: per-tier transport timeout (seconds). Overrides the global
    # relay_timeout for this tier's backend when set. bool is an int subclass --
    # reject it explicitly; must be > 0. Absent -> None (use the global default).
    raw_timeout = raw.get("timeout")
    tier_timeout: Optional[float] = None
    if raw_timeout is not None:
        if (
            isinstance(raw_timeout, bool)
            or not isinstance(raw_timeout, (int, float))
            or raw_timeout <= 0
            or not math.isfinite(raw_timeout)
        ):
            raise ConfigError(
                f"tier {tid!r}: timeout must be a positive number of seconds "
                f"or absent, got {raw_timeout!r}"
            )
        tier_timeout = float(raw_timeout)

    # ``max_concurrency`` (flexibility:T009): per-tier cap on concurrent in-flight
    # requests to this tier. bool is an int subclass -- reject it explicitly; must
    # be a positive int. Absent -> None (no per-tier cap; the process-global
    # front-door limiter is unchanged).
    raw_max_concurrency = raw.get("max_concurrency")
    tier_max_concurrency: Optional[int] = None
    if raw_max_concurrency is not None:
        if (
            isinstance(raw_max_concurrency, bool)
            or not isinstance(raw_max_concurrency, int)
            or raw_max_concurrency <= 0
        ):
            raise ConfigError(
                f"tier {tid!r}: max_concurrency must be a positive integer "
                f"or absent, got {raw_max_concurrency!r}"
            )
        tier_max_concurrency = raw_max_concurrency

    raw_max_output_tokens = raw.get("max_output_tokens")
    tier_max_output_tokens: Optional[int] = None
    if raw_max_output_tokens is not None:
        if (
            isinstance(raw_max_output_tokens, bool)
            or not isinstance(raw_max_output_tokens, int)
            or raw_max_output_tokens <= 0
            or (
                metadata_source == METADATA_CONFIGURED
                and raw_max_output_tokens > context_limit
            )
        ):
            raise ConfigError(
                f"tier {tid!r}: max_output_tokens must be a positive integer "
                f"no greater than the configured context_limit ({context_limit}) "
                f"when metadata_source='configured', or absent, "
                f"got {raw_max_output_tokens!r}"
            )
        tier_max_output_tokens = raw_max_output_tokens

    raw_health_path = raw.get("health_path")
    health_path: Optional[str] = None
    if raw_health_path is not None:
        if (
            not isinstance(raw_health_path, str)
            or not raw_health_path.startswith("/")
            or raw_health_path.startswith("//")
            or "?" in raw_health_path
            or "#" in raw_health_path
        ):
            raise ConfigError(
                f"tier {tid!r}: health_path must be an absolute URL path "
                f"without query/fragment or absent, got {raw_health_path!r}"
            )
        health_path = raw_health_path

    raw_model_identity = raw.get("model_identity", False)
    if not isinstance(raw_model_identity, bool):
        raise ConfigError(
            f"tier {tid!r}: model_identity must be a boolean (true/false)"
        )
    if raw_model_identity:
        if privacy != PRIVACY_LOCAL:
            raise ConfigError(
                f"tier {tid!r}: model_identity is supported only for local tiers"
            )
        if not isinstance(tier_model, str) or not tier_model.strip():
            raise ConfigError(
                f"tier {tid!r}: model_identity requires a non-empty model"
            )
        if health_path is None:
            raise ConfigError(
                f"tier {tid!r}: model_identity requires health_path"
            )
    if metadata_source == METADATA_UPSTREAM:
        if dialect != DIALECT_OPENAI:
            raise ConfigError(
                f"tier {tid!r}: metadata_source='upstream' currently requires "
                "dialect='openai'"
            )
        if health_path is None:
            raise ConfigError(
                f"tier {tid!r}: metadata_source='upstream' requires health_path"
            )
        if raw_model_identity:
            raise ConfigError(
                f"tier {tid!r}: metadata_source='upstream' cannot also set "
                "model_identity; runtime metadata replaces configured exact identity"
            )

    context_admission = raw.get(
        "context_admission", CONTEXT_ADMISSION_ESTIMATE
    )
    if context_admission not in VALID_CONTEXT_ADMISSION:
        raise ConfigError(
            f"tier {tid!r}: context_admission {context_admission!r} not in "
            f"{sorted(VALID_CONTEXT_ADMISSION)}"
        )
    if (
        context_admission == CONTEXT_ADMISSION_UPSTREAM
        and metadata_source != METADATA_UPSTREAM
        and not raw_model_identity
    ):
        raise ConfigError(
            f"tier {tid!r}: context_admission='upstream' requires either "
            "metadata_source='upstream' or model_identity=true"
        )

    return Tier(
        id=tid,
        base_url=base_url,
        dialect=dialect,
        context_limit=context_limit,
        privacy=privacy,
        tool_support=tool_support,
        auth_env=auth_env,
        model=tier_model or None,
        metadata_source=metadata_source,
        context_admission=context_admission,
        extra_body=extra_body,
        extra_body_defaults=extra_body_defaults,
        engine=engine,
        quantization=quantization,
        params=params,
        timeout=tier_timeout,
        max_concurrency=tier_max_concurrency,
        max_output_tokens=tier_max_output_tokens,
        health_path=health_path,
        model_identity=raw_model_identity,
    )


def _parse_purpose_model(raw: object) -> PurposeModel:
    """Parse + validate one ``[[router.purpose_models]]`` table (T010).

    Mirrors :func:`_parse_tier`'s validation stance: typed errors naming the
    entry, env-var-NAME-only auth references, http(s)-only base URLs.
    """
    if not isinstance(raw, dict):
        raise ConfigError(
            f"purpose_models entry must be a table, got {type(raw).__name__}"
        )

    pid = raw.get("id")
    if not isinstance(pid, str) or not pid:
        raise ConfigError(
            f"purpose model id must be a non-empty string, got {pid!r}"
        )

    kind = raw.get("kind")
    if not isinstance(kind, str) or kind not in VALID_PURPOSE_KINDS:
        raise ConfigError(
            f"purpose model {pid!r}: kind {kind!r} not in "
            f"{sorted(VALID_PURPOSE_KINDS)}"
        )

    model = raw.get("model")
    if not isinstance(model, str) or not model:
        raise ConfigError(
            f"purpose model {pid!r}: model must be a non-empty string (the "
            f"serve's --served-model-name), got {model!r}"
        )

    base_url = raw.get("base_url")
    if not isinstance(base_url, str) or not base_url.lower().startswith(
        ("http://", "https://")
    ):
        raise ConfigError(
            f"purpose model {pid!r}: base_url must be an http:// or https:// "
            f"URL (got {base_url!r}); file://, ftp://, and other schemes are "
            f"rejected to prevent SSRF and local-file access"
        )

    auth_env = raw.get("auth_env")
    if auth_env is not None:
        _validate_auth_env(auth_env, f"purpose model {pid!r}: auth_env")

    raw_timeout = raw.get("timeout")
    timeout: Optional[float] = None
    if raw_timeout is not None:
        if (
            isinstance(raw_timeout, bool)
            or not isinstance(raw_timeout, (int, float))
            or raw_timeout <= 0
            or not math.isfinite(raw_timeout)
        ):
            raise ConfigError(
                f"purpose model {pid!r}: timeout must be a positive number of "
                f"seconds or absent, got {raw_timeout!r}"
            )
        timeout = float(raw_timeout)

    return PurposeModel(
        id=pid,
        kind=kind,
        model=model,
        base_url=base_url,
        auth_env=auth_env,
        timeout=timeout,
    )


def _parse_audio_route(raw: object) -> AudioRoute:
    """Parse one ``[[router.audio_routes]]`` table.

    The table is intentionally small and declarative: callers address an
    audio purpose or route id, never a raw upstream URL or model name.  Audio
    serve lifecycle stays in ``anvil-serving voice``; this schema owns only
    ingress routing and contract normalization.
    """
    if not isinstance(raw, dict):
        raise ConfigError(
            f"audio_routes entry must be a table, got {type(raw).__name__}"
        )

    route_id = raw.get("id")
    if not isinstance(route_id, str) or not route_id:
        raise ConfigError(
            f"audio route id must be a non-empty string, got {route_id!r}"
        )

    purpose = raw.get("purpose")
    if not isinstance(purpose, str) or purpose not in VALID_AUDIO_PURPOSES:
        raise ConfigError(
            f"audio route {route_id!r}: purpose {purpose!r} not in "
            f"{sorted(VALID_AUDIO_PURPOSES)}"
        )

    model = raw.get("model")
    if not isinstance(model, str) or not model:
        raise ConfigError(
            f"audio route {route_id!r}: model must be a non-empty string "
            f"(the upstream served model name), got {model!r}"
        )

    base_url = raw.get("base_url")
    if not isinstance(base_url, str) or not base_url.lower().startswith(
        ("http://", "https://")
    ):
        raise ConfigError(
            f"audio route {route_id!r}: base_url must be an http:// or "
            f"https:// URL (got {base_url!r}); file://, ftp://, and other "
            "schemes are rejected to prevent SSRF and local-file access"
        )
    parsed_url = urllib.parse.urlparse(base_url)
    try:
        port = parsed_url.port
    except ValueError as exc:
        raise ConfigError(
            f"audio route {route_id!r}: base_url has an invalid port"
        ) from exc
    if port is not None and not (1 <= port <= 65535):
        raise ConfigError(
            f"audio route {route_id!r}: base_url port must be from 1 through 65535"
        )
    hostname = (parsed_url.hostname or "").lower()
    if (
        not hostname
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise ConfigError(
            f"audio route {route_id!r}: base_url must name a credential-free "
            "origin without query strings or fragments"
        )
    if hostname == "localhost":
        raise ConfigError(
            f"audio route {route_id!r}: base_url must use 127.0.0.1 or "
            "host.docker.internal, never localhost"
        )
    if hostname != "host.docker.internal":
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            raise ConfigError(
                f"audio route {route_id!r}: base_url host must be "
                "host.docker.internal or a literal private/tailnet IP address"
            ) from None
        allowed_networks = (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
            ipaddress.ip_network("100.64.0.0/10"),
        )
        if str(address) != "127.0.0.1" and not any(
            address in network for network in allowed_networks
        ):
            raise ConfigError(
                f"audio route {route_id!r}: base_url host must be 127.0.0.1, "
                "RFC1918, or tailnet; public, link-local, wildcard, and "
                "alternate loopback upstreams are not audio routes"
            )

    raw_timeout = raw.get("timeout")
    timeout: Optional[float] = None
    if raw_timeout is not None:
        if (
            isinstance(raw_timeout, bool)
            or not isinstance(raw_timeout, (int, float))
            or raw_timeout <= 0
            or not math.isfinite(raw_timeout)
        ):
            raise ConfigError(
                f"audio route {route_id!r}: timeout must be a positive number "
                f"of seconds or absent, got {raw_timeout!r}"
            )
        timeout = float(raw_timeout)

    source_sample_rate = raw.get("source_sample_rate")
    if purpose == AUDIO_TTS:
        if (
            isinstance(source_sample_rate, bool)
            or not isinstance(source_sample_rate, int)
            or not (8_000 <= source_sample_rate <= 192_000)
        ):
            raise ConfigError(
                f"audio route {route_id!r}: TTS source_sample_rate must be a "
                f"integer from 8000 through 192000, got {source_sample_rate!r}"
            )
    elif source_sample_rate is not None:
        raise ConfigError(
            f"audio route {route_id!r}: source_sample_rate is valid only for TTS"
        )

    auth_env = raw.get("auth_env")
    if auth_env is not None:
        _validate_auth_env(auth_env, f"audio route {route_id!r}: auth_env", detailed=False)

    default = raw.get("default", False)
    if not isinstance(default, bool):
        raise ConfigError(
            f"audio route {route_id!r}: default must be a boolean (true/false)"
        )

    return AudioRoute(
        id=route_id,
        purpose=purpose,
        model=model,
        base_url=base_url,
        source_sample_rate=source_sample_rate,
        timeout=timeout,
        auth_env=auth_env,
        default=default,
    )


def load(path: str) -> RouterConfig:
    """Load + validate the ``[router]`` block of the TOML config at ``path``.

    Never reads ``os.environ`` for a secret and never requires any secret to be
    set: it only records each tier's ``auth_env`` env-var NAME.
    """
    path = os.path.expanduser(path)
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except OSError as e:
        raise ConfigError(f"cannot read router config {path!r}: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in router config {path!r}: {e}") from e

    router = data.get("router")
    if not isinstance(router, dict):
        raise ConfigError(f"no [router] block in {path}")
    _reject_unknown_keys(router, _ROUTER_KEYS, "[router]")

    raw_tiers = router.get("tiers", [])
    if not isinstance(raw_tiers, list):
        raise ConfigError(f"[router].tiers must be a list of tables in {path}")

    tiers: list[Tier] = []
    seen_ids: set[str] = set()
    for raw in raw_tiers:
        tier = _parse_tier(raw)
        if tier.id in seen_ids:
            raise ConfigError(f"duplicate tier id: {tier.id!r}")
        seen_ids.add(tier.id)
        tiers.append(tier)

    if not tiers:
        raise ConfigError(f"[router].tiers is empty in {path}")

    # ``model_routes`` is the complete chat vocabulary.  Each alias selects
    # exactly one local tier; there is no inferred/preset fallback path.
    raw_model_routes = router.get("model_routes")
    if not isinstance(raw_model_routes, dict):
        raise ConfigError(
            f"[router].model_routes must be a non-empty table in {path}"
        )
    if not raw_model_routes:
        raise ConfigError(f"[router].model_routes must declare at least one alias in {path}")

    model_routes: dict[str, str] = {}
    seen_model_aliases: set[str] = set()
    tiers_by_id = {tier.id: tier for tier in tiers}

    def _route_items(table: Mapping[str, object], prefix: str = ""):
        for raw_alias, target in table.items():
            alias = f"{prefix}.{raw_alias}" if prefix else raw_alias
            if isinstance(target, dict):
                yield from _route_items(target, alias)
            else:
                yield alias, target

    # TOML parses an unquoted ``llm.primary = "tier"`` as nested tables.
    # Flatten it so both that natural spelling and a quoted literal key express
    # the same configured caller alias.
    for alias, tier_id in _route_items(raw_model_routes):
        if not isinstance(alias, str) or not normalize_model_alias(alias):
            raise ConfigError(
                f"model route alias must be a non-empty string, got {alias!r}"
            )
        normalized_alias = normalize_model_alias(alias)
        if normalized_alias in seen_model_aliases:
            raise ConfigError(
                f"duplicate model route alias (case-insensitive): {alias!r}"
            )
        if not isinstance(tier_id, str) or tier_id not in seen_ids:
            raise ConfigError(
                f"model route {alias!r} references unknown tier id: {tier_id!r}"
            )
        if tiers_by_id[tier_id].privacy != "local":
            raise ConfigError(
                f"model route {alias!r} must target a privacy='local' tier"
            )
        seen_model_aliases.add(normalized_alias)
        model_routes[normalized_alias] = tier_id

    unaddressable = sorted(seen_ids - set(model_routes.values()))
    if unaddressable:
        raise ConfigError(
            "every chat tier must be named by [router].model_routes; "
            f"unaddressable tiers: {unaddressable}"
        )

    # HTTP status returned when the selected direct tier is unavailable.
    # Default 503 lets an upstream client apply its own transport retry policy.
    raw_exhaustion_status = router.get("exhaustion_status", 503)
    if (
        isinstance(raw_exhaustion_status, bool)
        or not isinstance(raw_exhaustion_status, int)
        or not (100 <= raw_exhaustion_status <= 599)
    ):
        raise ConfigError(
            f"[router].exhaustion_status must be an HTTP status integer "
            f"(100-599, default 503) in {path}"
        )
    exhaustion_status: int = raw_exhaustion_status

    # Transport timeout in seconds used to build local relay backends. The
    # default stays short (20s) so a hung or cold serve fails promptly. bool is
    # a subclass of int, so reject it explicitly.
    raw_relay_timeout = router.get("relay_timeout", 20.0)
    if (
        isinstance(raw_relay_timeout, bool)
        or not isinstance(raw_relay_timeout, (int, float))
        or raw_relay_timeout <= 0
        or not math.isfinite(raw_relay_timeout)
    ):
        raise ConfigError(
            f"[router].relay_timeout must be a positive number of seconds "
            f"(default 20.0) in {path}"
        )
    relay_timeout: float = float(raw_relay_timeout)

    def _positive_seconds(key: str, default: float) -> float:
        raw_value = router.get(key, default)
        if (
            isinstance(raw_value, bool)
            or not isinstance(raw_value, (int, float))
            or raw_value <= 0
        ):
            raise ConfigError(
                f"[router].{key} must be a positive number of seconds "
                f"(default {default}) in {path}"
            )
        return float(raw_value)

    # ``purpose_models`` (gpu-reservations:T010 / ADR-0017 §7): non-chat
    # inference serves routed by model name on /v1/embeddings + /v1/rerank.
    # Absent -> empty -> those endpoints stay 404 (existing behaviour).
    raw_purpose = router.get("purpose_models", [])
    if not isinstance(raw_purpose, list):
        raise ConfigError(
            f"[router].purpose_models must be a list of tables in {path}"
        )
    purpose_models: list[PurposeModel] = []
    seen_purpose_ids: set[str] = set()
    seen_purpose_keys: set[tuple[str, str]] = set()
    for raw in raw_purpose:
        pm = _parse_purpose_model(raw)
        if pm.id in seen_purpose_ids or pm.id in seen_ids:
            raise ConfigError(
                f"duplicate purpose model id: {pm.id!r} (purpose model ids "
                f"share the audit-trail namespace with tier ids and must be "
                f"unique across both)"
            )
        # One serve per (kind, model): the model name is the routing key for a
        # purpose surface, so a duplicate would be ambiguous.
        key = (pm.kind, pm.model)
        if key in seen_purpose_keys:
            raise ConfigError(
                f"duplicate purpose model routing key: kind={pm.kind!r} "
                f"model={pm.model!r} (each {pm.kind} model name may map to "
                f"exactly one serve)"
            )
        seen_purpose_ids.add(pm.id)
        seen_purpose_keys.add(key)
        purpose_models.append(pm)

    # ``audio_routes``: optional Dark-owned STT/TTS routes behind the router's
    # normalized JSON /v1/audio/* gateway.  Unlike purpose models, callers
    # select a purpose (or explicit route id), not the upstream model.  Multiple
    # routes may share a purpose, but purpose-only selection must have exactly
    # one default route (a lone route is its own default).
    raw_audio = router.get("audio_routes", [])
    if not isinstance(raw_audio, list):
        raise ConfigError(
            f"[router].audio_routes must be a list of tables in {path}"
        )
    audio_routes: list[AudioRoute] = []
    seen_audio_ids: set[str] = set()
    audio_by_purpose: dict[str, list[AudioRoute]] = {}
    for raw in raw_audio:
        audio_route = _parse_audio_route(raw)
        if (
            audio_route.id in seen_ids
            or audio_route.id in seen_purpose_ids
            or audio_route.id in seen_audio_ids
        ):
            raise ConfigError(
                f"duplicate audio route id: {audio_route.id!r} (audio route ids "
                "share the audit-trail namespace with tiers and purpose models)"
            )
        seen_audio_ids.add(audio_route.id)
        audio_routes.append(audio_route)
        audio_by_purpose.setdefault(audio_route.purpose, []).append(audio_route)

    for audio_purpose, routes in audio_by_purpose.items():
        default_count = sum(1 for route in routes if route.default)
        if len(routes) > 1 and default_count != 1:
            raise ConfigError(
                f"audio purpose {audio_purpose!r} has {len(routes)} routes; "
                "exactly one must set default = true for purpose-only routing"
            )
        if len(routes) == 1 and default_count > 1:  # defensive, unreachable
            raise ConfigError(
                f"audio purpose {audio_purpose!r} has more than one default route"
            )

    def _audio_limit(key: str, default: int) -> int:
        value = router.get(key, default)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not (1024 <= value <= _MAX_AUDIO_GATEWAY_BYTES)
        ):
            raise ConfigError(
                f"[router].{key} must be an integer from 1024 through "
                f"{_MAX_AUDIO_GATEWAY_BYTES} in {path}"
            )
        return value

    audio_max_input_bytes = _audio_limit("audio_max_input_bytes", 4 * 1024 * 1024)
    audio_max_output_bytes = _audio_limit("audio_max_output_bytes", 4 * 1024 * 1024)
    audio_max_text_chars = _audio_limit("audio_max_text_chars", 16 * 1024)
    raw_audio_max_concurrency = router.get("audio_max_concurrency", 4)
    if (
        isinstance(raw_audio_max_concurrency, bool)
        or not isinstance(raw_audio_max_concurrency, int)
        or not (1 <= raw_audio_max_concurrency <= 16)
    ):
        raise ConfigError(
            f"[router].audio_max_concurrency must be an integer from 1 through 16 "
            f"in {path}"
        )

    availability_probe_interval = _positive_seconds(
        "availability_probe_interval", 5.0
    )
    availability_probe_timeout = _positive_seconds(
        "availability_probe_timeout", 1.0
    )
    raw_probe_max_bytes = router.get("availability_probe_max_bytes", 64 * 1024)
    if (
        isinstance(raw_probe_max_bytes, bool)
        or not isinstance(raw_probe_max_bytes, int)
        or not (256 <= raw_probe_max_bytes <= 1024 * 1024)
    ):
        raise ConfigError(
            f"[router].availability_probe_max_bytes must be an integer from "
            f"256 through 1048576 in {path}"
        )

    return RouterConfig(
        tiers=tuple(tiers),
        model_routes=MappingProxyType(model_routes),
        exhaustion_status=exhaustion_status,
        relay_timeout=relay_timeout,
        availability_probe_interval=availability_probe_interval,
        availability_probe_timeout=availability_probe_timeout,
        availability_probe_max_bytes=raw_probe_max_bytes,
        purpose_models=tuple(purpose_models),
        audio_routes=tuple(audio_routes),
        audio_max_input_bytes=audio_max_input_bytes,
        audio_max_output_bytes=audio_max_output_bytes,
        audio_max_text_chars=audio_max_text_chars,
        audio_max_concurrency=raw_audio_max_concurrency,
    )

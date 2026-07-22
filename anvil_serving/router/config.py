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
from dataclasses import dataclass, field, replace
from functools import cached_property
from types import MappingProxyType
from typing import Any, Mapping, Optional

from .prices import fetch_prices

# Tier dialect + privacy enums as NAMED constants, defined once here so the bare
# string literals don't scatter across the router (backends, serve, policy). The
# VALID_* sets stay the validation source of truth, now built from these names.
DIALECT_OPENAI = "openai"
DIALECT_ANTHROPIC = "anthropic"
VALID_DIALECTS = {DIALECT_OPENAI, DIALECT_ANTHROPIC}

PRIVACY_LOCAL = "local"
PRIVACY_CLOUD = "cloud"
VALID_PRIVACY = {PRIVACY_LOCAL, PRIVACY_CLOUD}

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
# Keep optional per-audio-route limits no larger than the front door's default
# body cap without importing front_door (which imports this module).
_MAX_AUDIO_GATEWAY_BYTES = 32 * 1024 * 1024

_REQUIRED_TIER_KEYS = (
    "id",
    "base_url",
    "dialect",
    "context_limit",
    "privacy",
    "tool_support",
    "auth_env",
)


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
    # Cost fields: USD per million tokens.  None = unknown / unset (e.g. all local tiers).
    # Set these on metered cloud tiers so cost_usd can be computed per-request.
    cost_input_per_mtok: Optional[float] = None
    cost_output_per_mtok: Optional[float] = None
    # Optional inline-table of extra JSON-serialisable keys merged verbatim into the
    # upstream request body (genericity:T003) -- e.g. a local vLLM/SGLang server's
    # `chat_template_kwargs: {enable_thinking: false}` to defend against the
    # thinking-budget-starvation gotcha (CLAUDE.md gotcha #6/#9). Never overrides the
    # keys the router itself sets (model/messages/stream/...); it is applied via
    # ``body.update(extra_body)`` in backends/cloud.py, so a key here CAN clobber a
    # router-set key if the operator explicitly configures it that way -- that is
    # intentional passthrough, not a bug. Kept ``hash=False`` (a dict is unhashable)
    # to match ``RouterConfig.presets`` below; Tier is never used as a dict/set key.
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
    """Validated router topology: tiers + preset->candidate mapping.

    ``metered_cloud`` lists the work-classes (R002 taxonomy keys) that are
    permitted to use a ``privacy == "cloud"`` tier.  **Default empty** — an
    absent or empty list means a cloud tier is NEVER a routing candidate,
    regardless of what preset pools include it (ADR-0001 / advise-and-defer:T002).
    The gate is enforced by :func:`~anvil_serving.router.policy.route`.

    ``exhaustion_status`` is the HTTP status returned when ALL quality-gated
    tiers are exhausted (no available tier).  Default 503 is the **keyless
    handoff signal** (ADR-0001 §Mechanism, advise-and-defer:T004): OpenClaw's
    transport failover classifies 503 as "overloaded" and re-runs the request
    on the native subscription provider — pending live validation in T005.
    Operators may override to match a different gateway's transport-failover
    trigger via ``[router].exhaustion_status``.
    """

    tiers: tuple[Tier, ...]
    presets: Mapping[str, tuple[str, ...]] = field(hash=False)
    mapping_version: str
    metered_cloud: tuple[str, ...] = ()
    exhaustion_status: int = 503
    # ADR-0001 / advise-and-defer:T006 — off by default (no network in default mode).
    # When True, tiers with unset cost fields have them filled from the LiteLLM pricing
    # JSON after loading; static config values always win (never overwritten).
    cost_sync: bool = False
    # genericity:T005 — transport timeout (seconds) used to build LOCAL-tier
    # (privacy="local") backends. Kept short by default: a local vLLM/SGLang serve
    # that has hung or gone cold should fail fast so the router escalates to the
    # next tier promptly, rather than sitting on the CloudBackend/RelayBackend
    # default of 120s (tuned for a slower cloud provider). Threaded through
    # serve.build_backends -> build_backend_for_tier. Does not affect cloud tiers.
    relay_timeout: float = 20.0
    # genericity:T004 — when True (default), a privacy="local" tier under an
    # "allow" profile verdict is NOT streamed as a raw zero-verifier passthrough;
    # it runs through a minimal commit-window (NonEmptyContent/NotTruncated) first,
    # so an empty/truncated local 200 escalates (or exhausts to
    # ``exhaustion_status``) instead of being served silently. A cloud/remote tier
    # under "allow" is never affected by this flag.
    verify_local_min: bool = True
    # Optional path to a measured quality profile (the ``profile.json`` written by
    # ``python -m anvil_serving.router.profile_bootstrap`` / ``eval bootstrap``).
    # When set, ``serve`` loads it at startup instead of the hand-authored seed
    # profile, so the router routes on YOUR measured verdicts. Absent (default)
    # keeps today's behaviour: the built-in seed profile. A configured-but-
    # unreadable path is a startup ConfigError (fail fast, never silently fall
    # back to seeds the operator asked to replace).
    profile_path: Optional[str] = None
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
    # Issue #180 / ADR-0026 — compatibility-preserving wire transparency.
    # Appended to preserve positional construction of older optional fields.
    # Default False keeps response.model equal to the caller's routing token;
    # True reports the tier id that actually served across every chat dialect.
    transparent_response_model: bool = False

    @cached_property
    def _tiers_by_id(self) -> Mapping[str, Tier]:
        """Lazy id -> Tier index. ``tier()`` runs several times per routed
        request (policy filters, verdict lookups, fallback attempts), so the
        linear scan over ``tiers`` is replaced with one dict build on first
        use. ``cached_property`` writes straight into ``__dict__``, which a
        frozen dataclass permits (no ``__slots__``)."""
        return MappingProxyType({t.id: t for t in self.tiers})

    def tier(self, tier_id: str) -> Tier:
        """Return the tier with ``tier_id`` or raise :class:`ConfigError`."""
        t = self._tiers_by_id.get(tier_id)
        if t is None:
            raise ConfigError(f"unknown tier id: {tier_id!r}")
        return t

    def candidates(self, preset: str) -> tuple[Tier, ...]:
        """Resolve a preset's ordered candidate tiers (raises if unknown)."""
        try:
            ids = self.presets[preset]
        except KeyError:
            raise ConfigError(f"unknown preset: {preset!r}") from None
        return tuple(self.tier(tid) for tid in ids)


@dataclass(frozen=True)
class ServerConfig:
    """Optional ``[server]`` table: front-door token-auth configuration (ADR-0004).

    ``auth_env`` names the env var holding the bearer/``x-api-key`` token that
    incoming requests are compared against (constant-time, ``hmac.compare_digest``
    in :mod:`front_door`). **Absent -> auth is OFF**, identical to today's
    loopback-only default — full back-compat. The secret literal is NEVER
    stored here, only the env-var NAME, mirroring the ``Tier.auth_env``
    contract above.
    """

    auth_env: Optional[str] = None


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
        return ServerConfig(auth_env=None)
    if not isinstance(server, dict):
        raise ConfigError(f"[server] must be a table in {path}")

    auth_env = server.get("auth_env")
    if auth_env is None:
        return ServerConfig(auth_env=None)

    if not isinstance(auth_env, str) or not _ENV_NAME_RE.fullmatch(auth_env):
        raise ConfigError(
            f"[server].auth_env must name an ENV VAR matching "
            f"^[A-Z][A-Z0-9_]*$ (got {auth_env!r}); store a secret reference, "
            f"never the secret itself"
        )
    if _SECRET_SHAPED_RE.fullmatch(auth_env):
        raise ConfigError(
            f"[server].auth_env {auth_env!r} is shaped like a credential "
            f"literal, not an env-var name; store the env-var NAME, never the secret"
        )

    return ServerConfig(auth_env=auth_env)


def _parse_tier(raw: object) -> Tier:
    if not isinstance(raw, dict):
        raise ConfigError(f"tier entry must be a table, got {type(raw).__name__}")

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

    context_limit = raw["context_limit"]
    # bool is a subclass of int; reject it explicitly.
    if isinstance(context_limit, bool) or not isinstance(context_limit, int) or context_limit <= 0:
        raise ConfigError(
            f"tier {tid!r}: context_limit must be a positive int, got {context_limit!r}"
        )

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
    if not isinstance(auth_env, str) or not _ENV_NAME_RE.fullmatch(auth_env):
        raise ConfigError(
            f"tier {tid!r}: auth_env must name an ENV VAR matching "
            f"^[A-Z][A-Z0-9_]*$ (got {auth_env!r}); store a secret reference, "
            f"never the secret itself"
        )
    if _SECRET_SHAPED_RE.fullmatch(auth_env):
        raise ConfigError(
            f"tier {tid!r}: auth_env {auth_env!r} is shaped like a credential "
            f"literal, not an env-var name; store the env-var NAME, never the secret"
        )

    # Optional: concrete provider model id to forward upstream instead of the
    # routing token.  Absent or None -> fall back to request.model at dispatch time.
    tier_model = raw.get("model")
    if tier_model is not None and not isinstance(tier_model, str):
        raise ConfigError(
            f"tier {tid!r}: model must be a string or absent, got {tier_model!r}"
        )

    # A local tier without an explicit served-model-name is a footgun: the
    # request's routing token (a preset like "quick-edit") is forwarded upstream
    # as the model id, and vLLM/SGLang reject an unknown model with HTTP 404.
    # Warn (non-fatal) at load so a misconfigured local tier is caught here, not
    # as a confusing per-request 404. (genericity:R001)
    if privacy == PRIVACY_LOCAL and tier_model is None:
        print(
            f"[anvil-serving] WARNING: local tier {tid!r} has no `model` set; the "
            f"request's routing token will be forwarded upstream as the model id "
            f"and the serve will 404. Set model = \"<served-model-name>\" (the "
            f"serve's --served-model-name).",
            file=sys.stderr,
            flush=True,
        )

    # Optional: cost per million tokens (USD) for metered cloud tiers.
    # Absent or None -> None (unknown, e.g. all local tiers).
    # A non-numeric or negative value is a config error.
    def _parse_cost_field(raw_val: object, field_name: str) -> Optional[float]:
        if raw_val is None:
            return None
        # bool is a subclass of int/float in Python; reject explicitly.
        if isinstance(raw_val, bool) or not isinstance(raw_val, (int, float)):
            raise ConfigError(
                f"tier {tid!r}: {field_name} must be a non-negative number or absent, "
                f"got {raw_val!r}"
            )
        v = float(raw_val)
        if v < 0:
            raise ConfigError(
                f"tier {tid!r}: {field_name} must be >= 0, got {v!r}"
            )
        return v

    cost_input = _parse_cost_field(raw.get("cost_input_per_mtok"), "cost_input_per_mtok")
    cost_output = _parse_cost_field(raw.get("cost_output_per_mtok"), "cost_output_per_mtok")

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

    return Tier(
        id=tid,
        base_url=base_url,
        dialect=dialect,
        context_limit=context_limit,
        privacy=privacy,
        tool_support=tool_support,
        auth_env=auth_env,
        model=tier_model or None,
        cost_input_per_mtok=cost_input,
        cost_output_per_mtok=cost_output,
        extra_body=extra_body,
        extra_body_defaults=extra_body_defaults,
        engine=engine,
        quantization=quantization,
        params=params,
        timeout=tier_timeout,
        max_concurrency=tier_max_concurrency,
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
        if not isinstance(auth_env, str) or not _ENV_NAME_RE.fullmatch(auth_env):
            raise ConfigError(
                f"purpose model {pid!r}: auth_env must name an ENV VAR matching "
                f"^[A-Z][A-Z0-9_]*$ (got {auth_env!r}); store a secret "
                f"reference, never the secret itself"
            )
        if _SECRET_SHAPED_RE.fullmatch(auth_env):
            raise ConfigError(
                f"purpose model {pid!r}: auth_env {auth_env!r} is shaped like a "
                f"credential literal, not an env-var name; store the env-var "
                f"NAME, never the secret"
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
        if not isinstance(auth_env, str) or not _ENV_NAME_RE.fullmatch(auth_env):
            raise ConfigError(
                f"audio route {route_id!r}: auth_env must name an ENV VAR "
                f"matching ^[A-Z][A-Z0-9_]*$ (got {auth_env!r})"
            )
        if _SECRET_SHAPED_RE.fullmatch(auth_env):
            raise ConfigError(
                f"audio route {route_id!r}: auth_env {auth_env!r} is shaped "
                "like a credential literal, not an env-var name"
            )

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

    raw_presets = router.get("presets", {})
    if not isinstance(raw_presets, dict):
        raise ConfigError(f"[router].presets must be a table in {path}")

    presets: dict[str, tuple[str, ...]] = {}
    for name, cands in raw_presets.items():
        if not isinstance(cands, list) or not all(isinstance(c, str) for c in cands):
            raise ConfigError(
                f"preset {name!r} must be a list of tier-id strings, got {cands!r}"
            )
        if not cands:
            raise ConfigError(f"preset {name!r} has no candidate tiers")
        if len(set(cands)) != len(cands):
            raise ConfigError(f"preset {name!r} has duplicate tier ids: {cands}")
        for cid in cands:
            if cid not in seen_ids:
                raise ConfigError(
                    f"preset {name!r} references unknown tier id: {cid!r}"
                )
        presets[name] = tuple(cands)

    mapping_version = router.get("mapping_version")
    if not isinstance(mapping_version, str) or not mapping_version:
        raise ConfigError(f"[router].mapping_version must be a non-empty string in {path}")

    # ``metered_cloud``: optional list of work-class strings.  Absent → empty →
    # cloud is NEVER a candidate (ADR-0001 / advise-and-defer:T002).
    raw_metered = router.get("metered_cloud", [])
    if not isinstance(raw_metered, list) or not all(
        isinstance(w, str) for w in raw_metered
    ):
        raise ConfigError(
            f"[router].metered_cloud must be a list of strings in {path}"
        )
    metered_cloud: tuple[str, ...] = tuple(raw_metered)

    # ``exhaustion_status``: HTTP status code returned when ALL quality-gated
    # tiers are exhausted (ADR-0001 §Mechanism, advise-and-defer:T004).
    # Default 503 is the keyless handoff signal — OpenClaw's transport failover
    # classifies it as "overloaded" and re-runs the request on the native
    # subscription provider.  Configurable so operators can match a different
    # gateway's transport-failover trigger if 503 does not map to it.
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

    # ``cost_sync`` (ADR-0001 / advise-and-defer:T006): opt-in, off by default.
    # When True, tiers with unset cost fields are filled from the LiteLLM pricing
    # JSON after loading; a network fetch only happens if the local cache is stale.
    # Static config values always win (explicit costs are never overwritten).
    raw_cost_sync = router.get("cost_sync", False)
    if not isinstance(raw_cost_sync, bool):
        raise ConfigError(
            f"[router].cost_sync must be a boolean (true/false) in {path}"
        )
    cost_sync: bool = raw_cost_sync

    if cost_sync:
        filled: list[Tier] = []
        for t in tiers:
            model_key = t.model or t.id
            inp, out = fetch_prices(model_key)
            cost_in = t.cost_input_per_mtok if t.cost_input_per_mtok is not None else inp
            cost_out = t.cost_output_per_mtok if t.cost_output_per_mtok is not None else out
            filled.append(replace(t, cost_input_per_mtok=cost_in, cost_output_per_mtok=cost_out))
        tiers = filled

    # ``relay_timeout`` (genericity:T005): transport timeout in seconds used to
    # build LOCAL-tier backends. Default kept short (20s) so a hung/cold local
    # serve fails fast to the next tier rather than sitting on the 120s cloud-
    # tuned default. bool is a subclass of int -- reject it explicitly.
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

    # ``verify_local_min`` (genericity:T004): gate for the minimal commit-window
    # safety net on a privacy=local tier under an "allow" verdict. Default True.
    raw_verify_local_min = router.get("verify_local_min", True)
    if not isinstance(raw_verify_local_min, bool):
        raise ConfigError(
            f"[router].verify_local_min must be a boolean (true/false) in {path}"
        )
    verify_local_min: bool = raw_verify_local_min

    raw_transparent_response_model = router.get("transparent_response_model", False)
    if not isinstance(raw_transparent_response_model, bool):
        raise ConfigError(
            f"[router].transparent_response_model must be a boolean "
            f"(true/false) in {path}"
        )
    transparent_response_model: bool = raw_transparent_response_model

    # ``profile_path``: optional path to a measured ``profile.json`` (written by
    # profile_bootstrap). Only the SHAPE is validated here; readability/content
    # are checked where it is consumed (serve.build_server), which fail-fasts
    # with a ConfigError on a configured-but-unloadable profile.
    raw_profile_path = router.get("profile_path")
    if raw_profile_path is not None and (
        not isinstance(raw_profile_path, str) or not raw_profile_path
    ):
        raise ConfigError(
            f"[router].profile_path must be a non-empty path string or absent "
            f"in {path}"
        )
    profile_path: Optional[str] = (
        os.path.expanduser(raw_profile_path) if raw_profile_path else None
    )

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
        presets=MappingProxyType(presets),
        mapping_version=mapping_version,
        metered_cloud=metered_cloud,
        exhaustion_status=exhaustion_status,
        cost_sync=cost_sync,
        relay_timeout=relay_timeout,
        verify_local_min=verify_local_min,
        transparent_response_model=transparent_response_model,
        profile_path=profile_path,
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

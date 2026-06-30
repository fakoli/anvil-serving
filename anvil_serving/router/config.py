"""Router/tier config schema + loader (stdlib-only).

Loads the ``[router]`` block of an anvil-serving TOML config into a frozen,
validated :class:`RouterConfig`. Every tier names an env-var for its auth
secret (``auth_env``); the secret literal is never stored here and is never
read at load time, so a config can be loaded with no secrets present.
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Mapping, Optional

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

# An auth reference must be an ENV-VAR NAME, not a secret literal.
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Some credential literals are all-caps alphanumeric and so also fit the env-name
# charset (e.g. an AWS access key id ``AKIA…`` / ``ASIA…``). Reject those shapes
# explicitly as defense-in-depth so a pasted key id can't masquerade as a name.
_SECRET_SHAPED_RE = re.compile(r"^(AKIA|ASIA)[0-9A-Z]{16}$")

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

    def tier(self, tier_id: str) -> Tier:
        """Return the tier with ``tier_id`` or raise :class:`ConfigError`."""
        for t in self.tiers:
            if t.id == tier_id:
                return t
        raise ConfigError(f"unknown tier id: {tier_id!r}")

    def candidates(self, preset: str) -> tuple[Tier, ...]:
        """Resolve a preset's ordered candidate tiers (raises if unknown)."""
        try:
            ids = self.presets[preset]
        except KeyError:
            raise ConfigError(f"unknown preset: {preset!r}") from None
        return tuple(self.tier(tid) for tid in ids)


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

    return RouterConfig(
        tiers=tuple(tiers),
        presets=MappingProxyType(presets),
        mapping_version=mapping_version,
        metered_cloud=metered_cloud,
        exhaustion_status=exhaustion_status,
        cost_sync=cost_sync,
    )

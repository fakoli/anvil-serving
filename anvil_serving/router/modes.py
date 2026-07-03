"""Global two-mode switch: resolve a MODE to its config path (ADR-0011 Phase 1 / flexibility:T012).

anvil runs exactly ONE mode of operation at a time (ADR-0011): the whole router
binds that mode's tiers + presets + serving config.

* ``agentic`` — the SGLang cache-friendly agent config (prefix/radix cache is
  load-bearing for multi-turn tool loops; ADR-0008).
* ``flexibility`` — cache-irrelevant single-turn quality generation on any-engine
  specialized tiers (ADR-0010).

The two modes are two SEPARATE, isolated config files; this module is the thin
resolver + docs that turns a mode NAME into that mode's config PATH, with no
change to the serving hot path (``serve.build_server`` still just loads a path).

Mechanism (the chosen "two config files + a --mode resolver", ADR-0011 §Decision
Phase 1) — two orthogonal concerns:

1. **Which mode is active** — resolved by strict precedence
   (:func:`resolve_mode`)::

       --mode FLAG   >   ANVIL_MODE env   >   [modes].active_mode   >   DEFAULT_MODE

   The ``active_mode`` default lives in an optional ``[modes]`` manifest (a small
   "base config" pointed at by the ``ANVIL_MODES_CONFIG`` env var,
   :func:`load_modes_manifest`); ``ANVIL_MODE`` overrides it and ``--mode``
   overrides both. An unknown mode from ANY source raises a clear
   :class:`~anvil_serving.router.config.ConfigError`.

2. **Which config file a mode maps to** — resolved by
   :func:`resolve_config_path`::

       ANVIL_CONFIG_<MODE> env   >   [modes].<mode> manifest entry   >   built-in default

   The per-mode env override (e.g. ``ANVIL_CONFIG_FLEXIBILITY=/etc/anvil/flex.toml``)
   lets a deployment point a mode at its real config without editing anything; the
   built-in defaults (``configs/example.toml`` for agentic, ``configs/example-flexibility.toml``
   for flexibility) make ``serve --mode …`` work out of the box in a source checkout.
   (In a non-editable install the packaged ``configs/`` are not shipped — set
   ``ANVIL_CONFIG_<MODE>`` or a ``[modes]`` manifest, or use explicit ``--config``.)

The existing explicit ``serve --config PATH`` bypasses this module entirely: an
explicit path is loaded verbatim, unchanged (the mode system is purely additive).

Stdlib-only (``tomllib``); reads env only for the NAMES resolved here, never a secret.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from .config import ConfigError

# --------------------------------------------------------------------------- #
# The two shipped modes of operation (ADR-0011). Fixed set for Phase 1 — a new
# mode is a deliberate, ADR-recorded addition, not a free-form string.
# --------------------------------------------------------------------------- #
MODE_AGENTIC = "agentic"
MODE_FLEXIBILITY = "flexibility"
KNOWN_MODES: tuple[str, ...] = (MODE_AGENTIC, MODE_FLEXIBILITY)
DEFAULT_MODE = MODE_AGENTIC

#: Env var selecting the active mode (overridden by the ``--mode`` flag).
ENV_MODE = "ANVIL_MODE"
#: Env var pointing at an optional ``[modes]`` manifest (carries ``active_mode``
#: and, optionally, per-mode config-path overrides). Absent -> no manifest.
ENV_MODES_CONFIG = "ANVIL_MODES_CONFIG"


def env_config_var(mode: str) -> str:
    """Name of the per-mode config-path override env var (``ANVIL_CONFIG_<MODE>``)."""
    return f"ANVIL_CONFIG_{mode.upper()}"


# Built-in default config path per mode, relative to the repo root (source
# checkout). ``modes.py`` lives at ``anvil_serving/router/modes.py`` -> parents[2]
# is the repo root that holds ``configs/``.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_MODE_CONFIG: Mapping[str, Path] = {
    MODE_AGENTIC: _REPO_ROOT / "configs" / "example.toml",
    MODE_FLEXIBILITY: _REPO_ROOT / "configs" / "example-flexibility.toml",
}


def _clean(value: Optional[str]) -> str:
    """Strip a possibly-None env value; empty/whitespace collapses to ``""`` (unset)."""
    return value.strip() if isinstance(value, str) else ""


@dataclass(frozen=True)
class ModesManifest:
    """A parsed ``[modes]`` manifest: the default active mode + per-mode path overrides.

    * ``active_mode`` — the default mode when neither ``--mode`` nor ``ANVIL_MODE``
      is set (may be ``None`` if the manifest omits it -> fall through to
      :data:`DEFAULT_MODE`).
    * ``paths`` — ``{mode: absolute_config_path}`` for any mode the manifest
      overrides (relative paths in the file are resolved against the manifest's
      own directory, so a manifest is portable). Omitted modes fall through to the
      per-mode env var / built-in default.
    """

    active_mode: Optional[str]
    paths: Mapping[str, str]
    source: Optional[str] = None


def load_modes_manifest(path: str) -> ModesManifest:
    """Load + validate the ``[modes]`` table of the TOML manifest at ``path``.

    Raises :class:`~anvil_serving.router.config.ConfigError` on a missing file,
    invalid TOML, a missing ``[modes]`` table, an ``active_mode`` outside
    :data:`KNOWN_MODES`, or a non-string per-mode path. Only the two known modes
    are read from the table; any other key is ignored (forward-compatible).
    """
    expanded = os.path.expanduser(path)
    try:
        with open(expanded, "rb") as f:
            data = tomllib.load(f)
    except OSError as e:
        raise ConfigError(f"cannot read modes manifest {expanded!r}: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in modes manifest {expanded!r}: {e}") from e

    modes = data.get("modes")
    if not isinstance(modes, dict):
        raise ConfigError(
            f"modes manifest {expanded!r} has no [modes] table (expected a "
            f"[modes] block with active_mode and/or per-mode config paths)"
        )

    active = modes.get("active_mode")
    if active is not None and (not isinstance(active, str) or active not in KNOWN_MODES):
        raise ConfigError(
            f"[modes].active_mode must be one of {list(KNOWN_MODES)} "
            f"(got {active!r}) in {expanded}"
        )

    base_dir = os.path.dirname(os.path.abspath(expanded))
    paths: dict[str, str] = {}
    for mode in KNOWN_MODES:
        raw = modes.get(mode)
        if raw is None:
            continue
        if not isinstance(raw, str) or not raw.strip():
            raise ConfigError(
                f"[modes].{mode} must be a non-empty config-path string in {expanded}"
            )
        expanded_path = os.path.expanduser(raw)
        paths[mode] = (
            expanded_path
            if os.path.isabs(expanded_path)
            else os.path.join(base_dir, expanded_path)
        )

    return ModesManifest(active_mode=active, paths=paths, source=expanded)


def resolve_mode(
    *,
    mode_flag: Optional[str] = None,
    env_mode: Optional[str] = None,
    active_mode: Optional[str] = None,
) -> str:
    """Resolve the effective mode by precedence.

    ``--mode FLAG > ANVIL_MODE env > [modes].active_mode > DEFAULT_MODE``. Every
    non-empty candidate is validated against :data:`KNOWN_MODES`; an unknown value
    from any source raises :class:`~anvil_serving.router.config.ConfigError` naming
    both the offending value and the known modes. An empty/whitespace ``env_mode``
    is treated as unset (falls through).
    """
    for label, candidate in (
        ("--mode", _clean(mode_flag)),
        (ENV_MODE, _clean(env_mode)),
        ("[modes].active_mode", _clean(active_mode)),
    ):
        if candidate:
            if candidate not in KNOWN_MODES:
                raise ConfigError(
                    f"unknown mode {candidate!r} (from {label}); "
                    f"known modes: {', '.join(KNOWN_MODES)}"
                )
            return candidate
    return DEFAULT_MODE


def resolve_config_path(
    mode: str,
    *,
    env: Mapping[str, str],
    manifest: Optional[ModesManifest] = None,
) -> str:
    """Resolve the config PATH a mode maps to.

    ``ANVIL_CONFIG_<MODE> env > [modes].<mode> manifest entry > built-in default``.
    Raises :class:`~anvil_serving.router.config.ConfigError` for a mode outside
    :data:`KNOWN_MODES`.
    """
    if mode not in KNOWN_MODES:
        raise ConfigError(
            f"unknown mode {mode!r}; known modes: {', '.join(KNOWN_MODES)}"
        )
    override = _clean(env.get(env_config_var(mode)))
    if override:
        return os.path.expanduser(override)
    if manifest is not None and mode in manifest.paths:
        return manifest.paths[mode]
    return str(_DEFAULT_MODE_CONFIG[mode])


def resolve_serve_config(
    *,
    config_flag: Optional[str],
    mode_flag: Optional[str],
    env: Mapping[str, str],
) -> tuple[str, Optional[str]]:
    """Top-level ``serve`` resolver: return ``(config_path, mode)``.

    * If ``config_flag`` (``--config PATH``) is set, it is returned verbatim
      (``os.path.expanduser`` only) with ``mode=None`` — the explicit-path
      contract is unchanged; the mode system is bypassed entirely.
    * Otherwise the active mode is resolved (:func:`resolve_mode`, honouring an
      optional ``ANVIL_MODES_CONFIG`` manifest for ``active_mode``) and mapped to
      its config path (:func:`resolve_config_path`); ``mode`` is that mode name.

    Never starts a server and never reads a secret — pure path resolution.
    """
    if _clean(config_flag):
        return os.path.expanduser(config_flag.strip()), None  # type: ignore[union-attr]

    manifest: Optional[ModesManifest] = None
    manifest_path = _clean(env.get(ENV_MODES_CONFIG))
    if manifest_path:
        manifest = load_modes_manifest(manifest_path)

    active_mode = manifest.active_mode if manifest else None
    mode = resolve_mode(
        mode_flag=mode_flag, env_mode=env.get(ENV_MODE), active_mode=active_mode
    )
    return resolve_config_path(mode, env=env, manifest=manifest), mode

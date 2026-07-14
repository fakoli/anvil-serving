"""``anvil-serving eval calibrate`` - operator entry to the guarded write-back batch (flexibility:T006).

This is the operator-facing verb for the *live* half of the quality-profile
loop (ADR-0009). It is the counterpart of the offline, CI-safe
``anvil-serving eval bootstrap`` (which replays committed eval fixtures): where
``bootstrap`` re-grades pre-committed outputs with no network, ``calibrate``
*measures your real LOCAL tiers* through their real backends, grades the fresh
outputs with the INDEPENDENT Agent-SDK judge, and writes a reviewable candidate
``profile.json``.

The verb is a thin wrapper over
:func:`anvil_serving.router.profile_bootstrap.run_live` — the guarded batch. What
this verb ADDS over ``python -m anvil_serving.router.profile_bootstrap --live``
(whose stub cannot supply tiers and stops at the guard) is the one missing piece:
it LOADS THE OPERATOR'S CONFIG and hands ``run_live`` the configured tiers. (It
passes ALL tiers; ``run_live`` structurally filters out ``privacy="cloud"`` tiers —
a Claude judge must never grade a Claude tier — and measures only the LOCAL ones.)
Everything else is ``run_live``'s job.

Two structural safeties are inherited from ``run_live`` and never weakened here:

* **Guarded — never silently calls a real tier.** ``run_live`` refuses
  (:class:`~anvil_serving.router.profile_bootstrap.LiveBootstrapNotConfigured`)
  unless the operator passes ``--endpoint TIER=URL`` covering every measured
  LOCAL tier *and* ``--i-understand-this-calls-real-tiers``. This verb passes
  those through verbatim and surfaces the refusal as a clean exit 2 — so CI /
  any un-confirmed invocation makes ZERO network / ``claude`` calls.
* **Never auto-promotes.** The written file is a CANDIDATE. Live routing is
  untouched. Promotion is a separate, explicit operator step: review the diff,
  then point ``[router].profile_path`` at the candidate (ADR-0009). This verb
  only ever *writes and instructs*; it never edits a config or swaps a profile.

The single model-call path is ``run_live``'s Agent-SDK grader seam (the ``claude``
CLI, ADR-0007) — never the raw Anthropic API. Stdlib-only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from pathlib import Path
from typing import List, Optional

from .router.config import ConfigError, load
from .router.modes import ENV_MODE, ENV_MODES_CONFIG, KNOWN_MODES, resolve_serve_config
from .guard import backup_file, confirmation_authorized

# Imported into this module's namespace (not called qualified) so a hermetic test
# can inject a fake via ``monkeypatch.setattr(calibrate, "run_live", ...)`` — the
# real batch calls real tiers + the `claude` judge and must never run in CI.
from .router.profile_bootstrap import LiveBootstrapNotConfigured, run_live

# Committed eval fixtures carry the per-work-class prompt sets run_live measures.
# The dated findings tree was relocated to the private notes repo; fall back to the
# legacy docs/findings path so a checkout that still carries it keeps working.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_EVAL_DATA_CANDIDATES = (
    os.path.join(_REPO, "tests", "fixtures", "eval-data"),
    os.path.join(_REPO, "docs", "findings", "eval-data"),
)
DEFAULT_EVAL_DATA = next(
    (p for p in _EVAL_DATA_CANDIDATES if os.path.isdir(p)), _EVAL_DATA_CANDIDATES[0]
)


def _parse_endpoints(specs: List[str]) -> Optional[dict]:
    """Parse repeated ``--endpoint TIER=URL`` specs into a ``{tier: url}`` dict.

    Returns ``None`` for an empty list (so ``run_live``'s guard sees "no
    endpoints" and refuses), or raises :class:`ValueError` on a malformed spec.
    """
    endpoints: dict = {}
    for spec in specs:
        tier, sep, url = spec.partition("=")
        if not sep or not tier or not url:
            raise ValueError(f"--endpoint expects TIER=URL, got {spec!r}")
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"--endpoint URL must be absolute http(s): {url!r}")
        if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
            raise ValueError(
                "--endpoint URL must not contain userinfo, query, or fragment; "
                "use environment variables for credentials"
            )
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("--endpoint URL has an invalid port: %s" % exc) from exc
        endpoints[tier] = url
    return endpoints or None


def _promote_instructions(out: str, n_rows: Optional[int]) -> str:
    """The review -> promote message printed after a successful candidate write.

    Promotion is deliberately manual (ADR-0009): the operator reviews the diff and
    points ``[router].profile_path`` at the candidate. Nothing here auto-promotes.
    """
    measured = f" ({n_rows} measured row(s))" if n_rows is not None else ""
    return (
        f"\nwrote candidate quality profile -> {out}{measured}\n"
        f"\nThis is a CANDIDATE. Nothing was promoted; live routing is UNCHANGED.\n"
        f"Review it (diff against your current profile), then promote it by pointing\n"
        f"your router config at it:\n"
        f"\n"
        f"    [router]\n"
        f'    profile_path = "{out}"\n'
        f"\n"
        f"Restart `anvil-serving router run` to route on the measured verdicts.\n"
    )


def _measured_row_count(out_path: Optional[Path]) -> Optional[int]:
    """Best-effort count of measured entries in the written candidate (or None).

    Never raises: a missing/short-circuited write (e.g. an injected fake in a
    test) just yields ``None`` so the summary line is omitted.
    """
    if out_path is None or not out_path.is_file():
        return None
    try:
        doc = json.loads(out_path.read_text(encoding="utf-8"))
        return len(doc.get("entries", []))
    except Exception:
        return None


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="anvil-serving eval calibrate",
        description=(
            "Measure configured local tiers and write a reviewable candidate profile. "
            "Nothing is auto-promoted.\n\n"
            "Examples:\n"
            "  anvil-serving eval calibrate --config router.toml --eval-data ./eval-data "
            "--out ./candidate.json --endpoint fast-local=http://127.0.0.1:30001/v1 --dry-run\n"
            "  anvil-serving eval calibrate --config router.toml --eval-data ./eval-data "
            "--out ./candidate.json --endpoint fast-local=http://127.0.0.1:30001/v1 --confirm"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    selector = p.add_mutually_exclusive_group()
    selector.add_argument(
        "--config",
        metavar="PATH",
        help=(
            "router TOML config whose [router] tiers are loaded and measured "
            "(e.g. configs/example.toml). Bypasses the --mode/ANVIL_MODE resolver."
        ),
    )
    selector.add_argument(
        "--mode",
        choices=KNOWN_MODES,
        help=(
            "global mode (ADR-0011) whose config supplies the tiers, WITHOUT "
            "--config. Precedence --mode > ANVIL_MODE > [modes].active_mode > default."
        ),
    )
    p.add_argument(
        "--out",
        required=True,
        metavar="PROFILE_JSON",
        help="write the reviewable candidate profile.json here (required).",
    )
    p.add_argument(
        "--endpoint",
        action="append",
        default=[],
        metavar="TIER=URL",
        help=(
            "CONFIRM a LOCAL tier's serving URL, e.g. fast-local=http://127.0.0.1:"
            "30001/v1; repeatable. Every measured LOCAL tier's id MUST be listed "
            "here (the guard: the batch never dials a tier you did not confirm)."
        ),
    )
    p.add_argument(
        "--eval-data",
        required=True,
        metavar="EVAL_DATA_DIR",
        help="retained eval fixtures containing prompts/",
    )
    p.add_argument(
        "--i-understand-this-calls-real-tiers", dest="legacy_confirm",
        action="store_true", help=argparse.SUPPRESS,
    )
    p.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        metavar="N",
        help=(
            "per-request output budget for the batch (default: run_live's "
            ">=4096, which keeps a thinking-by-default local model from spending "
            "its whole budget reasoning — CLAUDE.md gotcha #6/#9)."
        ),
    )
    p.add_argument("--overwrite", action="store_true",
                   help="replace an existing candidate after a numbered backup")
    p.add_argument("--dry-run", action="store_true",
                   help="validate and print the plan; call no tiers or judge and write nothing")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    env = os.environ

    # Never silently calibrate a default: require an explicit config selector, the
    # same rule `serve` enforces (--config, --mode, ANVIL_MODE, or an ANVIL_MODES_CONFIG
    # manifest with an active_mode). Bare `calibrate` is a usage error.
    if not (
        (args.config or "").strip()
        or args.mode
        or (env.get(ENV_MODE) or "").strip()
        or (env.get(ENV_MODES_CONFIG) or "").strip()
    ):
        print(
            "anvil-serving eval calibrate: no config selected: pass --config PATH or "
            f"--mode {{{'|'.join(KNOWN_MODES)}}} (or set {ENV_MODE} / point "
            f"{ENV_MODES_CONFIG} at a [modes] manifest) so the tiers to measure "
            "are known",
            file=sys.stderr,
        )
        return 2

    try:
        endpoints = _parse_endpoints(args.endpoint)
    except ValueError as exc:
        print(f"anvil-serving eval calibrate: {exc}", file=sys.stderr)
        return 2

    try:
        config_path, mode = resolve_serve_config(
            config_flag=args.config, mode_flag=args.mode, env=env
        )
        config = load(config_path)
    except ConfigError as exc:
        print(f"anvil-serving eval calibrate: {exc}", file=sys.stderr)
        return 2

    if mode is not None:
        print(f"anvil-serving eval calibrate: mode={mode!r} -> config {config_path}",
              file=sys.stderr, flush=True)

    out_path = Path(args.out)
    # Pre-flight the output dir BEFORE the expensive live batch — otherwise a bad
    # --out surfaces only as a late write error, after the real tier generations +
    # judge grades have already run (and their measurement work is lost). (T006 critic.)
    if not out_path.parent.is_dir():
        print(
            f"anvil-serving eval calibrate: output directory does not exist: {out_path.parent}",
            file=sys.stderr,
        )
        return 2
    eval_data = Path(args.eval_data)
    if not eval_data.is_dir():
        print(
            f"anvil-serving eval calibrate: eval-data directory does not exist: {eval_data} "
            "(pass --eval-data pointing at your committed prompts/)",
            file=sys.stderr,
        )
        return 2
    if out_path.exists() and not args.overwrite:
        print(
            f"anvil-serving eval calibrate: output exists: {out_path}; choose another "
            "--out or pass --overwrite (a numbered backup is created)",
            file=sys.stderr,
        )
        return 2
    if args.max_tokens is not None and not 1 <= args.max_tokens <= 65536:
        print("anvil-serving eval calibrate: --max-tokens must be from 1 through 65536",
              file=sys.stderr)
        return 2
    local_tiers = [tier for tier in config.tiers if tier.privacy != "cloud"]
    missing_endpoints = [tier.id for tier in local_tiers if tier.id not in (endpoints or {})]
    if missing_endpoints:
        print(
            "anvil-serving eval calibrate: --endpoint must cover every local tier: %s"
            % ", ".join(missing_endpoints),
            file=sys.stderr,
        )
        return 2
    mismatched = [
        "%s: confirmed %r, configured %r" % (tier.id, endpoints[tier.id], tier.base_url)
        for tier in local_tiers
        if endpoints[tier.id].rstrip("/") != tier.base_url.rstrip("/")
    ]
    if mismatched:
        print(
            "anvil-serving eval calibrate: confirmed endpoint does not match configured "
            "backend: %s" % "; ".join(mismatched),
            file=sys.stderr,
        )
        return 2
    if args.dry_run:
        print("calibration plan")
        print(f"  config: {config_path}")
        print(f"  eval data: {eval_data}")
        print(f"  output: {out_path}")
        print("  endpoints:")
        for tier, endpoint in sorted((endpoints or {}).items()):
            print(f"    {tier}: {endpoint}")
        print("  deferred: tier requests, independent judge calls, candidate write")
        return 0
    if out_path.exists():
        backup = backup_file(out_path)
        print(f"backed up existing candidate -> {backup}")
    # Pass ALL configured tiers: run_live structurally filters out cloud/Claude
    # tiers (a Claude judge must never grade a Claude tier — no self-verification)
    # and measures only the LOCAL ones, requiring each to be covered by --endpoint.
    # `mode` (resolved above) threads into the candidate fingerprints so a profile
    # measured under `--mode flexibility` matches the live flexibility-mode serve.
    run_kwargs = dict(
        tiers=config.tiers,
        endpoints=endpoints,
        eval_data_root=eval_data,
        out_path=out_path,
        confirm_calls_real_tiers=(confirmation_authorized() or args.legacy_confirm),
        mode=mode,
    )
    if args.max_tokens is not None:
        run_kwargs["max_tokens"] = args.max_tokens

    try:
        run_live(**run_kwargs)
    except LiveBootstrapNotConfigured as exc:
        # The guard fired BEFORE any tier/judge was dialed (missing confirmation,
        # uncovered endpoints, no local tiers/prompts). Surface it cleanly; this is
        # the CI-safe refusal path — nothing was measured, nothing promoted.
        print(f"anvil-serving eval calibrate: not configured to run: {exc}", file=sys.stderr)
        return 2

    print(_promote_instructions(str(out_path), _measured_row_count(out_path)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""validate.py — T013 validation tooling for the anvil-serving x OpenClaw integration.

Revalidates the wire-form contract in docs/OPENCLAW-INTEGRATION-SPEC.md §3 and
the cadence gate in §6. These checks originally settled the
pre-plugin live gaps now recorded in that document's §7 history:

  1. WIRE FORM   — every Anvil-bound ``model`` string names a preset exported by
                   the shipped OpenClaw plugin, AND the anvil front door accepts
                   BOTH the bare (``planning``) and namespaced
                   (``anvil/planning``) wire form.
  2. FIRE CADENCE — ``before_model_resolve`` fires once per user message (so the
                   plugin's per-turn classification is real).

Python-stdlib-only; Node is invoked to load the same plugin ESM export OpenClaw
uses. The two checks are exercised against a committed *representative fixture*
(``hook-fire-log.jsonl``); the live capture against the real OpenClaw install on
Fakoli Mini is a MANUAL step documented in README.md.

Usage
-----
    # the T013 verification command (runs both checks against the fixture):
    python examples/openclaw/validate.py \
        --assert-wire-form \
        --assert-fire-cadence examples/openclaw/hook-fire-log.jsonl

    # against a REAL live capture (the manual step — see README.md):
    python examples/openclaw/validate.py \
        --config /path/to/deployed-router.toml \
        --assert-wire-form --capture captured-request.json \
        --assert-fire-cadence real-hook-fire-log.jsonl

Exit status
-----------
Non-zero on a wire-form violation, unavailable/malformed plugin vocabulary,
malformed capture/log evidence, or the front door failing to accept both forms.
A fire cadence that is not 1 fire / user message is *documented* (the actual
cadence is printed) but does not by itself fail — per the acceptance criterion
"fire-count == user-message-count (or the actual cadence is documented)".
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# locate the repo root so ``anvil_serving`` imports from source even when the
# package is not pip-installed. examples/openclaw/validate.py -> parents[2] root.
# --------------------------------------------------------------------------- #
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
CONFIG_PATH = REPO_ROOT / "configs" / "example.toml"
PLUGIN_CLASSIFIER_PATH = (
    REPO_ROOT / "plugins" / "openclaw-anvil-intent-router" / "classify.mjs"
)


def _load_openclaw_preset_ids() -> Tuple[str, ...]:
    """Load the preset enum exported by the shipped plugin runtime.

    This intentionally executes the same ESM module OpenClaw loads. A stale
    Python copy cannot make this validator pass after the plugin vocabulary
    changes. Node is already a prerequisite for running the OpenClaw plugin.
    """
    module_uri = PLUGIN_CLASSIFIER_PATH.resolve().as_uri()
    source = (
        f"import {{ PRESETS }} from {json.dumps(module_uri)}; "
        "process.stdout.write(JSON.stringify(PRESETS));"
    )
    try:
        result = subprocess.run(
            ["node", "--input-type=module", "--eval", source],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        parsed = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise MalformedLog(f"could not load OpenClaw plugin preset vocabulary: {exc}") from exc
    if (
        not isinstance(parsed, list)
        or not parsed
        or any(not isinstance(preset, str) or not preset for preset in parsed)
        or len(set(parsed)) != len(parsed)
    ):
        raise MalformedLog(
            "OpenClaw plugin PRESETS must be a non-empty list of unique strings"
        )
    return tuple(parsed)


def _wire_form_re(preset_ids: Tuple[str, ...]) -> re.Pattern[str]:
    pattern = "|".join(re.escape(preset) for preset in preset_ids)
    return re.compile(rf"(?:anvil/)?(?:{pattern})\Z")


# --------------------------------------------------------------------------- #
# small reporting helpers (no third-party deps)
# --------------------------------------------------------------------------- #
def _ok(msg: str) -> None:
    print(f"  PASS  {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL  {msg}")


def _info(msg: str) -> None:
    print(f"  ....  {msg}")


# --------------------------------------------------------------------------- #
# log / capture parsing
# --------------------------------------------------------------------------- #
class MalformedLog(Exception):
    """The fire-cadence log could not be parsed or a record was missing a
    required field. This is a hard failure (exit non-zero), distinct from a
    cadence that merely differs from 1 fire / message."""


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Parse a JSONL file into a list of records. Raises :class:`MalformedLog`
    on bad JSON. Blank lines are skipped."""
    if not path.exists():
        raise MalformedLog(f"log file not found: {path}")
    records: List[Dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MalformedLog(f"{path}:{lineno}: not valid JSON ({exc})") from exc
        if not isinstance(obj, dict):
            raise MalformedLog(f"{path}:{lineno}: record is not a JSON object")
        records.append(obj)
    return records


def _session_of(rec: Dict[str, Any]) -> Optional[str]:
    """The session grouping key for a fire record (``sessionKey`` preferred,
    else ``runId``)."""
    for key in ("sessionKey", "runId"):
        val = rec.get(key)
        if val not in (None, ""):
            return str(val)
    return None


def _user_message_of(rec: Dict[str, Any]) -> Optional[str]:
    """The per-message identity a fire belongs to. Each fire carries the user
    message it was triggered by (``userMessageIndex`` or ``userMessageId``)."""
    for key in ("userMessageIndex", "userMessageId"):
        if key in rec and rec[key] not in (None, ""):
            return str(rec[key])
    return None


# --------------------------------------------------------------------------- #
# check 1a: wire-form regex over captured / logged model strings
# --------------------------------------------------------------------------- #
def _collect_model_strings(
    capture: Optional[Path], fire_records: Optional[List[Dict[str, Any]]]
) -> Tuple[List[Tuple[str, str]], str]:
    """Gather (source-label, model-string) pairs to check against the wire-form
    regex. Priority: an explicit ``--capture`` file (a real outbound request),
    else the ``modelOverride`` values present in the fire-cadence log (which are
    the representative selection strings).

    Returns ``(pairs, origin)`` where ``origin`` describes where the strings came
    from (for the report) — empty list means no model strings were available.
    """
    if capture is not None:
        if not capture.exists():
            raise MalformedLog(f"capture file not found: {capture}")
        text = capture.read_text(encoding="utf-8").strip()
        if not text:
            raise MalformedLog(f"capture file is empty: {capture}")
        pairs: List[Tuple[str, str]] = []
        # accept a single JSON object, a JSON array of objects, or JSONL.
        objs: List[Any] = []
        try:
            parsed = json.loads(text)
            objs = parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            for line in text.splitlines():
                line = line.strip()
                if line:
                    try:
                        objs.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        raise MalformedLog(
                            f"{capture}: invalid JSON capture record ({exc})"
                        ) from exc
        for i, obj in enumerate(objs):
            if isinstance(obj, str):
                pairs.append((f"capture[{i}]", obj))
            elif isinstance(obj, dict):
                # A decision log can contain valid native/cloud routes whose
                # modelOverride is not an Anvil preset. Only Anvil-bound records
                # prove this wire contract; raw outbound request bodies have a
                # plain `model` and are always checked.
                provider = obj.get("providerOverride")
                destination = obj.get("destination")
                is_decision_record = (
                    "providerOverride" in obj or "destination" in obj
                )
                if is_decision_record:
                    model_override = obj.get("modelOverride")
                    if (
                        destination not in {"anvil", "native"}
                        or not isinstance(provider, str)
                        or not provider.strip()
                        or provider != provider.strip()
                        or not isinstance(model_override, str)
                        or not model_override.strip()
                    ):
                        raise MalformedLog(
                            f"capture[{i}] has malformed decision-route fields"
                        )
                    if (destination == "anvil") != (provider == "anvil"):
                        raise MalformedLog(
                            f"capture[{i}] has inconsistent destination/providerOverride"
                        )
                    if destination == "native":
                        continue
                    key = "modelOverride"
                else:
                    key = "model" if "model" in obj else "modelOverride"
                if key not in obj:
                    raise MalformedLog(
                        f"capture[{i}] has no model/modelOverride field"
                    )
                model = obj[key]
                if model is None:
                    raise MalformedLog(f"capture[{i}].{key} is null")
                pairs.append((f"capture[{i}].{key}", str(model)))
            else:
                raise MalformedLog(
                    f"capture[{i}] is not a JSON object or model string"
                )
        if not pairs:
            raise MalformedLog(
                "capture contains no Anvil-bound model string to validate"
            )
        return pairs, f"captured outbound request(s) ({capture.name})"

    if fire_records:
        pairs = []
        for i, rec in enumerate(fire_records):
            mo = rec.get("modelOverride")
            if mo:
                pairs.append((f"fire-log[{i}].modelOverride", str(mo)))
        if pairs:
            return pairs, "representative modelOverride strings from the fire-cadence log"
    return [], ""


def check_wire_form(
    capture: Optional[Path],
    fire_records: Optional[List[Dict[str, Any]]],
    config_path: Path = CONFIG_PATH,
) -> bool:
    """Run both halves of the wire-form check. Returns ``True`` if it passes."""
    openclaw_preset_ids = _load_openclaw_preset_ids()
    wire_form_re = _wire_form_re(openclaw_preset_ids)
    print("[wire-form] outbound `model` string == ^(anvil/)?<plugin-preset>$")
    passed = True

    # (a) captured / logged model strings satisfy the regex.
    pairs, origin = _collect_model_strings(capture, fire_records)
    if pairs:
        _info(f"checking {len(pairs)} model string(s) from {origin}")
        for label, model in pairs:
            if wire_form_re.fullmatch(model):
                _ok(f"{label} = {model!r}")
            else:
                _fail(f"{label} = {model!r} does NOT match {wire_form_re.pattern}")
                passed = False
    else:
        _info(
            "no captured outbound request provided - this is the LIVE manual step "
            "(see README.md). Skipping (a); (b) still proves acceptance."
        )

    # (b) the front door accepts BOTH wire forms (the load-bearing proof): every
    # preset resolves identically whether bare or `anvil/`-prefixed.
    try:
        from anvil_serving.router.config import load
        from anvil_serving.router.intent import PRESETS, resolve
        from anvil_serving.router.internal import InternalRequest, Message
    except Exception as exc:  # pragma: no cover - environment/import problem
        _fail(f"could not import anvil_serving.router (is it on PYTHONPATH?): {exc}")
        return False

    cfg = load(str(config_path))

    def _req(model: str) -> "InternalRequest":
        return InternalRequest(model=model, messages=[Message("user", "hello")], raw={})

    # The plugin vocabulary and router-global vocabulary are deliberately
    # different: optional router presets such as ocr/vision need not be emitted
    # or mapped by this text/voice integration. Every plugin-emittable preset,
    # however, must be configured or OpenClaw could select an unroutable model.
    openclaw_ids = set(openclaw_preset_ids)
    configured_by_lower = {str(preset).lower(): str(preset) for preset in cfg.presets}
    missing = sorted(openclaw_ids - set(configured_by_lower))
    if missing:
        _fail(
            "OpenClaw preset(s) are not configured in the target router config: "
            f"{missing}"
        )
        passed = False

    global_ids = {p.id for p in PRESETS}
    global_only = sorted(global_ids - openclaw_ids)
    configured_only = sorted(set(configured_by_lower) - openclaw_ids)
    if global_only:
        _info(f"router-global preset(s) outside the OpenClaw contract: {global_only}")
    if configured_only:
        _info(f"configured preset(s) outside the OpenClaw contract: {configured_only}")

    _info(
        "proving front-door accepts both forms for "
        f"{len(openclaw_preset_ids)} OpenClaw preset(s)"
    )
    for preset_id in openclaw_preset_ids:
        bare = resolve(_req(preset_id), cfg)
        prefixed = resolve(_req(f"anvil/{preset_id}"), cfg)
        configured_id = configured_by_lower.get(preset_id)
        if (
            configured_id is not None
            and bare == prefixed
            and bare.preset == configured_id
            and bare.source == "declared-preset"
        ):
            _ok(
                f"{preset_id!r} == anvil/{preset_id!r} -> "
                f"preset={bare.preset!r}, tiers={bare.candidate_tiers}"
            )
        else:
            _fail(
                f"{preset_id!r} and anvil/{preset_id!r} did NOT resolve identically "
                f"(bare.preset={bare.preset!r}, prefixed.preset={prefixed.preset!r}, "
                f"equal={bare == prefixed}, source={bare.source!r}, "
                f"configured={configured_id is not None})"
            )
            passed = False

    print(f"[wire-form] {'PASS' if passed else 'FAIL'}\n")
    return passed


# --------------------------------------------------------------------------- #
# check 2: firing cadence (fires per user message, per session)
# --------------------------------------------------------------------------- #
def check_fire_cadence(records: List[Dict[str, Any]]) -> Tuple[bool, bool]:
    """Group fires by session and assert fire-count == user-message-count.

    Returns ``(malformed, cadence_is_one)``:
      * ``malformed``  — a hard failure (missing required fields / no records).
      * ``cadence_is_one`` — every session fired exactly once per user message.
        When False the ACTUAL cadence (fires per message) is printed and the run
        does NOT fail on that basis alone (the criterion allows documenting it).
    """
    print("[fire-cadence] before_model_resolve fires == user messages (per session)")

    if not records:
        _fail("fire-cadence log has no records")
        return True, False  # malformed

    # validate required fields up front (malformed -> hard fail).
    for i, rec in enumerate(records):
        if _session_of(rec) is None:
            _fail(f"record[{i}] has no session key (sessionKey/runId): {rec}")
            return True, False
        if _user_message_of(rec) is None:
            _fail(f"record[{i}] has no user-message id (userMessageIndex/userMessageId): {rec}")
            return True, False

    # group fires by session, then count fires per distinct user message.
    sessions: Dict[str, Dict[str, int]] = {}
    for rec in records:
        sess = _session_of(rec)
        msg = _user_message_of(rec)
        per_msg = sessions.setdefault(sess, {})
        per_msg[msg] = per_msg.get(msg, 0) + 1

    cadence_is_one = True
    for sess, per_msg in sorted(sessions.items()):
        fires = sum(per_msg.values())
        n_msgs = len(per_msg)
        cadence = fires / n_msgs if n_msgs else 0.0
        if fires == n_msgs:
            _ok(
                f"session {sess!r}: {fires} fire(s) over {n_msgs} user message(s) "
                f"-> 1.0 fire/message"
            )
        else:
            cadence_is_one = False
            # document the ACTUAL cadence rather than only failing.
            hot = ", ".join(
                f"msg {m}={c} fires" for m, c in sorted(per_msg.items()) if c != 1
            )
            _info(
                f"session {sess!r}: ACTUAL cadence {cadence:.2f} fire/message "
                f"({fires} fires over {n_msgs} messages; {hot}) - "
                f"document this in OPENCLAW-INTEGRATION-SPEC.md (does NOT fail the gate)"
            )

    print(f"[fire-cadence] {'1.0 fire/message (PASS)' if cadence_is_one else 'cadence documented'}\n")
    return False, cadence_is_one


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate.py",
        description="Validate the OpenClaw wire-form and fire-cadence contracts.",
    )
    parser.add_argument(
        "--assert-wire-form",
        action="store_true",
        help="assert the outbound model strings match ^(anvil/)?<preset>$ AND the "
        "front door accepts both the bare and the anvil/-prefixed form.",
    )
    parser.add_argument(
        "--config",
        metavar="FILE",
        default=str(CONFIG_PATH),
        help="router config whose preset mappings must cover the plugin vocabulary "
        "(default: configs/example.toml)",
    )
    parser.add_argument(
        "--capture",
        metavar="FILE",
        default=None,
        help="optional outbound request or plugin decision log (JSON/JSONL) for "
        "the wire-form (a) check; omit to use the cadence fixture's "
        "modelOverride strings.",
    )
    parser.add_argument(
        "--assert-fire-cadence",
        metavar="LOG",
        default=None,
        help="path to the hook-fire JSONL log; assert fires == user messages per session.",
    )
    args = parser.parse_args(argv)

    if not args.assert_wire_form and not args.assert_fire_cadence:
        parser.error("nothing to do: pass --assert-wire-form and/or --assert-fire-cadence")

    failures: List[str] = []

    # parse the fire-cadence log once; it feeds both checks.
    fire_records: Optional[List[Dict[str, Any]]] = None
    fire_log_malformed = False
    if args.assert_fire_cadence:
        try:
            fire_records = _load_jsonl(Path(args.assert_fire_cadence))
        except MalformedLog as exc:
            print("[fire-cadence] before_model_resolve fires == user messages (per session)")
            _fail(str(exc))
            print("[fire-cadence] FAIL (malformed log)\n")
            failures.append("fire-cadence: malformed log")
            fire_log_malformed = True

    if args.assert_wire_form:
        capture = Path(args.capture) if args.capture else None
        try:
            if not check_wire_form(capture, fire_records, Path(args.config)):
                failures.append("wire-form")
        except MalformedLog as exc:
            _fail(str(exc))
            failures.append("wire-form: malformed capture")

    if args.assert_fire_cadence and not fire_log_malformed:
        malformed, cadence_is_one = check_fire_cadence(fire_records or [])
        if malformed:
            failures.append("fire-cadence: malformed log")
        elif not cadence_is_one:
            # documented, not failed — surface it so it's not silently swallowed.
            print(
                "NOTE: fire cadence is not 1 fire / user message. This is allowed by "
                "the acceptance criterion IF documented in the spec — record the actual "
                "cadence in docs/OPENCLAW-INTEGRATION-SPEC.md.\n"
            )

    print("=" * 70)
    if failures:
        print(f"RESULT: FAIL ({', '.join(failures)})")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

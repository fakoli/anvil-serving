#!/usr/bin/env python3
"""validate.py — T013 validation tooling for the anvil-serving x OpenClaw integration.

Revalidates the wire-form contract in docs/OPENCLAW-INTEGRATION-SPEC.md §3 and
the cadence gate in §6. These checks originally settled the
pre-plugin live gaps now recorded in that document's §7 history:

  1. WIRE FORM   — every outbound ``model`` string is ``(anvil/)?<preset>``, AND
                   the anvil front door accepts BOTH the bare (``planning``) and
                   the namespaced (``anvil/planning``) wire form.
  2. FIRE CADENCE — ``before_model_resolve`` fires once per user message (so the
                   plugin's per-turn classification is real).

Stdlib-only. The two checks are exercised against a committed *representative
fixture* (``hook-fire-log.jsonl``); the live capture against the real OpenClaw
install on Fakoli Mini is a MANUAL step documented in README.md.

Usage
-----
    # the T013 verification command (runs both checks against the fixture):
    python examples/openclaw/validate.py \
        --assert-wire-form \
        --assert-fire-cadence examples/openclaw/hook-fire-log.jsonl

    # against a REAL live capture (the manual step — see README.md):
    python examples/openclaw/validate.py \
        --assert-wire-form --capture captured-request.json \
        --assert-fire-cadence real-hook-fire-log.jsonl

Exit status
-----------
Non-zero ONLY on a wire-form violation (a captured ``model`` string that does not
match the regex, or the front door failing to accept both forms) or a malformed
fire-cadence log. A fire cadence that is not 1 fire / user message is *documented*
(the actual cadence is printed) but does not by itself fail — per the acceptance
criterion "fire-count == user-message-count (or the actual cadence is documented)".
"""
from __future__ import annotations

import argparse
import json
import re
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

# The wire-form contract (docs/OPENCLAW-INTEGRATION-SPEC.md §3). The OpenClaw
# selection string is ``anvil/<preset>``; the openai-completions convention puts
# the bare id on the wire — anvil must accept BOTH, so the optional prefix.
WIRE_FORM_RE = re.compile(r"^(anvil/)?(planning|quick-edit|review|chat|chat-fast|long-context)$")


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
                    objs.append(json.loads(line))
        for i, obj in enumerate(objs):
            if isinstance(obj, str):
                pairs.append((f"capture[{i}]", obj))
            elif isinstance(obj, dict):
                model = obj.get("model", obj.get("modelOverride"))
                if model:
                    pairs.append((f"capture[{i}].model", str(model)))
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
    capture: Optional[Path], fire_records: Optional[List[Dict[str, Any]]]
) -> bool:
    """Run both halves of the wire-form check. Returns ``True`` if it passes."""
    print("[wire-form] outbound `model` string == ^(anvil/)?<preset>$")
    passed = True

    # (a) captured / logged model strings satisfy the regex.
    pairs, origin = _collect_model_strings(capture, fire_records)
    if pairs:
        _info(f"checking {len(pairs)} model string(s) from {origin}")
        for label, model in pairs:
            if WIRE_FORM_RE.match(model):
                _ok(f"{label} = {model!r}")
            else:
                _fail(f"{label} = {model!r} does NOT match {WIRE_FORM_RE.pattern}")
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

    cfg = load(str(CONFIG_PATH))

    def _req(model: str) -> "InternalRequest":
        return InternalRequest(model=model, messages=[Message("user", "hello")], raw={})

    # drift guard: the literal regex must enumerate exactly the canonical presets.
    regex_ids = {"planning", "quick-edit", "review", "chat", "chat-fast", "long-context"}
    canonical_ids = {p.id for p in PRESETS}
    if regex_ids != canonical_ids:
        _fail(
            "wire-form regex preset set has drifted from anvil_serving.router.intent.PRESETS: "
            f"regex={sorted(regex_ids)} canonical={sorted(canonical_ids)}"
        )
        passed = False

    _info(f"proving front-door accepts both forms for {len(PRESETS)} preset(s)")
    for p in PRESETS:
        bare = resolve(_req(p.id), cfg)
        prefixed = resolve(_req(f"anvil/{p.id}"), cfg)
        if bare == prefixed and bare.preset == p.id and bare.source == "declared-preset":
            _ok(f"{p.id!r} == anvil/{p.id!r} -> preset={bare.preset!r}, tiers={bare.candidate_tiers}")
        else:
            _fail(
                f"{p.id!r} and anvil/{p.id!r} did NOT resolve identically "
                f"(bare.preset={bare.preset!r}, prefixed.preset={prefixed.preset!r}, "
                f"equal={bare == prefixed}, source={bare.source!r})"
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
        description="Validate the two CRITICAL OpenClaw live gaps (wire-form + fire-cadence).",
    )
    parser.add_argument(
        "--assert-wire-form",
        action="store_true",
        help="assert the outbound model strings match ^(anvil/)?<preset>$ AND the "
        "front door accepts both the bare and the anvil/-prefixed form.",
    )
    parser.add_argument(
        "--capture",
        metavar="FILE",
        default=None,
        help="optional captured outbound request (JSON/JSONL) for the wire-form (a) "
        "check — this is the LIVE manual artifact; omit to use the fixture's "
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
            if not check_wire_form(capture, fire_records):
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

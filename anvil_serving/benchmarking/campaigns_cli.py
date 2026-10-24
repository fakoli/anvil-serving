"""Short local commands for frozen benchmark campaigns."""
from __future__ import annotations

import argparse
import json
import os
import sys

from ..guard import confirmation_authorized
from . import campaigns
from .jobs import BenchmarkJobError
from .jobs_cli import _store


def _parser():
    parser = argparse.ArgumentParser(prog="anvil-serving eval benchmark campaign")
    actions = parser.add_subparsers(dest="action", required=True)
    for action in ("plan", "prepare", "run", "report", "cancel"):
        command = actions.add_parser(action)
        if action in {"plan", "prepare"}:
            command.add_argument("--spec", required=True)
        else:
            command.add_argument("campaign_id")
            command.add_argument("--ownership-id", default=os.environ.get("ANVIL_BENCHMARK_OWNERSHIP_ID"))
        if action == "run":
            command.add_argument("--arm", required=True)
        if action == "prepare":
            command.add_argument("--offline", action="store_true")
        if action in {"prepare", "run", "cancel"}:
            command.add_argument("--confirm", action="store_true")
    return parser


def run(argv):
    args = _parser().parse_args(argv)
    if args.action == "plan":
        return campaigns.plan_campaign(campaigns.read_campaign_spec(args.spec))
    if args.action in {"prepare", "run", "cancel"} and not (args.confirm or confirmation_authorized()):
        raise BenchmarkJobError("confirmation_required", f"campaign {args.action} requires --confirm")
    if args.action != "prepare" and not args.ownership_id:
        raise BenchmarkJobError("campaign_owner_required", "provide --ownership-id or configure ANVIL_BENCHMARK_OWNERSHIP_ID")
    store = _store()
    if args.action == "prepare":
        return campaigns.prepare_campaign(campaigns.read_campaign_spec(args.spec), store,
                                          cache_root=os.environ.get("ANVIL_BENCHMARK_CACHE_ROOT"), offline=args.offline)
    manifest = campaigns.load_campaign(store.run_root, args.ownership_id, args.campaign_id)
    if args.action == "run":
        return campaigns.run_campaign(manifest, store, arm_id=args.arm)
    if args.action == "cancel":
        return campaigns.cancel_campaign(manifest, store)
    from .campaign_report import report_campaign
    directory = campaigns.campaign_directory(store.run_root, args.ownership_id, args.campaign_id)
    return report_campaign(manifest, store, state=campaigns._state(directory))


def main(argv=None):
    try:
        result = run(list(sys.argv[1:] if argv is None else argv))
    except BenchmarkJobError as exc:
        print(json.dumps({"ok": False, "error": {"code": exc.code, "message": exc.message}}))
        return 2
    print(json.dumps({"ok": True, "data": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

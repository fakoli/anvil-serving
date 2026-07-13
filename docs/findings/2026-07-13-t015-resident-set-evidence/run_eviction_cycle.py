#!/usr/bin/env python3
"""T015 eviction-cycle driver (evidence artifact).

Executes the REAL anvil_serving.serves drain path against the isolated `sim-5090`
gpu_role (eviction-sim-manifest.toml / eviction-sim-compose.yml): real docker
container stop/start via serves.cmd_up/cmd_down, real ledger admission +
plan_eviction, with the ADR-0018 router quiesce/drain/readmit legs RECORDED
through the injectable transition seam (those legs were validated LIVE against the
real router in gpu-reservations:T013). Prints a JSON transcript and the derived
per-step ledger. Zero impact on the live dark-fast resident set.
"""
import json
import os
import sys

from anvil_serving import serves

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "eviction-sim-manifest.toml")

transcript = {"steps": [], "transition_calls": []}


def record_transition(action, tier_id, timeout=None):
    transcript["transition_calls"].append(
        {"action": action, "tier_id": tier_id, "timeout": timeout, "applied": True}
    )
    return 0  # 0 == applied (matches _transition_cli success semantics)


def ledger(sv, label):
    summ = serves.reservation_summary(sv)
    role = next((r for r in summ["gpu_roles"] if r["gpu_role"] == "sim-5090"), None)
    transcript["steps"].append({"label": label, "sim-5090": role})
    return role


def main():
    sv = serves.load_manifest(MANIFEST)

    # 0. Clean slate + bring the evictable vision-sim slot up (committed).
    serves.cmd_down(sv, ["vision-sim", "comfyui-sim"])
    rc = serves.cmd_up(sv, ["vision-sim"])
    assert rc == 0, "vision-sim admission failed rc=%s" % rc
    ledger(sv, "1-vision-sim-committed")

    # 1. On-demand comfyui-sim acquisition WITHOUT --evict: must be denied
    #    (over budget, and plain admission never stops an evictable).
    rc_no_evict = serves.cmd_up(sv, ["comfyui-sim"], evict=False)
    transcript["steps"].append(
        {"label": "2-comfyui-sim-denied-without-evict", "returncode": rc_no_evict}
    )
    assert rc_no_evict == 1, "expected denial rc=1, got %s" % rc_no_evict

    # 2. On-demand comfyui-sim acquisition WITH --evict: evicts vision-sim via the
    #    drain path (recorded router legs), then starts comfyui-sim.
    rc_evict = serves.cmd_up(
        sv, ["comfyui-sim"], evict=True, drain_timeout=60.0,
        _transition=record_transition,
    )
    transcript["steps"].append(
        {"label": "3-comfyui-sim-admitted-with-evict", "returncode": rc_evict}
    )
    assert rc_evict == 0, "eviction acquisition failed rc=%s" % rc_evict
    ledger(sv, "3-after-eviction")

    # 3. Restore: release the on-demand reservation, bring vision-sim back, and
    #    record the guarded readmit that returns the tier to rotation.
    serves.cmd_down(sv, ["comfyui-sim"])
    rc_restore = serves.cmd_up(sv, ["vision-sim"])
    assert rc_restore == 0, "vision-sim restore failed rc=%s" % rc_restore
    record_transition("readmit", "vision-sim-tier")
    ledger(sv, "4-vision-sim-restored")

    # 4. Teardown the sim containers.
    serves.cmd_down(sv, ["vision-sim", "comfyui-sim"])

    print(json.dumps(transcript, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

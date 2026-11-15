"""Bounded idle release through the existing job-owned worker lifecycle.

The desktop caller freezes new gateway submissions before applying. This module
runs in that worker's lifecycle container, where its durable ownership lives.
"""
from __future__ import annotations

import json
from contextlib import redirect_stdout
import io
from pathlib import Path
import os
import sqlite3
import sys

from anvil_serving.media.comfyui import ComfyUIClient
from anvil_serving.media.lifecycle import _selected_status
from anvil_serving.paths import runtime_url


def _native_queue(status):
    port = status.get("port")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("worker port unavailable")
    return ComfyUIClient(runtime_url(f"http://127.0.0.1:{port}"), timeout=5).queue()


def _inspect(state_path, binding, status_operation, queue):
    def refuse(reason):
        return {"idle": False, "applied": False, "reason": reason}

    with sqlite3.connect(Path(state_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        row = db.execute(
            "SELECT * FROM media_lifecycle_transactions WHERE service=? "
            "AND status IN ('preparing','active','releasing')", (binding["service"],)
        ).fetchone()
        if row is None or not row["owns_instance"] or row["preexisting"]:
            return refuse("worker_not_owned")
        if row["status"] != "active":
            return refuse("worker_transition_in_progress")
        if row["manifest"] != binding["manifest"]:
            return refuse("worker_manifest_mismatch")
        job = db.execute(
            "SELECT m.id FROM media_jobs m JOIN media_lifecycle_jobs j ON j.job_id=m.id "
            "WHERE j.transaction_id=? AND m.principal=? LIMIT 1",
            (row["id"], binding["principal"]),
        ).fetchone()
        if job is None:
            return refuse("worker_principal_mismatch")
        # Warm requests need not be linked to the original cold-start lease.
        unfinished = db.execute(
            "SELECT 1 FROM media_jobs WHERE state NOT IN ('completed','failed','canceled') LIMIT 1"
        ).fetchone()
        if unfinished:
            return refuse("unfinished_media_jobs")
        ambiguous = db.execute(
            "SELECT 1 FROM media_jobs j WHERE "
            "(j.state='canceled' AND j.backend_prompt_id <> '') OR "
            "EXISTS(SELECT 1 FROM media_job_events e WHERE e.job_id=j.id AND e.reason IN "
            "('backend_submission_recovery_failed','backend_submission_outcome_unknown',"
            "'backend_submission_recovery_unavailable','backend_prompt_lost',"
            "'exclusive_running_prompt_interrupted')) LIMIT 1"
        ).fetchone()
        if ambiguous:
            return refuse("submission_outcome_unknown")
    args = {"manifest": binding["manifest"], "names": [binding["service"]]}
    status = _selected_status(status_operation(args), binding["service"])
    if (status.get("container") != binding["worker"] or status.get("running") is not True
            or status.get("health_status") is None):
        return refuse("worker_identity_or_state_mismatch")
    try:
        native = queue(status)
        if any(type(native.get(k)) is not int or native[k] < 0 for k in ("running", "pending")):
            return refuse("native_queue_unavailable")
    except Exception:
        return refuse("native_queue_unavailable")
    if native["running"] or native["pending"]:
        return refuse("native_queue_busy")
    return {"idle": True, "applied": False, "reason": "owned_worker_idle",
            "_job_id": job["id"], "_transaction_id": row["id"]}


def release(lifecycle, binding, *, apply=False, queue=None):
    result = _inspect(lifecycle.store.path, binding, lifecycle._status_operation, queue or _native_queue)
    job_id = result.pop("_job_id", None)
    transaction_id = result.pop("_transaction_id", None)
    if not apply or result["idle"] is not True:
        return result
    lookup = lifecycle._transaction_for_job
    def checked_transaction(requested_job):
        row = lookup(requested_job)
        return row if row is not None and row["id"] == transaction_id else None
    # Pin the lookup inside teardown's lock, before any ownership mutation.
    lifecycle._transaction_for_job = checked_transaction
    try:
        receipt = lifecycle.teardown(job_id, principal=binding["principal"],
                                     manifest=binding["manifest"], confirm=True, human_approved=True)
    finally:
        lifecycle._transaction_for_job = lookup
    if not receipt.applied or not receipt.owns_instance:
        return {"idle": False, "applied": False, "reason": "managed_teardown_refused"}
    args = {"manifest": binding["manifest"], "names": [binding["service"]]}
    status = _selected_status(lifecycle._status_operation(args), binding["service"])
    if status.get("running") is not False:
        return {"idle": False, "applied": False, "reason": "managed_teardown_unverified"}
    return {"idle": True, "applied": True, "reason": "owned_worker_released"}


def main():
    try:
        if len(sys.argv) != 3 or len(sys.argv[1]) > 4096 or sys.argv[2] not in {"preview", "apply"}:
            raise ValueError("invalid idle-release request")
        binding = json.loads(sys.argv[1])
        if (set(binding) != {"worker", "controller", "service", "manifest", "principal"}
                or any(not isinstance(v, str) or not 1 <= len(v) <= 128 or "\x00" in v for v in binding.values())):
            raise ValueError("invalid idle-release binding")
        if not Path(os.environ.get("ANVIL_MEDIA_STATE_DB", "")).is_file():
            raise ValueError("existing lifecycle store unavailable")
        from anvil_serving.control_plane.mcp.tools.media_worker import _lifecycle, _resource_status
        with redirect_stdout(io.StringIO()):
            if sys.argv[2] == "preview":
                result = _inspect(os.environ["ANVIL_MEDIA_STATE_DB"], binding, _resource_status, _native_queue)
                result.pop("_job_id", None)
                result.pop("_transaction_id", None)
            else:
                result = release(_lifecycle(), binding, apply=True)
    except Exception:
        result = {"idle": False, "applied": False, "reason": "worker_state_unavailable"}
    print(json.dumps(result))


if __name__ == "__main__":
    main()

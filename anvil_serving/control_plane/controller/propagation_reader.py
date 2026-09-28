"""Bounded read-only callbacks through one installed, pinned fleet profile."""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Mapping

from .propagation_job_store import ExecutionProfile, PropagationJobError


_MAX_REQUEST = 256 * 1024


class FixedFleetReader:
    """Run only the installed readback executable; requests contain no argv."""

    def __init__(self, profile: ExecutionProfile):
        if sys.platform != "linux" or not isinstance(profile, ExecutionProfile) or profile.budget_seconds > 30:
            raise PropagationJobError("reader_unavailable")
        self.profile = profile
        # ponytail: one readback child at a time; increase only with measured demand.
        self._slot = threading.Lock()

    def probe(self, profile_id: str, contract_digest: str) -> None:
        expected = {"schema": "anvil-serving.propagation-reader/v1",
                    "profile_id": profile_id, "contract_digest": contract_digest,
                    "modes": ["preview", "status", "verify", "convergence", "recovery", "reconcile"]}
        result = self._run({"schema": expected["schema"], "kind": "probe",
                            "profile_id": profile_id, "contract_digest": contract_digest})
        if result != expected:
            raise PropagationJobError("reader_unavailable")

    def _run(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if not self._slot.acquire(blocking=False):
            raise PropagationJobError("reader_unavailable")
        try:
            return self._run_owned(request)
        finally:
            self._slot.release()

    def _run_owned(self, request: Mapping[str, Any]) -> dict[str, Any]:
        try:
            raw = json.dumps(request, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode("ascii") + b"\n"
            if len(raw) > _MAX_REQUEST:
                raise ValueError()
            self.profile.verify_executable()
            process = subprocess.Popen(
                self.profile.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, cwd=self.profile.cwd,
                env=dict(self.profile.environment or {}), close_fds=True,
                start_new_session=True,
            )
            assert process.stdin is not None and process.stdout is not None
            deadline = time.monotonic() + self.profile.budget_seconds
            output = bytearray()
            offset = 0
            with selectors.DefaultSelector() as selector:
                os.set_blocking(process.stdin.fileno(), False)
                os.set_blocking(process.stdout.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE)
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError()
                    for key, _ in selector.select(min(remaining, 0.1)):
                        if key.fileobj is process.stdin:
                            try:
                                offset += os.write(process.stdin.fileno(), raw[offset:])
                            except BrokenPipeError:
                                raise ValueError() from None
                            if offset == len(raw):
                                selector.unregister(process.stdin)
                                process.stdin.close()
                        else:
                            chunk = os.read(process.stdout.fileno(), 8192)
                            if not chunk:
                                selector.unregister(process.stdout)
                            else:
                                output.extend(chunk)
                                if len(output) > self.profile.output_limit:
                                    raise ValueError()
            process.wait(timeout=max(0.001, deadline - time.monotonic()))
            if process.returncode != 0 or time.monotonic() > deadline:
                raise ValueError()
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                pass
            else:
                raise ValueError()  # A descendant outlived the observed command.
            result = json.loads(output)
            if type(result) is not dict:
                raise ValueError()
            return result
        except (OSError, ValueError, TypeError, TimeoutError, subprocess.SubprocessError, PropagationJobError):
            if "process" in locals():
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.SubprocessError:
                    pass
            raise PropagationJobError("reader_unavailable") from None
        finally:
            if "process" in locals():
                for pipe in (process.stdin, process.stdout):
                    if pipe is not None and not pipe.closed:
                        pipe.close()

    def preview(self, canonical_contract: bytes) -> dict[str, Any]:
        return self._run({"schema": "anvil-serving.propagation-reader/v1", "kind": "preview",
                          "contract": json.loads(canonical_contract)})

    def observe(self, canonical_contract: bytes, job: Mapping[str, Any], kind: str,
                verification_id: str | None) -> dict[str, Any]:
        if kind not in {"status", "verify", "convergence"}:
            raise PropagationJobError("reader_unavailable")
        return self._run({"schema": "anvil-serving.propagation-reader/v1", "kind": kind,
                          "contract": json.loads(canonical_contract),
                          "job": {key: job[key] for key in ("job_id", "operation_id", "contract_digest", "intent_id")},
                          "verification_id": verification_id})

    def recovery_evidence(self, profile_id: str, caller_id: str) -> dict[str, Any]:
        return self._run({"schema": "anvil-serving.propagation-reader/v1",
                          "kind": "recovery", "profile_id": profile_id, "caller_id": caller_id})

    def reconcile(self, job: Mapping[str, Any], result: Mapping[str, Any]) -> str:
        value = self._run({"schema": "anvil-serving.propagation-reader/v1",
                           "kind": "reconcile",
                           "job": {key: job[key] for key in ("job_id", "operation_id", "contract_digest", "intent_id")},
                           "result": {key: result[key] for key in ("outcome", "native_effects", "quiescent")}})
        if set(value) != {"state"} or value["state"] not in {"applied", "uncertain"}:
            raise PropagationJobError("reader_unavailable")
        return value["state"]

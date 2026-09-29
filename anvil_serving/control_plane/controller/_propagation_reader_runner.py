"""One-shot readback custody process; never enable subreaping in the controller."""

from __future__ import annotations

import json
import signal
import sys

from anvil_serving.connect._qualification_supervisor import Children

from .propagation_job_store import ExecutionProfile
from .propagation_reader import FixedFleetReader, _MAX_REQUEST


def main() -> int:
    def interrupted(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, interrupted)
    children = None
    try:
        raw = sys.stdin.buffer.read(_MAX_REQUEST * 2 + 1)
        if len(raw) > _MAX_REQUEST * 2:
            return 1
        value = json.loads(raw)
        if type(value) is not dict or set(value) != {"profile", "request"} or type(value["request"]) is not dict:
            return 1
        profile = ExecutionProfile.from_private_value(value["profile"])
        reader = FixedFleetReader(profile)
        children = Children()
        result = reader._run_owned(value["request"])
        empty, limited = children.wait_empty(0.1)
        if not empty or limited:
            return 1
        output = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(output) > profile.output_limit:
            return 1
        sys.stdout.buffer.write(output)
        return 0
    except BaseException:
        return 1
    finally:
        if children is not None:
            try:
                children.cleanup(2)
            finally:
                children.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""Shared process helpers for tests."""
import os
import time

import pytest


def wait_gone(pid, limit=5.0):
    """Poll until ``pid`` is gone (or a zombie awaiting reaping); fail on timeout."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            state = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[0]
        except ProcessLookupError:
            return
        except OSError:
            state = ""
        if state == "Z":
            return
        time.sleep(0.01)
    pytest.fail(f"pid {pid} survived")

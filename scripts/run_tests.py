"""Run pytest with a process-unique base temp directory.

Windows pytest sessions otherwise share ``pytest-of-USER/pytest-current`` and
can turn an entirely green run into a cleanup-time ``PermissionError`` when
several worktrees test concurrently.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if any(item == "--basetemp" or item.startswith("--basetemp=") for item in args):
        print("run_tests.py owns --basetemp; remove the explicit option", file=sys.stderr)
        return 2
    if not args:
        args = ["tests/", "-q"]
    base_temp = tempfile.mkdtemp(prefix="anvil-serving-pytest-")
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", *args, "--basetemp", base_temp]
        )
        return completed.returncode
    finally:
        shutil.rmtree(base_temp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

"""The pre-submit contract must itself be enforced.

scripts/pre-submit.sh is the single command a contributor/agent runs before
`anvil submit` or opening a PR. If it goes missing, loses its executable bit, or
stops running the two invariants it exists to guard (regenerating the CLI
reference audit and running the full suite), the contract is silently broken.
These checks keep the tool honest — and are hermetic (no subprocess, no shell).
"""

from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pre-submit.sh"


def test_pre_submit_script_exists_and_is_well_formed():
    assert SCRIPT.is_file(), "scripts/pre-submit.sh must exist"
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.startswith("#!"), "script must start with a shebang"
    assert "/sh" in text.splitlines()[0], "script must be POSIX sh"
    # It must run the two invariants it exists to guard.
    assert "scripts/audit_cli_references.py --update" in text
    assert "pytest tests/" in text
    # Regenerating and then checking the tree is dirty is the whole point.
    assert "git diff --quiet" in text
    # It must be able to fail the caller.
    assert "exit 1" in text


def test_pre_submit_script_is_executable_in_the_git_index():
    """Executable bit is tracked in git so it holds on Windows and Linux alike."""
    listing = subprocess.run(
        ["git", "ls-files", "-s", "--", "scripts/pre-submit.sh"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    assert listing, "scripts/pre-submit.sh must be tracked by git"
    mode = listing.split()[0]
    assert mode == "100755", f"script must be executable (mode 100755), got {mode}"

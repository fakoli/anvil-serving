"""Shared host-mutation guard primitives — compute -> explain -> gate -> apply
-> verify -> rollback, plus one-attempt discipline for destructive operations.

Born from the WSL starvation incident (see host.py's module docstring): the
guard pattern proved itself in four places independently — host.py (confirm +
numbered backups + refusal floors), cache_prune.py (plan/gate/apply with a
single deletion chokepoint), router_manage.py (crash-loop verify + rollback on
promote), and mcp.py (the dry_run/confirm/human_approved triple gate). This
module consolidates the reusable primitives so new mutation paths adopt the
pattern by import instead of re-derivation.

Doctrine (the parts that are policy, not just helpers):

* **Gate before apply.** Irreversible operations (``docker rm -f``, file
  overwrites without backup) require an explicit yes — a ``--yes`` flag or an
  interactive confirm. Reversible service operations may stay prompt-free but
  must verify afterward.
* **Fail closed.** When the state needed to judge safety cannot be read,
  refuse (with a --force override) rather than proceed on hope.
* **Backup before overwrite.** Any config file the operator may have edited
  gets a numbered ``.anvil.bak.N`` sibling before being rewritten.
* **One attempt, then diagnose.** A destructive/global operation is issued
  once; on failure the caller reports state and stops. Never retry-loop a
  mutation (a `wsl --shutdown` retry loop is what wedged the host). The only
  sanctioned escalation is the bounded terminate -> kill ladder in
  :func:`terminate_then_kill`.
* **Verify it STAYED applied.** A single post-apply read is not proof —
  restart policies bounce crashed containers back to 'running'. Use
  :func:`await_stable` (settle + N consecutive good samples).
"""
from __future__ import annotations

import os
import shutil
import time


# --------------------------------------------------------------------------- #
# gate
# --------------------------------------------------------------------------- #
def confirm(prompt, *, force=False, assume_yes=False, _input=input):
    """Interactive [y/N] gate. ``force``/``assume_yes`` short-circuit to True
    (--force = "I understand I'm overriding a floor"; --yes = "don't prompt").
    EOF (no TTY, e.g. piped/automation without --yes) answers **No** — the
    fail-safe direction for a mutation gate."""
    if force or assume_yes:
        return True
    try:
        return (_input(prompt + " [y/N] ") or "").strip().lower() in ("y", "yes")
    except EOFError:
        return False


# --------------------------------------------------------------------------- #
# backup / rollback
# --------------------------------------------------------------------------- #
def backups(path):
    """Existing anvil backups for ``path``, newest last (sorted by numeric suffix)."""
    d, base = os.path.dirname(path) or ".", os.path.basename(path)
    pref = base + ".anvil.bak."
    try:
        got = [f for f in os.listdir(d) if f.startswith(pref) and f[len(pref):].isdigit()]
    except FileNotFoundError:
        return []
    return [os.path.join(d, f) for f in sorted(got, key=lambda f: int(f.rsplit(".", 1)[-1]))]


def next_backup(path):
    """Next backup name, numbered from the MAX existing suffix + 1 (never the
    count) so a gap from a deleted/pruned backup can't collide with — and
    silently overwrite — an existing one."""
    nums = [int(os.path.basename(b).rsplit(".", 1)[-1]) for b in backups(path)]
    return path + ".anvil.bak.%d" % ((max(nums) + 1) if nums else 1)


def backup_file(path):
    """Copy ``path`` to its next numbered backup before an overwrite. Returns
    the backup path, or None when ``path`` does not exist (nothing to save).

    The backup is opened with mode "x" (exclusive create): if a concurrent
    process computed the same suffix between our listdir and the write, this
    raises FileExistsError instead of silently truncating their backup — the
    same fail-loud invariant host.py's inline backup always had. mtime is
    preserved so "which backup predates the incident" stays answerable from a
    directory listing."""
    if not os.path.exists(path):
        return None
    dest = next_backup(path)
    with open(path, "rb") as src, open(dest, "xb") as out:
        shutil.copyfileobj(src, out)
    shutil.copystat(path, dest)
    return dest


def latest_backup(path):
    """Newest existing backup for ``path``, or None."""
    got = backups(path)
    return got[-1] if got else None


# --------------------------------------------------------------------------- #
# verify
# --------------------------------------------------------------------------- #
def await_stable(check, *, settle=3.0, checks=4, delay=2.0, _sleep=time.sleep):
    """Verify a mutation STAYED applied: sleep ``settle`` seconds, then require
    ``checks`` CONSECUTIVE truthy results from ``check()``.

    A single post-apply read almost always misses a crash-loop: a fail-fast
    service exits within seconds and a restart policy bounces it back to
    'running' before a naive check runs (router_manage._await_running wraps
    this with its RestartCount refinement). ``check`` may return any
    truthy/falsy value; the last value is returned alongside the verdict as
    ``(ok, last)``.

    ``checks`` must be >= 1: a zero-sample "verification" would return the
    exact false positive (declared healthy without ever sampling) this
    function exists to prevent, so it fails loud instead."""
    if checks < 1:
        raise ValueError("await_stable requires checks >= 1 (got %r) — a "
                         "zero-sample verify would be a vacuous pass" % checks)
    _sleep(settle)
    last = None
    for _ in range(checks):
        last = check()
        if not last:
            return False, last
        _sleep(delay)
    return True, last


# --------------------------------------------------------------------------- #
# one-attempt destructive escalation
# --------------------------------------------------------------------------- #
def terminate_then_kill(proc, *, grace=10):
    """The canonical bounded escalation for stopping a local process: one
    ``terminate()``, wait up to ``grace`` seconds, then one ``kill()``, wait
    the same grace again. Never loops. Returns True when the process is
    reaped, False when it survived both steps (caller should diagnose, not
    retry).

    This ladder (lifted from multiplexer.Backend._cleanup) is the ONLY
    sanctioned escalation shape for destructive ops — anything stronger than
    kill is a host-level action that belongs behind host.py's confirm gates.

    Known deliberately-separate variants (Popen-handle shape doesn't fit):
    host.py's ``_kill_process`` (PowerShell by-name, locale-independent) and
    voice/serves/native.py's pid-file + process-group ladder. Keep their
    one-attempt discipline aligned with this one when touching either."""
    try:
        proc.terminate()
        try:
            proc.wait(timeout=grace)
            return True
        except Exception:
            proc.kill()
            try:
                proc.wait(timeout=grace)
                return True
            except Exception:
                return False
    except Exception:
        # terminate() raising usually means the process is already gone.
        try:
            return proc.poll() is not None
        except Exception:
            return False

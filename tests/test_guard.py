"""Tests for the shared host-mutation guard primitives (anvil_serving.guard).

Pure-stdlib fakes: no docker, no subprocess, no real sleeping.
"""
import os

import pytest

from anvil_serving import guard


# ---- confirm ------------------------------------------------------------------

def test_confirm_yes_variants():
    for answer in ("y", "Y", "yes", " YES "):
        assert guard.confirm("do it?", _input=lambda p, a=answer: a) is True


def test_confirm_default_is_no():
    assert guard.confirm("do it?", _input=lambda p: "") is False
    assert guard.confirm("do it?", _input=lambda p: "nope") is False


def test_confirm_force_and_yes_short_circuit():
    def explode(_p):
        raise AssertionError("must not prompt")
    assert guard.confirm("do it?", force=True, _input=explode) is True
    assert guard.confirm("do it?", assume_yes=True, _input=explode) is True


def test_confirm_eof_is_no():
    # No TTY (automation without --yes) must fail-safe to No.
    def eof(_p):
        raise EOFError
    assert guard.confirm("do it?", _input=eof) is False


# ---- backups ------------------------------------------------------------------

def test_backup_numbering_from_max_not_count(tmp_path):
    f = tmp_path / "conf.toml"
    f.write_text("v1", encoding="utf-8")
    # Simulate a pruned backup 1 with a surviving backup 3: next must be 4,
    # never 2 (count-based naming would collide after a gap... with 3 present
    # a count of 1 existing backup would name it .2, then a later prune/create
    # cycle can collide — max+1 cannot).
    (tmp_path / "conf.toml.anvil.bak.3").write_text("old", encoding="utf-8")
    assert guard.next_backup(str(f)).endswith(".anvil.bak.4")
    bak = guard.backup_file(str(f))
    assert bak.endswith(".anvil.bak.4")
    assert open(bak, encoding="utf-8").read() == "v1"


def test_backup_file_missing_source_is_none(tmp_path):
    assert guard.backup_file(str(tmp_path / "nope.toml")) is None


def test_backups_sorted_and_latest(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text("x", encoding="utf-8")
    for n in (2, 10, 1):
        (tmp_path / f"c.txt.anvil.bak.{n}").write_text(str(n), encoding="utf-8")
    got = guard.backups(str(f))
    assert [os.path.basename(b) for b in got] == \
        ["c.txt.anvil.bak.1", "c.txt.anvil.bak.2", "c.txt.anvil.bak.10"]
    assert guard.latest_backup(str(f)).endswith(".10")


def test_backups_missing_dir_is_empty():
    assert guard.backups("/no/such/dir/file.toml") == []


# ---- await_stable ---------------------------------------------------------------

def test_await_stable_requires_consecutive_good_samples():
    # crash on the 3rd sample -> not stable, even though the first two were good.
    seq = iter([True, True, False, True])
    ok, last = guard.await_stable(lambda: next(seq), checks=4, _sleep=lambda s: None)
    assert ok is False and last is False


def test_await_stable_passes_and_returns_last():
    ok, last = guard.await_stable(lambda: "running", checks=3, _sleep=lambda s: None)
    assert ok is True and last == "running"


def test_await_stable_sleeps_settle_then_delays():
    slept = []
    guard.await_stable(lambda: True, settle=3.0, checks=2, delay=2.0,
                       _sleep=slept.append)
    assert slept == [3.0, 2.0, 2.0]


# ---- terminate_then_kill --------------------------------------------------------

class _Proc:
    """Fake Popen: `hangs` counts how many wait() calls time out before reaping."""

    def __init__(self, hangs=0, terminate_raises=False, alive=True):
        self.hangs = hangs
        self.terminate_raises = terminate_raises
        self.alive = alive
        self.events = []

    def terminate(self):
        self.events.append("terminate")
        if self.terminate_raises:
            raise OSError("already gone")

    def kill(self):
        self.events.append("kill")

    def wait(self, timeout=None):
        self.events.append("wait")
        if self.hangs > 0:
            self.hangs -= 1
            raise TimeoutError("hung")
        self.alive = False

    def poll(self):
        return None if self.alive else 0


def test_terminate_then_kill_clean_exit_never_escalates():
    p = _Proc()
    assert guard.terminate_then_kill(p) is True
    assert "kill" not in p.events


def test_terminate_then_kill_escalates_exactly_once():
    p = _Proc(hangs=1)  # terminate's wait times out; kill's wait reaps
    assert guard.terminate_then_kill(p) is True
    assert p.events.count("terminate") == 1
    assert p.events.count("kill") == 1  # ONE escalation, never a loop


def test_terminate_then_kill_survivor_reports_false():
    p = _Proc(hangs=2)  # both waits hang -> caller must diagnose, not retry
    assert guard.terminate_then_kill(p) is False
    assert p.events.count("kill") == 1


def test_terminate_then_kill_already_dead_process():
    p = _Proc(terminate_raises=True, alive=False)
    assert guard.terminate_then_kill(p) is True

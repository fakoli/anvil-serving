"""Tests for the shared host-mutation guard primitives (anvil_serving.guard).

Pure-stdlib fakes: no docker, no subprocess, no real sleeping.
"""


from anvil_serving import guard


# ---- capacity policy ----------------------------------------------------------

def test_model_free_experimental_override_requires_permission_and_flag():
    flag_only = guard.evaluate_capacity_policy(
        host_id="mini",
        workload="experimental-model",
        capacity_policy="model-free",
        allow_model_workloads=False,
        allow_experimental_model_workloads=False,
        experimental_model_workload=True,
    )
    permission_only = guard.evaluate_capacity_policy(
        host_id="mini",
        workload="experimental-model",
        capacity_policy="mini-experimental",
        allow_model_workloads=False,
        allow_experimental_model_workloads=True,
    )

    assert flag_only.allowed is False
    assert flag_only.experimental_model_workload_override is False
    assert permission_only.allowed is False
    assert permission_only.experimental_model_workload_permitted is True
    assert "--experimental-model-workload" in permission_only.reason


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


def test_confirmation_scope_is_observable_and_restored():
    assert guard.confirmation_authorized() is False
    with guard.confirmation_scope(True):
        assert guard.confirmation_authorized() is True
    assert guard.confirmation_authorized() is False


# ---- backups ------------------------------------------------------------------


# ---- await_stable ---------------------------------------------------------------


def test_await_stable_passes_and_returns_last():
    ok, last = guard.await_stable(lambda: "running", checks=3, _sleep=lambda s: None)
    assert ok is True and last == "running"


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

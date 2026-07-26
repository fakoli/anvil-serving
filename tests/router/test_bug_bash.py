"""Regression probes that still apply to the thin gateway."""
from __future__ import annotations

from anvil_serving.router.decision_log import DecisionLog


def test_decision_log_default_is_bounded():
    log = DecisionLog()
    assert log._records.maxlen is not None and log._records.maxlen > 0


def test_decision_log_rejects_nonpositive_cap():
    for cap in (0, -1):
        try:
            DecisionLog(max_records=cap)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid capacity was accepted")

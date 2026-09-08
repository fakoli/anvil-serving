import math

import pytest

from anvil_serving.observability.dashboard.contracts import (
    ObservatoryError, digest, present_metric, preview_is_current, strict_json, validate_values,
)


def test_zero_missing_stale_are_distinct():
    assert present_metric(0, source_status="fresh", unit="requests")["value"] == 0
    assert present_metric(None, source_status="fresh", unit="requests")["status"] == "unavailable"
    stale = present_metric(42, source_status="stale", unit="percent")
    assert stale["value"] is None and stale["last_known_value"] == 42
    assert present_metric(5, source_status="unsupported", unit="seconds")["last_known_value"] is None


@pytest.mark.parametrize("value", [True, False, math.nan, math.inf, -math.inf, "0", {}, []])
def test_bad_metric_numbers(value):
    with pytest.raises(ValueError):
        present_metric(value, source_status="fresh", unit="requests")


@pytest.mark.parametrize("status,unit", [("healthy", "requests"), (None, "requests"), ("fresh", ""), ("fresh", "<script>"), ("fresh", None)])
def test_bad_status_units(status, unit):
    with pytest.raises(ValueError):
        present_metric(0, source_status=status, unit=unit)


def baseline():
    return {"host_id": "host-fixture-a", "resource_id": "serve-fixture-a", "action_id": "configuration.apply",
            "baseline_digest": "a" * 64, "candidate_digest": "b" * 64, "policy_digest": "c" * 64}


@pytest.mark.parametrize("field", list(baseline()))
def test_preview_checks_every_identity(field):
    current = baseline()
    preview = {**current, "expires_at_epoch_seconds": 1120}
    assert preview_is_current(preview, current, now_epoch_seconds=1000)
    assert not preview_is_current(preview, {**current, field: "different"}, now_epoch_seconds=1000)
    assert not preview_is_current(preview, current, now_epoch_seconds=1120)
    assert not preview_is_current({}, current, now_epoch_seconds=1000)


@pytest.mark.parametrize("expiry", [None, True, math.inf, math.nan, "1120", 0])
def test_bad_expiry(expiry):
    assert not preview_is_current({**baseline(), "expires_at_epoch_seconds": expiry}, baseline(), now_epoch_seconds=1000)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}', b'{'])
def test_unambiguous_finite_json(raw):
    with pytest.raises(ObservatoryError):
        strict_json(raw)


def test_semantic_digest_and_complete_candidate():
    assert digest({"a": 0, "b": False}) == digest({"b": False, "a": 0})
    settings = [{"setting_id": "max_output", "label": "Maximum output", "support": "supported", "value_type": "integer", "configured": 64, "constraints": {"minimum": 1, "maximum": 128}}]
    assert validate_values({}, settings) == {"max_output": 64}
    assert validate_values({"max_output": 32}, settings) == {"max_output": 32}
    for bad in ({"max_output": True}, {"max_output": 129}, {"shell": "x"}):
        with pytest.raises(ObservatoryError):
            validate_values(bad, settings)


import pytest

from anvil_serving.observability.dashboard.contracts import (
    ObservatoryError, digest, validate_values,
)


def baseline():
    return {"host_id": "host-fixture-a", "resource_id": "serve-fixture-a", "action_id": "configuration.apply",
            "baseline_digest": "a" * 64, "candidate_digest": "b" * 64, "policy_digest": "c" * 64}


def test_semantic_digest_and_complete_candidate():
    assert digest({"a": 0, "b": False}) == digest({"b": False, "a": 0})
    settings = [{"setting_id": "max_output", "label": "Maximum output", "support": "supported", "value_type": "integer", "configured": 64, "constraints": {"minimum": 1, "maximum": 128}}]
    assert validate_values({}, settings) == {"max_output": 64}
    assert validate_values({"max_output": 32}, settings) == {"max_output": 32}
    for bad in ({"max_output": True}, {"max_output": 129}, {"shell": "x"}):
        with pytest.raises(ObservatoryError):
            validate_values(bad, settings)

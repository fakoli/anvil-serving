"""Direct capability routing behavior retained by the thin gateway."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from anvil_serving.router.admission import TierAdmission
from tests.router.helpers import StaticBackend
from anvil_serving.router.config import load
from anvil_serving.router.internal import InternalRequest, Message, NoAvailableTierError
from anvil_serving.router.request_control import (
    RequestControl,
    RequestDeadlineExceeded,
)
from anvil_serving.router.serve import RoutingBackend


_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "example.toml"


def _request(model="llm.primary", *, raw=None, content="hello"):
    return InternalRequest(model=model, messages=(Message("user", content),), raw=raw or {})


def _routing(*, backends=None, admission=None, max_output_tokens=None,
             upstream_context_admission=False):
    config = load(_CONFIG)
    if max_output_tokens is not None or upstream_context_admission:
        config = replace(
            config,
            tiers=tuple(
                replace(
                    tier,
                    max_output_tokens=(
                        max_output_tokens
                        if max_output_tokens is not None
                        else tier.max_output_tokens
                    ),
                    context_admission=(
                        "upstream"
                        if upstream_context_admission
                        else tier.context_admission
                    ),
                )
                if tier.id == "primary-local"
                else tier
                for tier in config.tiers
            ),
        )
    return RoutingBackend(
        config,
        backends or {"primary-local": StaticBackend(["heavy"]), "omni-local": StaticBackend(["omni"])},
        admission=admission,
    )


def test_upstream_context_admission_delegates_exact_token_count_without_fallback():
    seen = []

    class CapturingBackend:
        def generate(self, request):
            seen.append(request.model)
            return iter(("accepted",))

    routing = _routing(
        backends={
            "primary-local": CapturingBackend(),
            "omni-local": StaticBackend(["wrong-tier"]),
        },
        upstream_context_admission=True,
    )

    assert list(routing.generate(_request(content="x " * 262_145))) == ["accepted"]
    assert seen == ["llm.primary"]
    assert routing._decision_log.records[-1].served_tier == "primary-local"


def test_quiesced_target_is_unavailable_without_substitution():
    admission = TierAdmission(("primary-local", "omni-local"))
    admission.quiesce("primary-local", "test")
    routing = _routing(admission=admission)

    with pytest.raises(NoAvailableTierError) as error:
        routing.generate(_request())

    assert error.value.kind == "unavailable"
    assert routing._decision_log.records[-1].attempts[0].reason == "quiesced"


def test_request_control_enforces_named_admission_startup_and_idle_deadlines():
    now = [10.0]
    control = RequestControl(
        deadline_monotonic=100.0,
        admission_timeout_s=2,
        startup_timeout_s=3,
        idle_timeout_s=4,
        clock=lambda: now[0],
    )
    control.begin_admission_wait()
    now[0] = 12.0
    with pytest.raises(RequestDeadlineExceeded, match="admission timeout") as error:
        control.check_admission()
    assert error.value.code == "admission_timeout"

    control = RequestControl(deadline_monotonic=100.0, startup_timeout_s=3, clock=lambda: now[0])
    control.start_upstream()
    now[0] = 15.0
    with pytest.raises(RequestDeadlineExceeded, match="startup timeout"):
        control.check_upstream()

    control = RequestControl(deadline_monotonic=100.0, idle_timeout_s=4, clock=lambda: now[0])
    control.start_upstream()
    control.note_activity("streaming")
    control.note_activity(estimated_input_tokens=11, cache_read_input_tokens=3)
    assert control.snapshot()["estimated_input_tokens"] == 11
    assert control.snapshot()["cache_read_input_tokens"] == 3
    now[0] = 19.0
    with pytest.raises(RequestDeadlineExceeded, match="idle timeout"):
        control.check_upstream()

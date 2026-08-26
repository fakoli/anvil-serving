"""Direct capability routing behavior retained by the thin gateway."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from anvil_serving.router.admission import TierAdmission
from tests.router.helpers import StaticBackend
from anvil_serving.router.config import load
from anvil_serving.router.internal import InternalRequest, Message, NoAvailableTierError
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


def test_alias_resolves_once_and_never_falls_back_to_another_tier():
    routing = _routing(backends={"omni-local": StaticBackend(["wrong-tier"])})

    with pytest.raises(NoAvailableTierError) as error:
        routing.generate(_request("llm.primary"))

    assert error.value.kind == "unbound"
    record = routing._decision_log.records[-1]
    assert record.requested_tier == "primary-local"
    assert record.attempts[0].reason == "backend_unbound"


def test_direct_route_enforces_context_before_relaying():
    routing = _routing()

    with pytest.raises(NoAvailableTierError) as error:
        routing.generate(_request(content="x " * 262_145))

    assert error.value.kind == "over_context"
    assert routing._decision_log.records[-1].attempts[0].reason == "over_context"


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


def test_client_disconnect_records_metadata_without_content():
    def stream(_request):
        yield "first"
        yield "second"

    class StreamingBackend:
        generate = staticmethod(stream)

    routing = _routing(backends={"primary-local": StreamingBackend(), "omni-local": StaticBackend(["omni"])})
    iterator = routing.generate(_request())
    assert next(iterator) == "first"
    iterator.close()

    record = routing._decision_log.records[-1]
    assert record.served_tier is None
    assert record.attempts[0].reason == "client_disconnected"
    assert "first" not in repr(record)


@pytest.mark.parametrize(
    "wire_field",
    ["max_tokens", "max_completion_tokens", "max_output_tokens"],
)
def test_tier_output_cap_overrides_larger_client_budget_before_relay(wire_field):
    seen = []

    class CapturingBackend:
        def generate(self, request):
            seen.append(request.max_tokens)
            return iter(("ok",))

    request = _request(raw={wire_field: 32768})
    request.max_tokens = 32768
    routing = _routing(
        backends={
            "primary-local": CapturingBackend(),
            "omni-local": StaticBackend(["omni"]),
        },
        max_output_tokens=5120,
    )

    assert list(routing.generate(request)) == ["ok"]
    assert seen == [5120]
    assert request.raw[wire_field] == 5120
    assert request.raw["_anvil_output_clamp"] == {
        "requested": 32768,
        "applied": 5120,
    }
    assert (
        routing._decision_log.records[-1].attempts[0].reason
        == "served_output_clamped"
    )


def test_tier_output_cap_supplies_missing_budget_without_false_warning():
    seen = []

    class CapturingBackend:
        def generate(self, request):
            seen.append(request.max_tokens)
            return iter(("ok",))

    request = _request()
    routing = _routing(
        backends={
            "primary-local": CapturingBackend(),
            "omni-local": StaticBackend(["omni"]),
        },
        max_output_tokens=5120,
    )

    assert list(routing.generate(request)) == ["ok"]
    assert seen == [5120]
    assert "_anvil_output_clamp" not in request.raw
    assert routing._decision_log.records[-1].attempts[0].reason == "served"

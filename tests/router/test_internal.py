"""Direct dispatch errors retain only model, tier, and failure kind."""
from __future__ import annotations

import pytest

from anvil_serving.router.internal import NoAvailableTierError


@pytest.mark.parametrize(
    ("kind", "fragment"),
    [
        ("unknown_model", "not configured"),
        ("over_context", "context window"),
        ("unsupported_tools", "does not support tools"),
        ("unavailable", "not ready"),
        ("unbound", "no bound backend"),
    ],
)
def test_direct_dispatch_error_is_specific_and_metadata_only(kind, fragment):
    err = NoAvailableTierError("llm.primary", ["primary-local"], kind=kind)

    assert err.kind == kind
    assert err.model == "llm.primary"
    assert err.candidates == ("primary-local",)
    assert fragment in str(err)
    assert "work_class" not in str(err)
    assert "fallback" not in str(err)

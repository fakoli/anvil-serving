from __future__ import annotations

import pytest

from scripts.voice import mini_validation


@pytest.mark.parametrize(
    "hostname",
    [
        "Fakoli Mini",
        "fakoli-mini",
        "fakoli-mini-2",
        "FAKOLI.MINI.local",
        "mini-host.example",
        "mini_host_2",
    ],
)
def test_default_target_host_pattern_matches_documented_mini_labels(hostname):
    assert mini_validation.host_matches_target(
        hostname,
        mini_validation.DEFAULT_TARGET_HOST_PATTERN,
    )


def test_default_target_host_pattern_rejects_non_mini_host():
    assert not mini_validation.host_matches_target(
        "fakoli-dark",
        mini_validation.DEFAULT_TARGET_HOST_PATTERN,
    )

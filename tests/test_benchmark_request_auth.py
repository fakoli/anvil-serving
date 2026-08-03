"""Authentication normalization for benchmark endpoint requests."""

import pytest

from anvil_serving.benchmarking.requests import resolve_api_key


def test_resolve_api_key_strips_crlf_and_edge_whitespace(monkeypatch):
    monkeypatch.setenv("BENCHMARK_TOKEN", "  value\r")

    assert resolve_api_key("BENCHMARK_TOKEN") == "value"


def test_resolve_api_key_rejects_whitespace_only(monkeypatch):
    monkeypatch.setenv("BENCHMARK_TOKEN", "\r")

    with pytest.raises(ValueError):
        resolve_api_key("BENCHMARK_TOKEN")

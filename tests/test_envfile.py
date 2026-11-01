"""Tests for the shared durable-secret dotenv fallback (ADR-0033)."""


import pytest


@pytest.fixture
def env_homes(tmp_path, monkeypatch):
    """Point the config home and the user home at isolated tmp directories."""
    config_home = tmp_path / "anvil-home"
    user_home = tmp_path / "user-home"
    config_home.mkdir()
    user_home.mkdir()
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(config_home))
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("USERPROFILE", str(user_home))
    monkeypatch.delenv("EXAMPLE_TOKEN", raising=False)
    return config_home, user_home

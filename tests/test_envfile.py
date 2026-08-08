"""Tests for the shared durable-secret dotenv fallback (ADR-0033)."""

import os

import pytest

from anvil_serving import envfile


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


def test_read_dotenv_parses_quotes_comments_and_invalid_names(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(
            [
                "# comment",
                "PLAIN=value",
                "QUOTED='quoted value'",
                'DOUBLE="double value"',
                "TRAILING=value # trailing comment",
                "not a line",
                "9BAD=skipped",
            ]
        ),
        encoding="utf-8",
    )
    values = envfile.read_dotenv(str(path))
    assert values == {
        "PLAIN": "value",
        "QUOTED": "quoted value",
        "DOUBLE": "double value",
        "TRAILING": "value",
    }


def test_read_dotenv_missing_file_returns_empty(tmp_path):
    assert envfile.read_dotenv(str(tmp_path / "absent.env")) == {}


def test_resolve_precedence_shell_then_config_home_then_user_home(env_homes, monkeypatch):
    config_home, user_home = env_homes
    (config_home / ".env").write_text("EXAMPLE_TOKEN=from-config\n", encoding="utf-8")
    (user_home / ".env").write_text("EXAMPLE_TOKEN=from-home\n", encoding="utf-8")

    value, source = envfile.resolve_env_value("EXAMPLE_TOKEN")
    assert value == "from-config"
    assert source == os.path.join(str(config_home), ".env")

    monkeypatch.setenv("EXAMPLE_TOKEN", "from-shell")
    value, source = envfile.resolve_env_value("EXAMPLE_TOKEN")
    assert (value, source) == ("from-shell", "env")


def test_resolve_falls_back_to_user_home(env_homes):
    _config_home, user_home = env_homes
    (user_home / ".env").write_text("EXAMPLE_TOKEN=from-home\n", encoding="utf-8")
    value, source = envfile.resolve_env_value("EXAMPLE_TOKEN")
    assert value == "from-home"
    assert source == os.path.join(str(user_home), ".env")


def test_resolve_unset_everywhere_returns_none(env_homes):
    assert envfile.resolve_env_value("EXAMPLE_TOKEN") == (None, "")


def test_explicit_env_mapping_is_hermetic(env_homes):
    config_home, _user_home = env_homes
    (config_home / ".env").write_text("EXAMPLE_TOKEN=from-config\n", encoding="utf-8")
    # An injected mapping must never fall through to files.
    assert envfile.resolve_env_value("EXAMPLE_TOKEN", env={}) == (None, "")
    assert envfile.resolve_env_value("EXAMPLE_TOKEN", env={"EXAMPLE_TOKEN": "x"}) == (
        "x",
        "env",
    )


def test_invalid_name_never_resolves(env_homes):
    assert envfile.resolve_env_value("not a name") == (None, "")
    assert envfile.resolve_env_value("") == (None, "")


def test_env_sources_names_every_location(env_homes):
    config_home, user_home = env_homes
    sources = envfile.env_sources("EXAMPLE_TOKEN")
    assert sources[0] == "environment variable EXAMPLE_TOKEN"
    assert os.path.join(str(config_home), ".env") in sources
    assert os.path.join(str(user_home), ".env") in sources

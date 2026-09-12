"""Explicit request budgets and credential-derived admission configuration."""
import math

import pytest

from anvil_serving.router.config import ConfigError, load_server_config
from anvil_serving.router.front_door_runtime import ClientAdmission


@pytest.mark.parametrize("field", ["admission_timeout_s", "startup_timeout_s",
    "idle_timeout_s", "total_timeout_s", "heartbeat_interval_s"])
@pytest.mark.parametrize("value", ["true", "0", "-1", "nan", "inf", "86401", '"15"'])
def test_deadlines_reject_unbounded_or_malformed(tmp_path, field, value):
    config = tmp_path / "router.toml"
    config.write_text(f"[server]\n{field} = {value}\n")
    with pytest.raises(ConfigError):
        load_server_config(str(config))


def test_runtime_defaults_and_explicit_budgets(tmp_path):
    config = tmp_path / "router.toml"
    config.write_text('[server]\nauth_env = "ROUTER_TOKEN"\nadmission_timeout_s = 0.2\n'
        'client_limits = { pi = 2, voice = 1, _legacy = 4 }\n')
    parsed = load_server_config(str(config))
    assert parsed.admission_timeout_s == 0.2
    assert math.isfinite(parsed.total_timeout_s)
    limits = ClientAdmission(parsed.client_limits)
    assert limits.acquire("pi") and limits.acquire("pi")
    assert not limits.acquire("pi")
    assert limits.acquire("voice")
    limits.release("pi")
    assert limits.acquire("pi")


@pytest.mark.parametrize("limits", ['{ pi = true }', '{ pi = 0 }', '{ pi = 1025 }',
    '{ "bad client" = 1 }', '"pi"'])
def test_invalid_client_limits(tmp_path, limits):
    config = tmp_path / "router.toml"
    config.write_text(f'[server]\nauth_env = "ROUTER_TOKEN"\nclient_limits = {limits}\n')
    with pytest.raises(ConfigError):
        load_server_config(str(config))

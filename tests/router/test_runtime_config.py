"""Explicit request budgets and credential-derived admission configuration."""

import pytest

from anvil_serving.router.config import ConfigError, load_server_config


# All deadlines use the same validator. Cover its value classes once, then
# verify that each remaining field reaches it.
@pytest.mark.parametrize("field,value", [
    ("admission_timeout_s", value)
    for value in ["true", "0", "-1", "nan", "inf", "86401", '"15"']
] + [
    (field, "0") for field in ["startup_timeout_s", "idle_timeout_s",
                              "total_timeout_s", "heartbeat_interval_s"]
])
def test_deadlines_reject_unbounded_or_malformed(tmp_path, field, value):
    config = tmp_path / "router.toml"
    config.write_text(f"[server]\n{field} = {value}\n")
    with pytest.raises(ConfigError):
        load_server_config(str(config))

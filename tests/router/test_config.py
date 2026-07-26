"""Direct-only router configuration contract."""
from __future__ import annotations

from pathlib import Path

import pytest

from anvil_serving.router.config import ConfigError, PRIVACY_LOCAL, load


_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE = _ROOT / "configs" / "example.toml"


def _write(tmp_path, router: str) -> Path:
    path = tmp_path / "router.toml"
    path.write_text(router, encoding="utf-8")
    return path


_ONE_TIER = """
[router]
[[router.tiers]]
id = "primary"
base_url = "http://127.0.0.1:30000/v1"
model = "primary-model"
dialect = "openai"
context_limit = 4096
privacy = "local"
tool_support = true
auth_env = "ANVIL_PRIMARY_KEY"
[router.model_routes]
llm.primary = "primary"
"""


def test_example_declares_a_complete_local_direct_route_table():
    config = load(_EXAMPLE)

    assert set(config.model_routes) == {"llm.primary", "llm.voice"}
    assert {config.route_tier(alias).id for alias in config.model_routes} == {
        "heavy-local", "fast-local",
    }
    assert all(tier.privacy == PRIVACY_LOCAL for tier in config.tiers)


def test_alias_lookup_is_case_and_whitespace_normalized(tmp_path):
    config = load(_write(tmp_path, _ONE_TIER))

    assert config.route_tier(" LLM.PRIMARY ").id == "primary"
    assert config.route_tier("missing") is None


def test_missing_model_routes_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="model_routes"):
        load(_write(tmp_path, _ONE_TIER.replace("[router.model_routes]\nllm.primary = \"primary\"\n", "")))


def test_all_chat_tiers_must_be_addressable(tmp_path):
    two_tier = _ONE_TIER.replace(
        "[router.model_routes]",
        """[[router.tiers]]
id = "secondary"
base_url = "http://127.0.0.1:30001/v1"
model = "secondary-model"
dialect = "openai"
context_limit = 4096
privacy = "local"
tool_support = true
auth_env = "ANVIL_SECONDARY_KEY"
[router.model_routes]""",
    )

    with pytest.raises(ConfigError, match="unaddressable"):
        load(_write(tmp_path, two_tier))


def test_model_route_cannot_target_unknown_or_cloud_tier(tmp_path):
    unknown = _ONE_TIER.replace('llm.primary = "primary"', 'llm.primary = "missing"')
    with pytest.raises(ConfigError, match="unknown tier"):
        load(_write(tmp_path, unknown))

    cloud = _ONE_TIER.replace('privacy = "local"', 'privacy = "cloud"')
    with pytest.raises(ConfigError, match="privacy.*not in"):
        load(_write(tmp_path, cloud))


def test_duplicate_normalized_route_alias_is_rejected(tmp_path):
    duplicate = _ONE_TIER + '"LLM.PRIMARY" = "primary"\n'

    with pytest.raises(ConfigError, match="duplicate model route alias"):
        load(_write(tmp_path, duplicate))


@pytest.mark.parametrize(
    "legacy_field",
    [
        'mapping_version = 1\n',
        'profile_path = "/etc/anvil/profile.json"\n',
        'metered_cloud = false\n',
        'verify_local_min = 0.8\n',
    ],
)
def test_legacy_router_fields_are_rejected(tmp_path, legacy_field):
    body = _ONE_TIER.replace("[router]\n", "[router]\n" + legacy_field)

    with pytest.raises(ConfigError, match=r"\[router\] contains unknown field"):
        load(_write(tmp_path, body))


def test_legacy_tier_fields_are_rejected(tmp_path):
    body = _ONE_TIER.replace(
        'id = "primary"\n',
        'id = "primary"\npriority = 1\nwork_classes = ["coding"]\n',
    )

    with pytest.raises(ConfigError, match="tier entry contains unknown field"):
        load(_write(tmp_path, body))


def test_readiness_controls_and_identity_parse(tmp_path):
    body = _ONE_TIER.replace(
        "[router]\n",
        """[router]
availability_probe_interval = 2
availability_probe_timeout = 0.5
""",
    ).replace(
        "tool_support = true",
        '''tool_support = true
model_identity = true
health_path = "/health"''',
    )
    config = load(_write(tmp_path, body))

    tier = config.tier("primary")
    assert tier.model_identity is True
    assert tier.health_path == "/health"
    assert config.availability_probe_interval == 2.0
    assert config.availability_probe_timeout == 0.5


def test_purpose_model_routes_remain_independent_of_chat_aliases(tmp_path):
    config = load(_write(tmp_path, _ONE_TIER + """
[[router.purpose_models]]
id = "embeddings"
kind = "embedding"
model = "embed-model"
base_url = "http://127.0.0.1:30005/v1"
"""))

    assert [(model.kind, model.model) for model in config.purpose_models] == [("embedding", "embed-model")]
    assert set(config.model_routes) == {"llm.primary"}


def test_audio_route_requires_safe_dark_owned_upstream_and_tts_sample_rate(tmp_path):
    config = load(_write(tmp_path, _ONE_TIER + """
[[router.audio_routes]]
id = "voice-tts"
purpose = "tts"
model = "tts-model"
base_url = "http://127.0.0.1:30111"
source_sample_rate = 24000
"""))

    assert config.audio_routes[0].id == "voice-tts"

    invalid = _ONE_TIER + """
[[router.audio_routes]]
id = "voice-tts"
purpose = "tts"
model = "tts-model"
base_url = "http://localhost:30111"
source_sample_rate = 24000
"""
    with pytest.raises(ConfigError, match="never localhost"):
        load(_write(tmp_path, invalid))

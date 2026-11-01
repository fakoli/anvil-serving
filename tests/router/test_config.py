"""Direct and qualified replica router configuration contract."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from anvil_serving.router.config import (
    ConfigError,
    load,
    load_server_config,
)


_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE = _ROOT / "configs" / "example.toml"


def _write(tmp_path, router: str) -> Path:
    for declared, name in (('/synthetic/private/keys.sqlite3', 'keys.sqlite3'),
                           ('/synthetic/private/policy.toml', 'policy.toml'),
                           ('/synthetic/protected/signer', 'signer')):
        router = router.replace(json.dumps(declared), json.dumps(str(tmp_path / name)))
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

_WEBUI_SERVER = """
[server]
auth_env = "SYNTHETIC_ROUTER_AUTH"
api_keys_path = "/synthetic/private/keys.sqlite3"
authorization_policy_path = "/synthetic/private/policy.toml"
[[server.webui_identity]]
credential_id = "webui_service"
credential_kind = "device_key"
instance = "webui:synthetic"
signer_env = "SYNTHETIC_WEBUI_SIGNER"
"""


@pytest.mark.parametrize("before,after", [
    ('credential_id = "webui_service"', 'credential_id = "_legacy"'),
    ('credential_id = "webui_service"', 'credential_id = "secret marker"'),
    ('credential_kind = "device_key"', 'credential_kind = "human"'),
    ('instance = "webui:synthetic"', 'instance = ""'),
    ('instance = "webui:synthetic"', 'instance = true'),
    ('signer_env = "SYNTHETIC_WEBUI_SIGNER"', ''),
    ('signer_env = "SYNTHETIC_WEBUI_SIGNER"', 'signer_env = "secret marker"'),
    ('signer_env = "SYNTHETIC_WEBUI_SIGNER"', 'signer_env = 32'),
    ('signer_env = "SYNTHETIC_WEBUI_SIGNER"', 'signer_file = "relative"'),
    ('signer_env = "SYNTHETIC_WEBUI_SIGNER"', 'signer_file = "/synthetic/file"\nsigner_env = "SYNTHETIC_WEBUI_SIGNER"'),
    ('signer_env = "SYNTHETIC_WEBUI_SIGNER"', 'signer_env = "SYNTHETIC_ROUTER_AUTH"'),
    ('signer_env = "SYNTHETIC_WEBUI_SIGNER"', 'signer_env = "AKIAABCDEFGHIJKLMNOP"'),
    ('signer_env = "SYNTHETIC_WEBUI_SIGNER"', 'signer_env = "SYNTHETIC_WEBUI_SIGNER"\nissuer = "secret marker"'),
    ('signer_env = "SYNTHETIC_WEBUI_SIGNER"', 'signer_env = "SYNTHETIC_WEBUI_SIGNER"\nrequire_user = false'),
    ('signer_env = "SYNTHETIC_WEBUI_SIGNER"', 'signer_env = "SYNTHETIC_WEBUI_SIGNER"\nclock_skew_seconds = -1'),
    ('signer_env = "SYNTHETIC_WEBUI_SIGNER"', 'signer_env = "SYNTHETIC_WEBUI_SIGNER"\nclock_skew_seconds = 31'),
    ('signer_env = "SYNTHETIC_WEBUI_SIGNER"', 'signer_env = "SYNTHETIC_WEBUI_SIGNER"\nclock_skew_seconds = true'),
    ('signer_env = "SYNTHETIC_WEBUI_SIGNER"', 'signer_env = "SYNTHETIC_WEBUI_SIGNER"\nclock_skew_seconds = 1.0'),
    ('signer_env = "SYNTHETIC_WEBUI_SIGNER"', 'signer_env = "SYNTHETIC_WEBUI_SIGNER"\nsecret_marker = "private"'),
    ('api_keys_path = "/synthetic/private/keys.sqlite3"', ''),
])
def test_webui_profile_bad_configuration_is_closed_and_redacted(tmp_path, before, after):
    with pytest.raises(ConfigError) as caught:
        load_server_config(_write(tmp_path, _WEBUI_SERVER.replace(before, after)))
    assert str(caught.value) == "invalid [server].webui_identity profile"


def test_webui_scope_profile_requires_existing_authorization_config(tmp_path):
    body = _WEBUI_SERVER.replace('credential_kind = "device_key"', 'credential_kind = "configured_scope"')
    assert load_server_config(_write(tmp_path, body)).webui_identity[0].credential_kind == "configured_scope"
    with pytest.raises(ConfigError, match=r"invalid \[server\].webui_identity profile"):
        load_server_config(_write(tmp_path, body.replace('authorization_policy_path = "/synthetic/private/policy.toml"', '')))


def test_webui_profile_list_count_and_disabled_auth_fail_closed(tmp_path):
    repeated = _WEBUI_SERVER + ('\n[[server.webui_identity]]' +
                               _WEBUI_SERVER.split('[[server.webui_identity]]')[1]) * 32
    with pytest.raises(ConfigError, match=r"invalid \[server\].webui_identity profile"):
        load_server_config(_write(tmp_path, repeated))
    with pytest.raises(ConfigError, match=r"invalid \[server\].webui_identity profile"):
        body = _WEBUI_SERVER.replace('auth_env = "SYNTHETIC_ROUTER_AUTH"', '').replace(
            'api_keys_path = "/synthetic/private/keys.sqlite3"', '').replace(
            'authorization_policy_path = "/synthetic/private/policy.toml"', '')
        load_server_config(_write(tmp_path, body))

_REPLICA_TIER = """
[router]
[[router.tiers]]
id = "primary"
model = "primary-model"
dialect = "openai"
context_limit = 4096
privacy = "local"
tool_support = true
auth_env = "ANVIL_PRIMARY_KEY"
health_path = "/health"
model_identity = true
replicas = [
  { id = "member-a", base_url = "http://127.0.0.1:30000/v1", host_id = "host-a", resource_id = "gpu-a", qualification_ref = "qualification:primary-a" },
  { id = "member-b", base_url = "http://127.0.0.1:30001/v1", host_id = "host-a", resource_id = "gpu-b", qualification_ref = "qualification:primary-b" },
]
replica_identity = { model_revision = "revision-1", engine_version = "engine-1.0", image_digest = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", config_fingerprint = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" }
[router.model_routes]
llm.primary = "primary"
"""


def test_model_route_cannot_target_unknown_or_cloud_tier(tmp_path):
    unknown = _ONE_TIER.replace('llm.primary = "primary"', 'llm.primary = "missing"')
    with pytest.raises(ConfigError, match="unknown tier"):
        load(_write(tmp_path, unknown))

    cloud = _ONE_TIER.replace('privacy = "local"', 'privacy = "cloud"')
    with pytest.raises(ConfigError, match="privacy.*not in"):
        load(_write(tmp_path, cloud))


def test_replica_unknown_member_keys_and_shared_identity_requirements(tmp_path):
    unknown = _REPLICA_TIER.replace(
        'qualification_ref = "qualification:primary-a"',
        'qualification_ref = "qualification:primary-a", unexpected_ceiling = 1',
    )
    with pytest.raises(ConfigError, match="replica member contains unknown"):
        load(_write(tmp_path, unknown))

    unknown_identity = _REPLICA_TIER.replace(
        'config_fingerprint = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"',
        'config_fingerprint = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", other = "no"',
    )
    with pytest.raises(ConfigError, match="replica_identity contains unknown"):
        load(_write(tmp_path, unknown_identity))

    for old, new, message in (
        ('model_identity = true\n', '', "replicas require model_identity"),
        ('health_path = "/health"\n', '', "replicas require health_path"),
        ('model = "primary-model"\n', '', "replicas require a non-empty model"),
    ):
        with pytest.raises(ConfigError, match=message):
            load(_write(tmp_path, _REPLICA_TIER.replace(old, new)))

    upstream = _REPLICA_TIER.replace('model_identity = true', 'metadata_source = "upstream"')
    with pytest.raises(ConfigError, match="replicas require metadata_source='configured'"):
        load(_write(tmp_path, upstream))


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

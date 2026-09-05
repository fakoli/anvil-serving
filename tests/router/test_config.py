"""Direct and qualified replica router configuration contract."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
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


def test_example_declares_a_complete_local_direct_route_table():
    config = load(_EXAMPLE)

    assert set(config.model_routes) == {
        "llm.primary",
        "llm.voice",
        "vision.ocr",
        "vision.general",
    }
    assert {config.route_tier(alias).id for alias in config.model_routes} == {
        "primary-local",
        "omni-local",
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

    with pytest.raises(ConfigError, match="duplicate capability alias"):
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


def test_replica_tier_loads_immutable_member_and_declared_identity(tmp_path):
    tier = load(_write(tmp_path, _REPLICA_TIER)).tier("primary")

    assert tier.base_url == ""
    assert [member.id for member in tier.replicas] == ["member-a", "member-b"]
    assert tier.replica_identity.model_revision == "revision-1"
    with pytest.raises(FrozenInstanceError):
        tier.replicas[0].id = "other"
    with pytest.raises(FrozenInstanceError):
        tier.replicas = ()


def test_replica_member_count_is_bounded(tmp_path):
    for count in (0, 1, 17):
        members = ",\n".join(
            '{ id = "member-%s", base_url = "http://127.0.0.1:%s/v1", host_id = "host-a", resource_id = "gpu-%s", qualification_ref = "q-%s" }'
            % (index, 30000 + index, index, index)
            for index in range(count)
        )
        body = _REPLICA_TIER.replace(
            '  { id = "member-a", base_url = "http://127.0.0.1:30000/v1", host_id = "host-a", resource_id = "gpu-a", qualification_ref = "qualification:primary-a" },\n  { id = "member-b", base_url = "http://127.0.0.1:30001/v1", host_id = "host-a", resource_id = "gpu-b", qualification_ref = "qualification:primary-b" },',
            members,
        )
        with pytest.raises(ConfigError, match="from 2 through 16"):
            load(_write(tmp_path, body))


def test_sixteen_replica_members_load(tmp_path):
    members = ",\n".join(
        '{ id = "member-%s", base_url = "http://127.0.0.1:%s/v1", host_id = "host-a", resource_id = "gpu-%s", qualification_ref = "q-%s" }'
        % (index, 30000 + index, index, index)
        for index in range(16)
    )
    body = _REPLICA_TIER.replace(
        '  { id = "member-a", base_url = "http://127.0.0.1:30000/v1", host_id = "host-a", resource_id = "gpu-a", qualification_ref = "qualification:primary-a" },\n  { id = "member-b", base_url = "http://127.0.0.1:30001/v1", host_id = "host-a", resource_id = "gpu-b", qualification_ref = "qualification:primary-b" },',
        members,
    )

    assert len(load(_write(tmp_path, body)).tier("primary").replicas) == 16


def test_replica_endpoint_union_and_direct_tier_remain_closed(tmp_path):
    mixed = _REPLICA_TIER.replace('id = "primary"\n', 'id = "primary"\nbase_url = "http://127.0.0.1:30002/v1"\n', 1)
    with pytest.raises(ConfigError, match="exactly one endpoint shape"):
        load(_write(tmp_path, mixed))

    absent = _ONE_TIER.replace('base_url = "http://127.0.0.1:30000/v1"\n', "")
    with pytest.raises(ConfigError, match="exactly one endpoint shape"):
        load(_write(tmp_path, absent))

    direct_identity = _ONE_TIER.replace(
        'tool_support = true\n',
        'tool_support = true\nreplica_identity = { model_revision = "revision-1", engine_version = "engine-1", image_digest = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", config_fingerprint = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" }\n',
    )
    with pytest.raises(ConfigError, match="valid only with replicas"):
        load(_write(tmp_path, direct_identity))


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('id = "member-a"', 'id = "member-a!"', "replica id"),
        ('host_id = "host-a"', 'host_id = "1host"', "replica host_id"),
        ('host_id = "host-a"', 'host_id = "host-b"', "share one host_id"),
        ('resource_id = "gpu-b"', 'resource_id = "gpu-a"', "duplicate replica resource_id"),
        ('http://127.0.0.1:30001/v1', 'http://127.0.0.1:30000/v1/', "duplicate replica base_url"),
        ('qualification_ref = "qualification:primary-a"', 'qualification_ref = "https://evidence.invalid/a"', "qualification_ref"),
        ('model_revision = "revision-1"', 'model_revision = "bad/revision"', "model_revision"),
        ('image_digest = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"', 'image_digest = "sha256:ABC"', "image_digest"),
    ],
)
def test_replica_member_and_identity_guards(tmp_path, old, new, message):
    with pytest.raises(ConfigError, match=message):
        load(_write(tmp_path, _REPLICA_TIER.replace(old, new, 1)))


def test_replica_endpoint_normalizes_default_port_and_trailing_slash(tmp_path):
    body = _REPLICA_TIER.replace(
        'http://127.0.0.1:30000/v1', 'http://127.0.0.1/v1', 1
    ).replace('http://127.0.0.1:30001/v1', 'http://127.0.0.1:80/v1/', 1)

    with pytest.raises(ConfigError, match="duplicate replica base_url"):
        load(_write(tmp_path, body))


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            "http://127.0.0.1:30000/v1",
            "http://[::1/v1",
            "replica base_url must be an http\\(s\\) URL",
        ),
        (
            "http://127.0.0.1:30000/v1",
            "http://127.0.0.1:0/v1",
            "replica base_url port must be from 1 through 65535",
        ),
        (
            "http://127.0.0.1:30000/v1",
            "http://127.0.0.1:30000/v1\\n",
            "replica base_url must not contain whitespace or control characters",
        ),
        (
            "http://127.0.0.1:30000/v1",
            "http://replica.example:30000/v1\\u200b",
            "replica base_url must not contain whitespace or control characters",
        ),
        (
            "http://127.0.0.1:30000/v1",
            "http://\\u200blocalhost:30000/v1",
            "replica base_url must not contain whitespace or control characters",
        ),
    ],
)
def test_replica_endpoint_parse_errors_are_safe_config_errors(tmp_path, old, new, message):
    with pytest.raises(ConfigError, match=message):
        load(_write(tmp_path, _REPLICA_TIER.replace(old, new, 1)))


def test_replica_endpoint_rejects_idna_normalized_localhost(tmp_path):
    body = _REPLICA_TIER.replace(
        "http://127.0.0.1:30000/v1",
        "http://\\uff4c\\uff4f\\uff43\\uff41\\uff4c\\uff48\\uff4f\\uff53\\uff54:30000/v1",
        1,
    )

    with pytest.raises(ConfigError, match="never use localhost"):
        load(_write(tmp_path, body))


@pytest.mark.parametrize(
    ("endpoint", "message"),
    [
        ("http://127.0.0.1:30000/v1?", "without credentials, query, or fragment"),
        ("http://127.0.0.1:30000/v1#", "without credentials, query, or fragment"),
        ("http://good|bad:30000/v1", "replica base_url host is invalid"),
        ("http://good%2Ebad:30000/v1", "replica base_url host is invalid"),
    ],
)
def test_replica_endpoint_rejects_empty_delimiters_and_invalid_hosts(
    tmp_path, endpoint, message
):
    with pytest.raises(ConfigError, match=message):
        load(_write(tmp_path, _REPLICA_TIER.replace("http://127.0.0.1:30000/v1", endpoint, 1)))


def test_replica_endpoint_accepts_valid_dns_name_without_resolution(tmp_path):
    body = _REPLICA_TIER.replace(
        "http://127.0.0.1:30000/v1", "http://replica-a.example:30000/v1", 1
    ).replace(
        "http://127.0.0.1:30001/v1", "http://replica-b.example:30001/v1", 1
    )

    assert len(load(_write(tmp_path, body)).tier("primary").replicas) == 2


def test_replica_endpoint_canonicalizes_literal_ipv6_for_duplicates(tmp_path):
    body = _REPLICA_TIER.replace(
        "http://127.0.0.1:30000/v1", "http://[::1]:80/v1", 1
    ).replace(
        "http://127.0.0.1:30001/v1", "http://[0:0:0:0:0:0:0:1]/v1/", 1
    )

    with pytest.raises(ConfigError, match="duplicate replica base_url"):
        load(_write(tmp_path, body))


def test_replica_base_url_wrong_type_does_not_echo_its_value(tmp_path):
    body = _REPLICA_TIER.replace(
        'base_url = "http://127.0.0.1:30000/v1"',
        'base_url = { synthetic_secret_marker = "do-not-echo" }',
        1,
    )

    with pytest.raises(ConfigError, match="replica base_url must be a string") as excinfo:
        load(_write(tmp_path, body))
    assert "synthetic_secret_marker" not in str(excinfo.value)
    assert "do-not-echo" not in str(excinfo.value)


def test_replica_unknown_field_and_missing_identity_field_are_bounded(tmp_path):
    unknown = _REPLICA_TIER.replace(
        'qualification_ref = "qualification:primary-a"',
        'qualification_ref = "qualification:primary-a", synthetic_secret_marker = "do-not-echo"',
        1,
    )
    with pytest.raises(ConfigError, match="replica member contains unknown") as excinfo:
        load(_write(tmp_path, unknown))
    assert "synthetic_secret_marker" not in str(excinfo.value)
    assert "do-not-echo" not in str(excinfo.value)

    missing = _REPLICA_TIER.replace(
        ', config_fingerprint = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"',
        "",
        1,
    )
    with pytest.raises(ConfigError, match="replica_identity missing required keys"):
        load(_write(tmp_path, missing))


@pytest.mark.parametrize("field", ["metadata_source", "context_admission"])
def test_tier_enum_fields_reject_wrong_types_without_native_type_errors(tmp_path, field):
    body = _REPLICA_TIER.replace(
        'model_identity = true', f'{field} = []\nmodel_identity = true', 1
    )

    with pytest.raises(ConfigError, match=fr"{field} must be a string"):
        load(_write(tmp_path, body))


def test_replica_validation_aggregates_member_and_identity_errors(tmp_path):
    body = (
        _REPLICA_TIER.replace('id = "member-b"', 'id = "member-a"', 1)
        .replace(
            'base_url = "http://127.0.0.1:30001/v1", host_id = "host-a"',
            'base_url = "http://127.0.0.1:30001/v1", host_id = "host-b"',
            1,
        )
        .replace('resource_id = "gpu-b"', 'resource_id = "gpu-a"', 1)
        .replace('http://127.0.0.1:30001/v1', 'http://127.0.0.1:30000/v1/', 1)
        .replace('image_digest = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"', 'image_digest = "sha256:ABC"', 1)
    )

    with pytest.raises(ConfigError) as excinfo:
        load(_write(tmp_path, body))
    message = str(excinfo.value)
    assert "duplicate replica id" in message
    assert "duplicate replica resource_id" in message
    assert "duplicate replica base_url" in message
    assert "replica members must share one host_id" in message
    assert "replica_identity image_digest" in message


def test_invalid_member_id_does_not_hide_other_member_conflicts(tmp_path):
    body = (
        _REPLICA_TIER.replace('id = "member-a"', 'id = "member-a!"', 1)
        .replace('id = "member-b"', 'id = "member-b"', 1)
        .replace('host_id = "host-a", resource_id = "gpu-b"', 'host_id = "host-b", resource_id = "gpu-a"', 1)
        .replace('http://127.0.0.1:30001/v1', 'http://127.0.0.1:30000/v1/', 1)
    )

    with pytest.raises(ConfigError) as excinfo:
        load(_write(tmp_path, body))
    message = str(excinfo.value)
    assert "replica id must match" in message
    assert "duplicate replica resource_id" in message
    assert "duplicate replica base_url" in message
    assert "replica members must share one host_id" in message


def test_replica_validation_aggregates_shared_requirements_and_identity(tmp_path):
    body = (
        _REPLICA_TIER.replace('model = "primary-model"\n', "")
        .replace('health_path = "/health"\n', "")
        .replace(
            'config_fingerprint = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"',
            'config_fingerprint = "sha256:ABC"',
            1,
        )
    )

    with pytest.raises(ConfigError) as excinfo:
        load(_write(tmp_path, body))
    message = str(excinfo.value)
    assert "replicas require a non-empty model" in message
    assert "replicas require health_path" in message
    assert "replica_identity config_fingerprint" in message


def test_replica_unknown_member_keys_and_shared_identity_requirements(tmp_path):
    unknown = _REPLICA_TIER.replace(
        'qualification_ref = "qualification:primary-a"',
        'qualification_ref = "qualification:primary-a", max_concurrency = 1',
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


def test_per_tier_output_cap_is_optional_and_bounded_by_context(tmp_path):
    config = load(_write(tmp_path, _ONE_TIER))
    assert config.tier("primary").max_output_tokens is None

    capped = _ONE_TIER.replace(
        "context_limit = 4096",
        "context_limit = 4096\nmax_output_tokens = 1024",
    )
    assert load(_write(tmp_path, capped)).tier("primary").max_output_tokens == 1024

    for invalid_value in ("0", "-1", "true", '"1024"', "4097"):
        invalid = capped.replace(
            "max_output_tokens = 1024",
            f"max_output_tokens = {invalid_value}",
        )
        with pytest.raises(ConfigError, match="max_output_tokens.*context_limit"):
            load(_write(tmp_path, invalid))


def test_upstream_context_admission_requires_exact_identity_readiness(tmp_path):
    config = load(_write(tmp_path, _ONE_TIER))
    assert config.tier("primary").context_admission == "estimate"

    delegated = _ONE_TIER.replace(
        "tool_support = true",
        '''tool_support = true
health_path = "/health"
model_identity = true
context_admission = "upstream"''',
    )
    assert load(_write(tmp_path, delegated)).tier("primary").context_admission == "upstream"

    without_identity = _ONE_TIER.replace(
        "tool_support = true",
        'tool_support = true\ncontext_admission = "upstream"',
    )
    with pytest.raises(ConfigError, match="context_admission='upstream'.*model_identity"):
        load(_write(tmp_path, without_identity))

    invalid = _ONE_TIER.replace(
        "tool_support = true",
        'tool_support = true\ncontext_admission = "tokenizer"',
    )
    with pytest.raises(ConfigError, match="context_admission.*not in"):
        load(_write(tmp_path, invalid))


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

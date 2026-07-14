from __future__ import annotations

import json
from pathlib import Path

import pytest

from anvil_serving import cli, collectors


class Response:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self.payload if size < 0 else self.payload[:size]


def _config(**overrides):
    values = {
        "name": "gpu-gap-adapter",
        "adapter": "anvil-json-v1",
        "endpoint": "http://100.64.0.20:9100/capabilities",
        "capabilities": ("gpu-process-memory", "gpu-container-attribution"),
        "auth_env": "ANVIL_COLLECTOR_TOKEN",
    }
    values.update(overrides)
    return collectors.CollectorConfig(**values)


def test_private_adapter_requires_environment_resolved_authentication() -> None:
    with pytest.raises(ValueError, match="require auth_env"):
        _config(auth_env=None)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://8.8.8.8/capabilities",
        "https://collector.example/capabilities",
        "https://user:password@100.64.0.20/capabilities",
        "https://100.64.0.20/capabilities?token=secret",
    ],
)
def test_unsafe_or_credential_bearing_endpoints_are_rejected(endpoint) -> None:
    with pytest.raises(ValueError):
        _config(endpoint=endpoint)


def test_inspect_sends_bearer_token_and_redacts_all_output() -> None:
    seen = {}

    def opener(request, timeout):
        seen["authorization"] = request.get_header("Authorization")
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return Response(
            json.dumps(
                {
                    "status": "ok",
                    "capabilities": [
                        "gpu-process-memory",
                        "gpu-container-attribution",
                    ],
                    "diagnostic": "collector-secret-token",
                }
            ).encode()
        )

    result = collectors.inspect_adapter(
        _config(),
        environment={"ANVIL_COLLECTOR_TOKEN": "collector-secret-token"},
        opener=opener,
    )

    assert result["ok"] is True
    assert result["available_capabilities"] == [
        "gpu-container-attribution",
        "gpu-process-memory",
    ]
    assert seen["authorization"] == "Bearer collector-secret-token"
    assert seen["url"] == "http://100.64.0.20:9100/capabilities"
    assert "collector-secret-token" not in json.dumps(result)


def test_absent_or_failed_optional_adapter_is_degraded_not_exception() -> None:
    missing = collectors.inspect_adapter(_config(), environment={})
    failed = collectors.inspect_adapter(
        _config(endpoint="http://127.0.0.1:9100/capabilities", auth_env=None),
        opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")),
    )

    assert missing["capability_status"] == "permission-denied"
    assert failed["capability_status"] == "failed"
    assert missing["ok"] is failed["ok"] is False


def test_configure_writes_only_env_name_and_round_trips(tmp_path, capsys) -> None:
    output = tmp_path / "collector.json"
    rc = collectors.main(
        [
            "configure",
            "--name",
            "local-gap",
            "--endpoint",
            "http://127.0.0.1:9100/capabilities",
            "--capability",
            "gpu-gap",
            "--output",
            str(output),
        ]
    )

    assert rc == 0
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["auth_env"] is None
    assert "token" not in output.read_text(encoding="utf-8").lower()
    assert collectors.CollectorConfig.from_mapping(saved).name == "local-gap"
    assert json.loads(capsys.readouterr().out)["status"] == "configured"


def test_capabilities_without_configuration_reports_not_configured(capsys) -> None:
    assert collectors.main(["capabilities"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "not-configured"
    assert payload["capability_status"] == "unsupported"


def test_public_cli_exposes_bounded_adapter_actions(capsys) -> None:
    assert cli.main(["collectors", "--help"]) == 0
    help_text = capsys.readouterr().out
    for action in ("configure", "validate", "capabilities", "inspect"):
        assert action in help_text
    for forbidden in ("install", "launch", "start", "stop", "restart"):
        assert forbidden not in help_text


def test_public_cli_guards_collector_config_writes(tmp_path, capsys) -> None:
    output = tmp_path / "collector.json"
    args = [
        "collectors",
        "configure",
        "--name",
        "local-gap",
        "--endpoint",
        "http://127.0.0.1:9100/capabilities",
        "--capability",
        "gpu-gap",
        "--output",
        str(output),
    ]

    assert cli.main(args) == 3
    assert not output.exists()
    error = capsys.readouterr().err
    assert "confirmation required" in error
    assert "rerun the same command with --confirm" in error
    assert "collectors configure --confirm" not in error

    assert cli.main([*args, "--confirm"]) == 0
    assert output.is_file()


def test_config_file_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps({**_config().as_dict(), "command": "start exporter"}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown"):
        collectors._load_config(str(path))


@pytest.mark.parametrize("capability", ["9starts-wrong", "gpu.usage", "mémoire"])
def test_capability_identifiers_are_ascii_and_bounded(capability) -> None:
    with pytest.raises(ValueError, match="identifiers"):
        _config(capabilities=(capability,))


def test_library_inspection_enforces_timeout_bounds() -> None:
    with pytest.raises(ValueError, match="timeout"):
        collectors.inspect_adapter(_config(), timeout=0)


def test_oversized_config_is_rejected_before_read(tmp_path: Path) -> None:
    path = tmp_path / "huge.json"
    path.write_bytes(b" " * (256 * 1024 + 1))

    with pytest.raises(ValueError, match="exceeds"):
        collectors._load_config(str(path))

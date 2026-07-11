from __future__ import annotations

import json

import pytest

from anvil_serving import transports
from anvil_serving.operator_output import (
    CONTEXT_FIELDS,
    ENVELOPE_FIELDS,
    EXIT_CODES,
    OutputOptions,
    PartialResultError,
    SafetyError,
    TransportError,
    UsageError,
    error_envelope,
    exit_code,
    redact,
    render_human,
    render_json,
    success_envelope,
)


def _remote_context() -> dict[str, object]:
    return {
        "command": "router status",
        "topology": "fakoli",
        "overlay": "dark-prod",
        "command_host": "operator",
        "command_runtime": "operator-native",
        "target": "host:dark",
        "execution_host": "dark",
        "execution_runtime": "dark-docker",
        "resource_host": "dark",
        "resource_runtime": "dark-docker",
        "resource": "router",
        "transport": "controller",
        "controller_endpoint": "http://100.64.0.10:8766",
        "controller_endpoint_kind": "controller",
        "resource_endpoint": "http://100.64.0.10:8000/v1",
        "resource_endpoint_kind": "service",
        "gpu_role": "fast",
        "gpu_uuid": "GPU-01234567-89ab-cdef-0123-456789abcdef",
    }


def test_success_envelope_has_a_fixed_json_schema_and_context():
    envelope = success_envelope("router status", _remote_context(), {"state": "ready"})

    assert tuple(envelope) == ENVELOPE_FIELDS
    assert tuple(envelope["context"]) == CONTEXT_FIELDS
    assert envelope["ok"] is True
    assert envelope["error"] is None
    assert envelope["warnings"] == []
    assert json.loads(render_json(envelope)) == envelope
    assert exit_code(envelope) == EXIT_CODES["success"]


@pytest.mark.parametrize(
    ("error", "expected_class", "expected_exit"),
    [
        (RuntimeError("backend failed"), "execution", 1),
        (UsageError("bad flag"), "usage", 2),
        (SafetyError("confirmation required"), "safety", 3),
        (TransportError("controller unreachable"), "transport", 4),
        (PartialResultError("remote completion uncertain"), "partial", 5),
    ],
)
def test_error_envelopes_classify_every_primitive_exit_class(error, expected_class, expected_exit):
    envelope = error_envelope("router status", _remote_context(), error)

    assert tuple(envelope) == ENVELOPE_FIELDS
    assert envelope["ok"] is False
    assert envelope["data"] is None
    assert tuple(envelope["error"]) == ("class", "code", "message", "details")
    assert envelope["error"]["class"] == expected_class
    assert exit_code(envelope) == expected_exit


def test_redaction_removes_tokens_environment_values_and_private_payloads():
    rendered = redact(
        {
            "authorization": "Bearer secret-token-value",
            "environment": {"ANVIL_TOKEN": "secret-token-value", "PATH": "C:/bin"},
            "env": "ANVIL_TOKEN=secret-token-value",
            "command": "curl --token secret-token-value",
            "command_payload": ["--token", "secret-token-value"],
            "diagnostic": "request failed with Bearer secret-token-value and sk-abcdefghijk",
        },
        secrets=("secret-token-value",),
    )

    blob = json.dumps(rendered, sort_keys=True)
    assert "secret-token-value" not in blob
    assert "sk-abcdefghijk" not in blob
    assert rendered["environment"] == {"ANVIL_TOKEN": "<redacted>", "PATH": "<redacted>"}
    assert rendered["env"] == "<redacted>"
    assert rendered["command"] == "<redacted>"
    assert rendered["command_payload"] == "<redacted>"


def test_redaction_sanitizes_tuples_and_dict_keys_containing_known_tokens():
    secret = "known-controller-token"
    rendered = redact(
        {f"prefix-{secret}-suffix": (secret, {secret: "value"})},
        secrets=(secret,),
    )

    assert secret not in json.dumps(rendered)


def test_transport_uncertainty_becomes_partial_exit_with_execution_details():
    error = transports.TransportError(
        "controller_timeout",
        "controller result is uncertain",
        execution_state="partial_result",
        details={"endpoint": "http://100.64.0.10:8766"},
    )

    envelope = error_envelope("router restart", _remote_context(), error)

    assert envelope["error"]["class"] == "partial"
    assert envelope["error"]["code"] == "controller_timeout"
    assert envelope["error"]["details"]["execution_state"] == "partial_result"
    assert envelope["error"]["details"]["may_have_executed"] is True
    assert exit_code(envelope) == 5


def test_transport_errors_and_subclasses_are_exit_four_before_dispatch():
    class SpecializedTransportError(transports.TransportError):
        pass

    for error in (
        transports.TransportError("missing_controller_token", "token missing"),
        SpecializedTransportError("controller_connect_failed", "connection refused"),
    ):
        envelope = error_envelope("router status", _remote_context(), error)

        assert error.execution_state == "not_started"
        assert envelope["error"]["class"] == "transport"
        assert exit_code(envelope) == 4


def test_redaction_normalizes_nested_credential_shaped_keys():
    rendered = redact(
        {
            "result": {
                "accessToken": "access-value",
                "private.key": "private-value",
                "client_secret": "client-value",
                "authorization-token": "authorization-value",
            }
        }
    )

    assert set(rendered["result"].values()) == {"<redacted>"}


def test_redaction_removes_common_cloud_credentials_from_keys_and_text():
    rendered = redact(
        {
            "access_key": "access-value",
            "secretAccessKey": "secret-value",
            "diagnostic": "Bearer opaque-token access_key=AKIAABCDEFGHIJKLMNOP",
        }
    )

    blob = json.dumps(rendered)
    assert "access-value" not in blob
    assert "secret-value" not in blob
    assert "opaque-token" not in blob
    assert "AKIAABCDEFGHIJKLMNOP" not in blob


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_json_output_rejects_non_finite_numbers(value):
    with pytest.raises(ValueError, match="finite"):
        success_envelope("router status", _remote_context(), {"value": value})


def test_json_rendering_canonicalizes_set_and_frozenset_values():
    envelope = success_envelope(
        "router status",
        _remote_context(),
        {"set": {"z", "a", "m"}, "frozen": frozenset({3, 1, 2})},
    )

    assert json.loads(render_json(envelope))["data"] == {
        "frozen": [1, 2, 3],
        "set": ["a", "m", "z"],
    }


def test_remote_human_success_prints_context_header_before_primary_result():
    rendered = render_human(success_envelope("router status", _remote_context(), "ready"))

    assert rendered.stdout.startswith("Context: topology=fakoli")
    assert rendered.stdout.endswith("ready\n")
    assert rendered.stderr == ""


def test_human_errors_use_stderr_and_keep_remote_context_visible():
    rendered = render_human(
        error_envelope("router status", _remote_context(), TransportError("offline"))
    )

    assert rendered.stdout == ""
    assert rendered.stderr.startswith("Context: topology=fakoli")
    assert rendered.stderr.endswith("offline\n")


def test_json_mode_is_unchanged_by_verbosity_and_quiet_verbose_is_rejected():
    envelope = success_envelope("router status", _remote_context(), {"state": "ready"})

    quiet = render_human(envelope, options=OutputOptions(json_mode=True, quiet=True))
    verbose = render_human(envelope, options=OutputOptions(json_mode=True, verbose=True))

    assert quiet.stdout == verbose.stdout == render_json(envelope) + "\n"
    assert quiet.stderr == verbose.stderr == ""
    with pytest.raises(UsageError):
        OutputOptions(quiet=True, verbose=True)

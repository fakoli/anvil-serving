from __future__ import annotations

import json

import pytest

from anvil_serving import transports
from anvil_serving.operator_output import (
    error_envelope,
    exit_code,
    redact,
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


def _timing_histogram():
    return {"unit": "seconds", "count": 8, "sum": 0.04, "buckets": {"0.01": 8, "+Inf": 8}}


def test_closed_numeric_first_token_histogram_survives_recursive_redaction():
    payload = {"timing": {"histograms": {"prefill_to_first_token": _timing_histogram()}},
               "session_token": "opaque-secret"}
    rendered = redact(redact(payload))
    assert rendered["timing"]["histograms"]["prefill_to_first_token"] == _timing_histogram()
    assert rendered["session_token"] == "<redacted>"
    # Even this narrow exception still runs recursive known-secret redaction.
    assert redact({"prefill_to_first_token": _timing_histogram()}, secrets=("seconds",))[
        "prefill_to_first_token"]["unit"] == "<redacted>"
    assert redact({"session_token": _timing_histogram()})["session_token"] == "<redacted>"
    assert redact({"prefill_to_first_token_copy": _timing_histogram()})["prefill_to_first_token_copy"] == "<redacted>"


@pytest.mark.parametrize("replacement", [
    "opaque-secret", {"unit": "seconds"},
    _timing_histogram() | {"unit": "Bearer opaque-secret"},
    _timing_histogram() | {"token": "opaque-secret"},
    *[_timing_histogram() | {"count": count} for count in (True, -1, 1.5, 2**53 + 1)],
    *[_timing_histogram() | {"sum": total} for total in (True, -1, float("inf"), float("nan"), "opaque-secret")],
    *[_timing_histogram() | {"buckets": buckets} for buckets in (
        {}, {"0.01": "opaque-secret"}, {"0.01": {"token": "opaque-secret"}},
        {"session_token": 8}, {"1" * 33: 8}, {str(index): 8 for index in range(65)},
    )],
])
def test_first_token_histogram_exception_rejects_secret_or_malformed_payloads(replacement):
    assert redact({"prefill_to_first_token": replacement}) == {"prefill_to_first_token": "<redacted>"}

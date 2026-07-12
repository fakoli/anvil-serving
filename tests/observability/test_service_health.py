from __future__ import annotations

from datetime import datetime, timezone

from anvil_serving.observability.probes.service_health import (
    COMPONENTS,
    collect_service_health,
)
from anvil_serving.observability.schema import CapabilityStatus, TelemetrySample


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)


def _configuration():
    return {
        component: {
            "host": "dark" if component not in {"openclaw-gateway", "voice-realtime"} else "mini",
            "port": 8000 + index,
            "container": f"anvil-{component}" if component != "openclaw-gateway" else None,
            "served_identity": f"identity-{component}",
        }
        for index, component in enumerate(COMPONENTS)
    }


def test_configured_components_report_complete_healthy_entries() -> None:
    config = _configuration()

    def observed(component, target):
        return {
            "port_listening": True,
            "container_state": "running" if target["container"] else "not-applicable",
            "served_identity": target["served_identity"],
        }

    samples = collect_service_health(config, provider=observed, collected_at=NOW)

    assert len(samples) == len(COMPONENTS)
    assert {dict(sample.labels)["component"] for sample in samples} == set(COMPONENTS)
    for sample in samples:
        labels = dict(sample.labels)
        assert sample.capability_status is CapabilityStatus.OK
        assert sample.value is True
        assert labels["expected_port"].isdigit()
        assert labels["container_state"] in {"running", "not-applicable"}
        assert labels["served_identity"].startswith("identity-")
        assert labels["owning_host"] in {"dark", "mini"}
        assert TelemetrySample.from_json(sample.to_json()) == sample


def test_absent_components_are_not_configured_not_failed() -> None:
    samples = collect_service_health({}, provider=lambda *_: {}, collected_at=NOW)

    assert len(samples) == len(COMPONENTS)
    assert all(sample.capability_status is CapabilityStatus.UNSUPPORTED for sample in samples)
    assert all(sample.value is None for sample in samples)
    assert all(dict(sample.labels)["health"] == "not-configured" for sample in samples)
    assert all(dict(sample.labels)["configured"] == "false" for sample in samples)


def test_disabled_component_is_not_configured() -> None:
    samples = collect_service_health(
        {"stt": {"enabled": False}}, provider=lambda *_: {}, collected_at=NOW
    )

    stt = next(sample for sample in samples if dict(sample.labels)["component"] == "stt")
    assert stt.capability_status is CapabilityStatus.UNSUPPORTED
    assert dict(stt.labels)["health"] == "not-configured"


def test_configured_but_unhealthy_component_is_failed() -> None:
    config = {"router": _configuration()["router"]}

    samples = collect_service_health(
        config,
        provider=lambda *_: {
            "port_listening": False,
            "container_state": "exited",
            "served_identity": "router",
        },
        collected_at=NOW,
    )
    router = next(
        sample for sample in samples if dict(sample.labels)["component"] == "router"
    )

    assert router.capability_status is CapabilityStatus.FAILED
    assert router.value is None
    assert dict(router.labels)["health"] == "failed"
    assert dict(router.labels)["container_state"] == "exited"


def test_invalid_config_is_failed_and_does_not_hide_other_components() -> None:
    config = _configuration()
    config["router"] = {"host": "dark", "port": 0}

    samples = collect_service_health(
        config,
        provider=lambda component, target: {
            "port_listening": True,
            "container_state": "running" if target["container"] else "not-applicable",
            "served_identity": target["served_identity"],
        },
        collected_at=NOW,
    )
    by_component = {dict(sample.labels)["component"]: sample for sample in samples}

    assert by_component["router"].capability_status is CapabilityStatus.FAILED
    assert by_component["tts"].capability_status is CapabilityStatus.OK


def test_socket_permission_error_is_not_flattened_to_not_listening(monkeypatch) -> None:
    def denied(*_args, **_kwargs):
        raise PermissionError("network access denied")

    monkeypatch.setattr(
        "anvil_serving.observability.probes.service_health.socket.create_connection",
        denied,
    )
    router = next(
        sample
        for sample in collect_service_health(
            {
                "router": {
                    "host": "127.0.0.1",
                    "port": 8000,
                    "served_identity": "router",
                }
            },
            collected_at=NOW,
        )
        if dict(sample.labels)["component"] == "router"
    )

    assert router.capability_status is CapabilityStatus.PERMISSION_DENIED
    assert router.value is None

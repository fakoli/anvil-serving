from __future__ import annotations

from datetime import datetime, timezone

from anvil_serving.observability.probes.gpu_nvidia import collect_nvidia_gpus
from anvil_serving.observability.schema import CapabilityStatus, TelemetrySample


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)
XML = """<?xml version="1.0"?>
<nvidia_smi_log>
  <gpu id="00000000:01:00.0">
    <product_name>NVIDIA RTX Test</product_name>
    <uuid>GPU-1234</uuid>
    <fb_memory_usage><total>24576 MiB</total><used>4096 MiB</used><free>20480 MiB</free></fb_memory_usage>
    <utilization><gpu_util>72 %</gpu_util></utilization>
    <temperature><gpu_temp>63 C</gpu_temp></temperature>
    <gpu_power_readings><power_draw>188.5 W</power_draw></gpu_power_readings>
    <processes>
      <process_info><pid>42</pid><process_name>python.exe</process_name><used_memory>2048 MiB</used_memory></process_info>
    </processes>
  </gpu>
</nvidia_smi_log>"""


def _by_metric(samples: list[TelemetrySample]) -> dict[str, TelemetrySample]:
    return {sample.metric: sample for sample in samples}


def test_structured_nvidia_xml_reports_identity_gpu_metrics_and_processes() -> None:
    samples = collect_nvidia_gpus(provider=lambda: XML, host_id="dark", collected_at=NOW)
    metrics = _by_metric(samples)

    assert metrics["gpu.identity"].value == "NVIDIA RTX Test"
    assert metrics["gpu.utilization"].value == 72
    assert metrics["gpu.memory.total"].value == 24576 * 1024**2
    assert metrics["gpu.memory.used"].value == 4096 * 1024**2
    assert metrics["gpu.memory.available"].value == 20480 * 1024**2
    assert metrics["gpu.temperature"].value == 63
    assert metrics["gpu.power.draw"].value == 188.5
    assert metrics["gpu.process.count"].value == 1
    assert metrics["gpu.process.memory.used"].value == 2048 * 1024**2
    assert dict(metrics["gpu.process.memory.used"].labels)["pid"] == "42"
    assert all(TelemetrySample.from_json(sample.to_json()) == sample for sample in samples)


def test_missing_fields_are_explicit_not_zero() -> None:
    payload = XML.replace("<gpu_util>72 %</gpu_util>", "<gpu_util>N/A</gpu_util>")

    metric = _by_metric(
        collect_nvidia_gpus(provider=lambda: payload, host_id="dark", collected_at=NOW)
    )["gpu.utilization"]

    assert metric.capability_status is CapabilityStatus.MISSING
    assert metric.value is None


def test_absent_collector_fails_gracefully() -> None:
    def absent() -> str:
        raise FileNotFoundError("nvidia-smi was not found")

    samples = collect_nvidia_gpus(provider=absent, host_id="dark", collected_at=NOW)

    assert len(samples) == 1
    assert samples[0].metric == "gpu.collector.status"
    assert samples[0].capability_status is CapabilityStatus.FAILED
    assert samples[0].value is None


def test_malformed_or_empty_xml_fails_gracefully() -> None:
    for payload in ("not xml", "<nvidia_smi_log />"):
        sample = collect_nvidia_gpus(
            provider=lambda payload=payload: payload,
            host_id="dark",
            collected_at=NOW,
        )[0]
        assert sample.capability_status is CapabilityStatus.FAILED
        assert sample.value is None


def test_driver_token_like_process_names_are_redacted() -> None:
    payload = XML.replace("python.exe", "Bearer sk-proj-abcdefgh")

    serialized = "".join(
        sample.to_json()
        for sample in collect_nvidia_gpus(
            provider=lambda: payload, host_id="dark", collected_at=NOW
        )
    )

    assert "sk-proj-abcdefgh" not in serialized
    assert "<redacted>" in serialized


def test_unexpected_provider_failure_becomes_failed_status() -> None:
    def broken() -> str:
        raise RuntimeError("driver bridge bug")

    sample = collect_nvidia_gpus(provider=broken, host_id="dark", collected_at=NOW)[0]

    assert sample.capability_status is CapabilityStatus.FAILED


def test_xml_entities_are_rejected_before_parsing() -> None:
    payload = '<!DOCTYPE x [<!ENTITY a "expanded">]><nvidia_smi_log>&a;</nvidia_smi_log>'

    sample = collect_nvidia_gpus(
        provider=lambda: payload, host_id="dark", collected_at=NOW
    )[0]

    assert sample.capability_status is CapabilityStatus.FAILED


def test_nvidia_versioned_dtd_declaration_is_allowed_and_stripped() -> None:
    payload = XML.replace(
        '<?xml version="1.0"?>',
        '<?xml version="1.0"?>\n<!DOCTYPE nvidia_smi_log SYSTEM "nvsmi_device_v13.dtd">',
    )

    samples = collect_nvidia_gpus(
        provider=lambda: payload, host_id="dark", collected_at=NOW
    )

    assert samples[0].capability_status is CapabilityStatus.OK


def test_current_nvidia_power_field_is_supported() -> None:
    payload = XML.replace(
        "<power_draw>188.5 W</power_draw>",
        "<instant_power_draw>190.5 W</instant_power_draw>",
    )

    metric = _by_metric(
        collect_nvidia_gpus(provider=lambda: payload, host_id="dark", collected_at=NOW)
    )["gpu.power.draw"]

    assert metric.value == 190.5


def test_numeric_field_with_trailing_junk_is_missing() -> None:
    payload = XML.replace("<gpu_util>72 %</gpu_util>", "<gpu_util>72 % corrupted</gpu_util>")

    metric = _by_metric(
        collect_nvidia_gpus(provider=lambda: payload, host_id="dark", collected_at=NOW)
    )["gpu.utilization"]

    assert metric.capability_status is CapabilityStatus.MISSING
    assert metric.value is None


def test_oversized_identity_is_missing_instead_of_crashing_probe() -> None:
    payload = XML.replace("NVIDIA RTX Test", "x" * (64 * 1024 + 1))

    metric = _by_metric(
        collect_nvidia_gpus(provider=lambda: payload, host_id="dark", collected_at=NOW)
    )["gpu.identity"]

    assert metric.capability_status is CapabilityStatus.MISSING

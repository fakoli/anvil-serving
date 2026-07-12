from __future__ import annotations

from datetime import datetime, timezone

import pytest

from anvil_serving.observability.macos_primitives import parse_swapusage, parse_vm_stat
from anvil_serving.observability.probes import macos_host
from anvil_serving.observability.probes.macos_host import collect_macos_host
from anvil_serving.observability.schema import CapabilityStatus, TelemetrySample


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)
RAW = {
    "host_identity": "fakoli-mini",
    "hardware_model": "Mac16,10",
    "os_version": "26.0",
    "cpu_percent": 25.0,
    "memory_total_bytes": 16 * 1024**3,
    "memory_available_bytes": 6 * 1024**3,
    "memory_used_bytes": 10 * 1024**3,
    "memory_pressure_percent": 62.5,
    "swap_used_bytes": 512 * 1024**2,
    "disk_bytes_per_second": 1_000_000,
    "network_bytes_per_second": 2_000_000,
    "processes": [
        {
            "pid": 42,
            "command": "openclaw-gateway",
            "cpu_percent": 2.5,
            "memory_used_bytes": 256 * 1024**2,
        }
    ],
}


def _by_metric(samples: list[TelemetrySample]) -> dict[str, TelemetrySample]:
    return {sample.metric: sample for sample in samples}


def test_fixture_emits_cpu_memory_swap_disk_network_process_and_identity() -> None:
    samples = collect_macos_host(
        provider=lambda: RAW, host_id="mini", collected_at=NOW
    )
    metrics = _by_metric(samples)

    assert metrics["host.identity"].value == "fakoli-mini"
    assert metrics["host.cpu.utilization"].value == 25.0
    assert metrics["host.memory.used"].value == 10 * 1024**3
    assert metrics["host.memory.pressure"].value == 62.5
    assert metrics["host.swap.used"].value == 512 * 1024**2
    assert metrics["host.disk.throughput"].value == 1_000_000
    assert metrics["host.network.throughput"].value == 2_000_000
    assert metrics["host.process.cpu.utilization"].value == 2.5
    assert metrics["host.process.memory.used"].value == 256 * 1024**2
    assert all(TelemetrySample.from_json(sample.to_json()) == sample for sample in samples)


def test_permission_failure_is_explicit_and_never_zero() -> None:
    def denied():
        raise PermissionError("operation not permitted")

    samples = collect_macos_host(provider=denied, host_id="mini", collected_at=NOW)

    assert all(
        sample.capability_status is CapabilityStatus.PERMISSION_DENIED
        for sample in samples
    )
    assert all(sample.value is None for sample in samples)


def test_missing_signal_is_explicit_without_hiding_other_values() -> None:
    raw = dict(RAW)
    raw["swap_used_bytes"] = None

    metrics = _by_metric(
        collect_macos_host(provider=lambda: raw, host_id="mini", collected_at=NOW)
    )

    assert metrics["host.swap.used"].capability_status is CapabilityStatus.MISSING
    assert metrics["host.swap.used"].value is None
    assert metrics["host.cpu.utilization"].capability_status is CapabilityStatus.OK


def test_permission_restricted_signal_is_isolated_from_other_capabilities() -> None:
    raw = dict(
        RAW,
        swap_used_bytes=None,
        _statuses={"swap_used_bytes": CapabilityStatus.PERMISSION_DENIED},
        _details={"swap_used_bytes": "operation not permitted"},
    )

    metrics = _by_metric(
        collect_macos_host(provider=lambda: raw, host_id="mini", collected_at=NOW)
    )

    assert (
        metrics["host.swap.used"].capability_status
        is CapabilityStatus.PERMISSION_DENIED
    )
    assert metrics["host.swap.used"].value is None
    assert metrics["host.memory.used"].capability_status is CapabilityStatus.OK


def test_invalid_process_rows_still_emit_explicit_process_capabilities() -> None:
    raw = dict(RAW, processes=["malformed"])

    metrics = _by_metric(
        collect_macos_host(provider=lambda: raw, host_id="mini", collected_at=NOW)
    )

    assert (
        metrics["host.process.cpu.utilization"].capability_status
        is CapabilityStatus.MISSING
    )
    assert metrics["host.process.memory.used"].value is None


def test_non_macos_default_path_is_unsupported(monkeypatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Windows")

    samples = collect_macos_host(host_id="not-a-mac", collected_at=NOW)

    assert all(sample.capability_status is CapabilityStatus.UNSUPPORTED for sample in samples)
    assert all(sample.value is None for sample in samples)


def test_vm_stat_and_swap_primitives_preserve_page_size_and_units() -> None:
    memory = parse_vm_stat(
        """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free: 100.
Pages inactive: 200.
Pages speculative: 50.
Pages active: 500.
""",
        total_bytes=16 * 1024**3,
    )
    swap = parse_swapusage(
        "vm.swapusage: total = 2048.00M used = 512.50M free = 1535.50M"
    )

    assert memory["memory_available_bytes"] == 350 * 16384
    assert memory["memory_used_bytes"] == 16 * 1024**3 - 350 * 16384
    assert swap["swap_total_bytes"] == 2048 * 1024**2
    assert swap["swap_used_bytes"] == int(512.5 * 1024**2)


def test_probe_has_no_model_serve_assumptions() -> None:
    samples = collect_macos_host(
        provider=lambda: RAW, host_id="mini", collected_at=NOW
    )

    serialized = "".join(sample.to_json().lower() for sample in samples)
    for model_role in ("stt", "tts", "llm", "model-serve"):
        assert model_role not in serialized


def test_iostat_total_parser_handles_multiple_devices(monkeypatch) -> None:
    monkeypatch.setattr(
        macos_host,
        "run_command",
        lambda argv: "disk0 disk1\nKB/t xfrs MB KB/t xfrs MB\n4.0 10 2.5 8.0 20 3.5",
    )

    assert macos_host._read_iostat_total() == 6 * 1024**2


def test_netstat_total_uses_one_link_row_per_non_loopback_interface(monkeypatch) -> None:
    output = """Name Mtu Network Address Ipkts Ierrs Ibytes Opkts Oerrs Obytes Coll
en0 1500 <Link#4> aa:bb 10 0 1000 20 0 2000 0
en0 1500 192.0.2 host 10 0 1000 20 0 2000 0
lo0 16384 <Link#1> 00:00 10 0 9999 10 0 9999 0
"""
    monkeypatch.setattr(macos_host, "run_command", lambda argv: output)

    assert macos_host._read_netstat_total() == 3000


def test_inconsistent_swap_values_fail_closed() -> None:
    with pytest.raises(ValueError, match="inconsistent"):
        parse_swapusage("vm.swapusage: total = 1.00G used = 2.00G free = 0.00G")

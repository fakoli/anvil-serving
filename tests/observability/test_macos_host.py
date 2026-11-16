from __future__ import annotations

from datetime import datetime, timezone


from anvil_serving.observability.probes import macos_host
from anvil_serving.observability.probes.macos_host import collect_macos_host
from anvil_serving.observability.schema import CapabilityStatus, TelemetrySample


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)
RAW = {
    "host_identity": "companion-node",
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


def test_permission_failure_is_explicit_and_never_zero() -> None:
    def denied():
        raise PermissionError("operation not permitted")

    samples = collect_macos_host(provider=denied, host_id="mini", collected_at=NOW)

    assert all(
        sample.capability_status is CapabilityStatus.PERMISSION_DENIED
        for sample in samples
    )
    assert all(sample.value is None for sample in samples)


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

from __future__ import annotations

from datetime import datetime, timezone

from anvil_serving.observability.probes.gpu_nvidia import collect_nvidia_gpus


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

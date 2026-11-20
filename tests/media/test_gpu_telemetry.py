"""GPU-free process-boundary tests for physical worker telemetry."""
import sys
import time

import pytest

from anvil_serving.media import worker_pool
from anvil_serving.media.errors import MediaError


UUID = "GPU-12345678-1234-1234-1234-123456789abc"
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="worker is Linux-only")


def sampler(monkeypatch, script, *, timeout=1):
    def helper(fd):
        return [sys.executable, "-c", script, str(fd)]
    from anvil_serving.media import gpu_telemetry as module
    monkeypatch.setattr(module, "_helper_command", helper)
    sampler = module.NvmlSampler(UUID, timeout=timeout)
    sampler.start()
    return sampler


HELPER = """import os, socket, struct, sys, time
s=socket.socket(fileno=int(sys.argv[1]))
n=0
while s.recv(1) == b'?':
 n+=1
 {reply}
"""


def test_telemetry_worker_reuses_helper_with_new_validated_readings(monkeypatch):
    helper = sampler(monkeypatch, HELPER.format(reply=f"s.sendall(struct.pack('!40sQQ', b'{UUID}', 32<<30, (n<<20)+1048575))"))
    try:
        assert helper.sample() == (32768, 1)
        assert helper.sample() == (32768, 2)
    finally:
        helper.close()


@pytest.mark.parametrize("reply", [
    "time.sleep(15)",
    "s.sendall(b'x'); s.close(); break",
    "s.sendall(struct.pack('!40sQQ', b'GPU-98765432-1234-1234-1234-123456789abc', 32<<30, 1<<20))",
    f"s.sendall(struct.pack('!40sQQ', b'{UUID}', 1<<20, 2<<20))",
])
def test_bad_late_partial_or_wrong_gpu_sample_is_never_returned(monkeypatch, reply):
    helper = sampler(monkeypatch, HELPER.format(reply=reply), timeout=.2)
    try:
        start = time.monotonic()
        with pytest.raises(MediaError) as error:
            helper.sample()
        assert error.value.code == "gpu_telemetry_unavailable"
        assert time.monotonic() - start < 2
        assert helper._socket is None
    finally:
        helper.close()


def test_uuid_validation_and_provider_config_is_explicit(tmp_path):
    import json
    data = {"schema": worker_pool.FEATURE, "root": str(tmp_path), "command": [sys.executable, "main.py"],
            "imageBudgetMiB": 13312, "contextReserveMiB": 1024, "deviceReserveMiB": 1024,
            "hostPolicy": "/policy", "hostSignal": "/signal", "imageCommitReserveGiB": 16,
            "workflows": [{"id": "image.a", "version": "v1", "profile": "", "mode": "images"}]}
    path = tmp_path / "worker.json"
    path.write_text(json.dumps(data))
    assert "gpuTelemetry" not in worker_pool.load_config(path)
    data["gpuTelemetry"] = {"provider": "nvml-v1", "deviceUuid": UUID}
    path.write_text(json.dumps(data))
    assert worker_pool.load_config(path)["gpuTelemetry"] == data["gpuTelemetry"]
    for setting in ({"provider": "nvml-v1"}, {"provider": "nvml-v2", "deviceUuid": UUID},
                    {"provider": "nvml-v1", "deviceUuid": "0"}, {"provider": "nvml-v1", "deviceUuid": UUID, "timeout": 8}):
        data["gpuTelemetry"] = setting
        path.write_text(json.dumps(data))
        with pytest.raises(MediaError):
            worker_pool.load_config(path)


def test_containment_closes_helper_and_recreates_only_after_reaping(monkeypatch):
    helper = sampler(monkeypatch, HELPER.format(
        reply=f"s.sendall(struct.pack('!40sQQ', b'{UUID}', 32<<30, n<<20))"))
    try:
        assert helper.sample() == (32768, 1)
        old = helper._process
        with helper.contain():
            assert helper._socket is None
            old.wait(timeout=1)  # Same role as WorkerPool's direct-descendant sweep.
        with pytest.raises(MediaError):
            helper.sample()  # A request can never respawn a provider.
        helper.start()
        assert helper.sample() == (32768, 1)
        assert helper._process is not old
    finally:
        helper.close()
    assert helper._process is None
    with pytest.raises(MediaError, match="physical GPU telemetry unavailable"):
        helper.sample()


def test_cannot_start_another_helper_while_previous_process_is_unreaped(monkeypatch):
    from types import SimpleNamespace
    from anvil_serving.media import gpu_telemetry as module
    helper = module.NvmlSampler(UUID)
    killed = []
    helper._process = SimpleNamespace(poll=lambda: None, kill=lambda: killed.append(True))
    monkeypatch.setattr(module.socket, "socketpair", lambda: pytest.fail("unproven old helper"))
    with pytest.raises(MediaError):
        helper.start()
    assert helper._process is not None and not killed
    with pytest.raises(MediaError):
        helper.sample()
    assert not killed and helper._process is not None


def test_native_helper_uses_device_wide_v1_bytes_from_exactly_one_device(monkeypatch):
    from types import SimpleNamespace
    from anvil_serving.media import gpu_telemetry as module

    def count(ptr):
        ptr._obj.value = 1
        return 0

    def get_handle(index, ptr):
        assert index.value == 0
        ptr._obj.value = 16384
        return 0

    def get_uuid(handle, buffer, length):
        assert handle.value == 16384 and length.value == len(buffer)
        buffer.value = UUID.encode()
        return 0

    def get_memory(handle, ptr):
        assert handle.value == 16384 and isinstance(ptr._obj, module._MemoryV1)
        ptr._obj.total = 32 << 30
        ptr._obj.free = (27 << 30) + 3
        ptr._obj.used = (5 << 30) - 3
        return 0

    library = SimpleNamespace(nvmlInit_v2=lambda: 0, nvmlDeviceGetCount_v2=count,
                              nvmlDeviceGetHandleByIndex_v2=get_handle,
                              nvmlDeviceGetUUID=get_uuid, nvmlDeviceGetMemoryInfo=get_memory)
    monkeypatch.setattr(module.ctypes, "CDLL", lambda soname: library if soname == "libnvidia-ml.so.1" else None)
    packets = []
    commands = iter((b"?", b""))
    sock = SimpleNamespace(recv=lambda _: next(commands), sendall=packets.append)
    module._native_serve(sock)
    assert [module._RESPONSE.unpack(packet) for packet in packets] == [(UUID.encode(), 32 << 30, (27 << 30) + 3)]

    def two_devices(ptr):
        ptr._obj.value = 2
        return 0

    library.nvmlDeviceGetCount_v2 = two_devices
    library.nvmlDeviceGetHandleByIndex_v2 = lambda *_args: pytest.fail("must refuse multi-GPU enumeration")
    commands = iter((b"?", b""))
    module._native_serve(sock)
    assert len(packets) == 1


def test_admission_validation_does_not_wait_for_busy_provider(monkeypatch):
    helper = sampler(monkeypatch, HELPER.format(
        reply=f"s.sendall(struct.pack('!40sQQ', b'{UUID}', 32<<30, n<<20))"))
    try:
        epoch = helper.epoch
        with helper._lock, pytest.raises(MediaError):
            with helper.validate(epoch):
                pytest.fail("admission must not wait on provider lock")
        with helper.validate(epoch):
            pass
        with helper.contain():
            pass
        with pytest.raises(MediaError):
            with helper.validate(epoch):
                pytest.fail("a prior generation cannot grant admission")
    finally:
        helper.close()


def test_low_latest_sample_fences_old_high_before_controller_can_latch_fault(monkeypatch):
    from anvil_serving.media import gpu_telemetry as module

    script = HELPER.format(reply=f"s.sendall(struct.pack('!40sQQ', b'{UUID}', 32<<30, (1500 if n == 1 else 1000)<<20))")
    monkeypatch.setattr(module, "_helper_command", lambda fd: [sys.executable, "-c", script, str(fd)])
    helper = module.NvmlSampler(UUID, reserve_mib=1024)
    helper.start()
    try:
        high_epoch, high = helper.sample_with_revision()
        assert high == (32768, 1500)
        low_epoch, low = helper.sample_with_revision()
        assert low == (32768, 1000) and low_epoch > high_epoch
        for epoch in (high_epoch, low_epoch):
            with pytest.raises(MediaError):
                with helper.validate(epoch):
                    pytest.fail("older headroom or current-epoch submit may not supersede a low latest sample")
        with pytest.raises(MediaError, match="physical GPU reserve was breached"):
            helper.sample()
    finally:
        helper.close()

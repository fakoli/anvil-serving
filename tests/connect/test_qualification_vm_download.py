import hashlib
import io
import os
import sys
from types import SimpleNamespace

import pytest

from anvil_serving.connect import _qualification_vm_download as child
from anvil_serving.connect import qualification_vm as parent
from anvil_serving.connect.qualification import QualificationError


URL = "https://cloud-images.ubuntu.com/releases/noble/release-20260826/SHA256SUMS"


class Response(io.BytesIO):
    headers = {}

    def geturl(self):
        return URL


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux file custody controls")
def test_download_completes_short_writes_before_hashing(tmp_path, monkeypatch):
    data = b"synthetic-download" * 100
    monkeypatch.setattr(child, "build_opener", lambda *args: SimpleNamespace(open=lambda *args, **kw: Response(data)))
    original = os.write
    monkeypatch.setattr(child.os, "write", lambda fd, value: original(fd, value[:7]))
    target = tmp_path / "download"
    assert child.download(URL, target, 4096) == (hashlib.sha256(data).hexdigest(), len(data))
    assert target.read_bytes() == data


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux file custody controls")
def test_parent_rejects_child_digest_that_does_not_match_completed_bytes(tmp_path, monkeypatch):
    target = tmp_path / "download"
    target.write_bytes(b"actual")
    target.chmod(0o600)
    monkeypatch.setattr(parent, "_command", lambda *a, **kw: (0, b'{"sha256":"wrong","bytes":6}'))
    with pytest.raises(QualificationError):
        parent._download(URL, target, maximum=4096, timeout=1)


def test_shared_deadline_cannot_reset_between_downloads(monkeypatch):
    monkeypatch.setattr(parent.time, "monotonic", lambda: 9.5)
    assert parent._remaining(10) == 0.5
    monkeypatch.setattr(parent.time, "monotonic", lambda: 10.1)
    with pytest.raises(QualificationError) as failure:
        parent._remaining(10)
    assert failure.value.code == "runner-timeout"


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux process controls")
def test_isolated_helper_is_killed_on_cumulative_deadline(tmp_path, monkeypatch):
    # Simulate a response that makes small, continual progress. Individual
    # reads need not time out; the process-level cumulative deadline must.
    from anvil_serving.connect import _qualification_vm_process as process
    import sys
    monkeypatch.setattr(parent, "_command", lambda args, timeout: (
        process.execute([sys.executable, "-I", "-c", "import time\nwhile True: time.sleep(.01)"],
                        home=tmp_path, timeout=timeout, cpus=(min(os.sched_getaffinity(0)),))
    ))
    with pytest.raises(QualificationError) as failure:
        parent._download(URL, tmp_path / "download", maximum=4096, timeout=0.15)
    assert failure.value.code == "runner-timeout"

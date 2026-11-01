from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


from anvil_serving import fleet_bootstrap
from anvil_serving.control_plane.bootstrap_package import (
    build_receiver_zipapp,
)


_ENTRIES = (
    "__main__.py",
    "anvil_serving/__init__.py",
    "anvil_serving/fleet_bootstrap.py",
    "anvil_serving/control_plane/__init__.py",
    "anvil_serving/control_plane/bootstrap_shim.py",
)
_MAIN = (
    b"from anvil_serving.control_plane.bootstrap_shim import main\n"
    b"raise SystemExit(main())\n"
)
_RECEIVER = b"""from anvil_serving.fleet_bootstrap import BootstrapReceiverProtocolError, encode_receiver_result
import sys

def main():
    sys.stdout.buffer.write(encode_receiver_result(BootstrapReceiverProtocolError()))
    return 0
"""


def _validator() -> bytes:
    return Path(fleet_bootstrap.__file__).read_bytes()


def _artifact() -> bytes:
    return build_receiver_zipapp(_validator(), _RECEIVER)


def _run(path: Path, cwd: Path) -> subprocess.CompletedProcess[bytes]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(cwd)
    return subprocess.run(
        [sys.executable, "-I", "-S", str(path)],
        cwd=cwd,
        env=environment,
        capture_output=True,
        timeout=5,
        check=False,
    )


def test_receiver_zipapp_runs_in_isolation_with_embedded_validator_only(tmp_path) -> None:
    artifact_path = tmp_path / "receiver.pyz"
    artifact_path.write_bytes(_artifact())
    poison = tmp_path / "poison"
    (poison / "anvil_serving").mkdir(parents=True)
    (poison / "anvil_serving" / "__init__.py").write_text(
        "raise RuntimeError('external package was imported')\n", encoding="utf-8"
    )

    completed = _run(artifact_path, poison)

    assert completed.returncode == 0
    assert completed.stderr == b""
    assert fleet_bootstrap.decode_receiver_result(completed.stdout) == (
        fleet_bootstrap.BootstrapReceiverProtocolError()
    )

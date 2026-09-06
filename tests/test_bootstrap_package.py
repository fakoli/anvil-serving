from __future__ import annotations

import hashlib
import io
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from anvil_serving import fleet_bootstrap
from anvil_serving.control_plane.bootstrap_package import (
    MAX_RECEIVER_SOURCE_BYTES,
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


def test_receiver_zipapp_is_deterministic_exact_and_preserves_source_bytes() -> None:
    artifact = _artifact()
    assert artifact == _artifact()
    assert hashlib.sha256(artifact).hexdigest().encode("ascii") not in artifact
    with zipfile.ZipFile(io.BytesIO(artifact), "r") as archive:
        infos = archive.infolist()
        assert tuple(info.filename for info in infos) == _ENTRIES
        assert archive.comment == b""
        expected = (stat.S_IFREG | 0o600)
        for info in infos:
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.compress_type == zipfile.ZIP_STORED
            assert info.create_system == 3
            assert (info.external_attr >> 16) & 0xFFFF == expected
            assert info.extra == b"" and info.comment == b""
        assert archive.read("__main__.py") == _MAIN
        assert archive.read("anvil_serving/__init__.py") == b""
        assert archive.read("anvil_serving/control_plane/__init__.py") == b""
        assert archive.read("anvil_serving/fleet_bootstrap.py") == _validator()
        assert archive.read("anvil_serving/control_plane/bootstrap_shim.py") == _RECEIVER


def test_receiver_zipapp_identity_changes_when_one_source_byte_changes() -> None:
    validator = _validator()
    changed = build_receiver_zipapp(validator, _RECEIVER + b"# retained byte\n")
    assert hashlib.sha256(changed).digest() != hashlib.sha256(_artifact()).digest()


@pytest.mark.parametrize(
    "validator, receiver",
    (
        (None, _RECEIVER),
        (bytearray(b"x"), _RECEIVER),
        (b"", _RECEIVER),
        (b"\xff", _RECEIVER),
        (b"x\x00y", _RECEIVER),
        (b"if :\n", _RECEIVER),
        (b"x" * (MAX_RECEIVER_SOURCE_BYTES + 1), _RECEIVER),
        (b"pass\n", None),
        (b"pass\n", bytearray(b"x")),
        (b"pass\n", b""),
        (b"pass\n", b"\xff"),
        (b"pass\n", b"x\x00y"),
        (b"pass\n", b"if :\n"),
        (b"pass\n", b"x" * (MAX_RECEIVER_SOURCE_BYTES + 1)),
    ),
    ids=(
        "validator-none",
        "validator-bytearray",
        "validator-empty",
        "validator-invalid-utf8",
        "validator-nul",
        "validator-syntax",
        "validator-oversized",
        "receiver-none",
        "receiver-bytearray",
        "receiver-empty",
        "receiver-invalid-utf8",
        "receiver-nul",
        "receiver-syntax",
        "receiver-oversized",
    ),
)
def test_receiver_zipapp_rejects_invalid_source_inputs_without_echoing_them(validator, receiver) -> None:
    with pytest.raises(fleet_bootstrap.BootstrapContractError) as caught:
        build_receiver_zipapp(validator, receiver)  # type: ignore[arg-type]
    assert caught.value.code == "invalid-contract"
    assert "receiver source is invalid" == str(caught.value)


def test_receiver_zipapp_syntax_validation_does_not_execute_source() -> None:
    artifact = build_receiver_zipapp(
        _validator(), b"raise RuntimeError('must not execute while packaging')\n"
    )
    assert artifact


def test_receiver_zipapp_accepts_the_exact_source_bound_for_both_slots() -> None:
    maximum_source = b"#" + b"x" * (MAX_RECEIVER_SOURCE_BYTES - 2) + b"\n"
    assert len(maximum_source) == MAX_RECEIVER_SOURCE_BYTES
    assert build_receiver_zipapp(maximum_source, maximum_source)


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


def test_receiver_zipapp_without_embedded_validator_fails_in_isolation(tmp_path) -> None:
    artifact_path = tmp_path / "receiver.pyz"
    with zipfile.ZipFile(io.BytesIO(_artifact()), "r") as source:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as target:
            for info in source.infolist():
                if info.filename != "anvil_serving/fleet_bootstrap.py":
                    target.writestr(info, source.read(info))
    artifact_path.write_bytes(output.getvalue())

    completed = _run(artifact_path, tmp_path)

    assert completed.returncode != 0
    assert completed.stdout == b""

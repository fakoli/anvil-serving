"""Deterministic, inert receiver ZIP application construction."""

from __future__ import annotations

import io
import stat
import zipfile

from ..fleet_bootstrap import BootstrapContractError, BootstrapErrorCode


MAX_RECEIVER_SOURCE_BYTES = 256 * 1024
MAX_RECEIVER_ZIPAPP_BYTES = 1024 * 1024
_DOS_EPOCH = (1980, 1, 1, 0, 0, 0)
_MAIN_SOURCE = (
    b"from anvil_serving.control_plane.bootstrap_shim import main\n"
    b"raise SystemExit(main())\n"
)
_ENTRIES = (
    "__main__.py",
    "anvil_serving/__init__.py",
    "anvil_serving/fleet_bootstrap.py",
    "anvil_serving/control_plane/__init__.py",
    "anvil_serving/control_plane/bootstrap_shim.py",
)


def _invalid_source() -> BootstrapContractError:
    return BootstrapContractError(
        BootstrapErrorCode.INVALID_CONTRACT.value,
        "receiver source is invalid",
    )


def _validated_source(value: object) -> bytes:
    if type(value) is not bytes or not value or len(value) > MAX_RECEIVER_SOURCE_BYTES:
        raise _invalid_source()
    if b"\x00" in value:
        raise _invalid_source()
    try:
        source = value.decode("utf-8")
        compile(source, "<receiver-source>", "exec")
    except (SyntaxError, UnicodeError, ValueError):
        raise _invalid_source() from None
    return value


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_DOS_EPOCH)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    info.extra = b""
    info.comment = b""
    return info


def build_receiver_zipapp(validator_source: bytes, receiver_source: bytes) -> bytes:
    """Return a deterministic ZIP application without executing its sources."""

    validator = _validated_source(validator_source)
    receiver = _validated_source(receiver_source)
    output = io.BytesIO()
    try:
        with zipfile.ZipFile(
            output,
            "w",
            compression=zipfile.ZIP_STORED,
            allowZip64=False,
        ) as archive:
            archive.comment = b""
            for name, source in zip(
                _ENTRIES,
                (_MAIN_SOURCE, b"", validator, b"", receiver),
                strict=True,
            ):
                archive.writestr(_zip_info(name), source)
    except (OSError, ValueError, zipfile.BadZipFile):
        raise BootstrapContractError(
            BootstrapErrorCode.INVALID_CONTRACT.value,
            "receiver artifact could not be built",
        ) from None
    artifact = output.getvalue()
    if len(artifact) > MAX_RECEIVER_ZIPAPP_BYTES:
        raise BootstrapContractError(
            BootstrapErrorCode.INVALID_CONTRACT.value,
            "receiver artifact exceeds the supported bound",
        )
    return artifact

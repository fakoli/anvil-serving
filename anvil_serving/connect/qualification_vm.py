"""Pinned Ubuntu cloud-image preparation for the isolated Connect guest.

This module only verifies and caches a public base image.  It neither creates
an overlay nor starts QEMU; the later guest runner owns those lifecycle steps.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
from typing import Any

from ._qualification_vm_process import execute
from .qualification import QualificationConfig, QualificationError, _error, _private_directory, _read_config

_SCHEMA = "anvil-connect.qualification-vm/v1"
_PINS_SCHEMA = "anvil-connect.qualification-vm-pins/v1"
_PIN_PATH = "connect/test/vm/pins.json"
_CACHE_NAME = "ubuntu-24.04-server-cloudimg-amd64.img"
_RECEIPT_NAME = "vm-image.json"
_GPGV = Path("/usr/bin/gpgv")
_QEMU_IMG = Path("/usr/bin/qemu-img")
_KEYRING = Path("/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg")
_MAX_IMAGE = 768 * 1024 * 1024
_MAX_VIRTUAL_SIZE = 8 * 1024 * 1024 * 1024
_MAX_METADATA = 64 * 1024
_DOWNLOAD_TIMEOUT = 120
_COMMAND_TIMEOUT = 20
_SHA256 = re.compile(r"[0-9a-f]{64}$")
_FINGERPRINT = re.compile(r"[0-9A-F]{40}$")
_VERSION = re.compile(r"(?<![A-Za-z0-9])v?[0-9]+(?:\.[0-9]+){1,3}(?:[-+._A-Za-z0-9]*)?")
_BASE_URL = "https://cloud-images.ubuntu.com/releases/noble/release-20260826/"
_IMAGE_URL = _BASE_URL + _CACHE_NAME
_SUMS_URL = _BASE_URL + "SHA256SUMS"
_SIGNATURE_URL = _BASE_URL + "SHA256SUMS.gpg"


def _source_error(message: str) -> QualificationError:
    return _error("source-invalid", message)


def _private_cache(config: QualificationConfig) -> Path:
    _private_directory(config.artifact_root, create=True)
    cache = config.artifact_root / ".vm-images"
    _private_directory(cache, create=True)
    return cache


def _read_fd(path: Path, *, maximum: int, owner: int | None = None, mode: int | None = None) -> bytes:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size < 0 or info.st_size > maximum:
            raise OSError("unsafe file")
        if owner is not None and info.st_uid != owner:
            raise OSError("unexpected owner")
        if mode is not None and stat.S_IMODE(info.st_mode) != mode:
            raise OSError("unexpected mode")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        if len(value) > maximum:
            raise OSError("oversized file")
        return value
    except OSError as exc:
        raise _source_error("VM preparation input is unavailable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read_pin(config: QualificationConfig) -> bytes:
    directory: int | None = None
    descriptor: int | None = None
    try:
        root = config.source_root
        root_info = root.lstat()
        if root.is_symlink() or not stat.S_ISDIR(root_info.st_mode):
            raise OSError("unsafe source root")
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in Path(_PIN_PATH).parts[:-1]:
            next_directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        descriptor = os.open(Path(_PIN_PATH).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_METADATA:
            raise OSError("unsafe pins")
        chunks: list[bytes] = []
        remaining = _MAX_METADATA + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        if len(value) > _MAX_METADATA:
            raise OSError("oversized pins")
        return value
    except OSError as exc:
        raise _source_error("VM preparation pins are unavailable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


def _pins(config: QualificationConfig) -> dict[str, Any]:
    try:
        raw = json.loads(_read_pin(config).decode("utf-8"))
        expected = {
            "schema", "image_url", "image_sha256", "max_image_bytes", "sums_url", "sums_sha256",
            "signature_url", "signature_sha256", "signer_fingerprint", "keyring_sha256",
        }
        if not isinstance(raw, dict) or set(raw) != expected or raw["schema"] != _PINS_SCHEMA:
            raise ValueError
        if (raw["image_url"], raw["sums_url"], raw["signature_url"]) != (_IMAGE_URL, _SUMS_URL, _SIGNATURE_URL):
            raise ValueError
        if any(not isinstance(raw[name], str) or not _SHA256.fullmatch(raw[name]) for name in ("image_sha256", "sums_sha256", "signature_sha256", "keyring_sha256")):
            raise ValueError
        if not isinstance(raw["signer_fingerprint"], str) or not _FINGERPRINT.fullmatch(raw["signer_fingerprint"]):
            raise ValueError
        if type(raw["max_image_bytes"]) is not int or not 0 < raw["max_image_bytes"] <= _MAX_IMAGE:
            raise ValueError
        return raw
    except (UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise _source_error("VM preparation pins are invalid") from exc


def _system_file(path: Path, *, executable: bool, deadline: float) -> dict[str, str]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise _error("tool-invalid", "VM verification tool is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or (executable and not info.st_mode & 0o111):
        raise _error("tool-invalid", "VM verification tool is unsafe")
    digest, _ = _sha256(path, maximum=64 * 1024 * 1024, owner=0, deadline=deadline)
    return {"sha256": digest}


def _command(argv: list[str], *, timeout: float) -> tuple[int, bytes]:
    with tempfile.TemporaryDirectory(prefix="acq-vm-tool-") as home:
        try:
            result = execute(argv, home=Path(home), timeout=timeout,
                             cpus=tuple(sorted(os.sched_getaffinity(0))[-2:]),
                             maximum_output=_MAX_METADATA)
            return result.returncode, result.output
        except QualificationError as exc:
            raise _error("tool-invalid", "VM verification tool failed") from exc


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _error("runner-timeout", "VM preparation exceeded its deadline")
    return remaining


def _tool_metadata(*, deadline: float) -> dict[str, dict[str, str]]:
    values: dict[str, dict[str, str]] = {}
    for name, path in (("gpgv", _GPGV), ("qemu_img", _QEMU_IMG)):
        item = _system_file(path, executable=True, deadline=deadline)
        code, output = _command([str(path), "--version"], timeout=min(5, _remaining(deadline)))
        text = output.decode("utf-8", "replace").splitlines()
        item["version"] = (_VERSION.search(text[0]).group(0) if code == 0 and text and _VERSION.search(text[0]) else "unavailable")
        values[name] = item
    values["keyring"] = _system_file(_KEYRING, executable=False, deadline=deadline)
    return values


def _sha256(path: Path, *, maximum: int, owner: int | None = None, mode: int | None = None, deadline: float | None = None) -> tuple[str, int]:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size < 0 or info.st_size > maximum:
            raise OSError("unsafe image")
        if owner is not None and info.st_uid != owner:
            raise OSError("unexpected owner")
        if mode is not None and stat.S_IMODE(info.st_mode) != mode:
            raise OSError("unexpected mode")
        digest = hashlib.sha256()
        total = 0
        while True:
            if deadline is not None:
                _remaining(deadline)
            block = os.read(descriptor, 64 * 1024)
            if not block:
                break
            total += len(block)
            if total > maximum:
                raise OSError("oversized image")
            digest.update(block)
        return digest.hexdigest(), total
    except OSError as exc:
        raise _source_error("VM image cache is invalid") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _download(url: str, target: Path, *, maximum: int, timeout: float) -> tuple[str, int]:
    deadline = time.monotonic() + timeout
    helper = Path(__file__).with_name("_qualification_vm_download.py")
    code, output = _command([sys.executable, "-I", str(helper), url, str(target), str(maximum)], timeout=timeout)
    try:
        value = json.loads(output)
        if code != 0 or not isinstance(value, dict) or set(value) != {"sha256", "bytes"}:
            raise ValueError
        actual = _sha256(target, maximum=maximum, owner=os.geteuid(), mode=0o600, deadline=deadline)
        if value != {"sha256": actual[0], "bytes": actual[1]}:
            raise ValueError
        return actual
    except (ValueError, TypeError, QualificationError) as exc:
        raise _source_error("VM image download failed") from exc


def _signed_sums(cache: Path, pins: dict[str, Any], *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    sums = cache / ".SHA256SUMS.new"
    signature = cache / ".SHA256SUMS.gpg.new"
    try:
        sums_hash, _ = _download(pins["sums_url"], sums, maximum=_MAX_METADATA, timeout=_remaining(deadline))
        signature_hash, _ = _download(pins["signature_url"], signature, maximum=_MAX_METADATA, timeout=_remaining(deadline))
        if sums_hash != pins["sums_sha256"] or signature_hash != pins["signature_sha256"]:
            raise _source_error("VM signed checksums do not match their pins")
        code, output = _command([str(_GPGV), "--status-fd", "1", "--keyring", str(_KEYRING), str(signature), str(sums)], timeout=min(_COMMAND_TIMEOUT, _remaining(deadline)))
        statuses = output.decode("ascii", "ignore").splitlines()
        valid = {line.split()[2] for line in statuses if line.startswith("[GNUPG:] VALIDSIG ") and len(line.split()) >= 3}
        if code != 0 or valid != {pins["signer_fingerprint"]}:
            raise _source_error("VM signed checksums have an unexpected signer")
        expected = f"{pins['image_sha256']}  {_CACHE_NAME}"
        if expected not in sums.read_text(encoding="ascii", errors="strict").splitlines():
            raise _source_error("VM image checksum is absent from signed checksums")
    finally:
        for path in (sums, signature):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise _source_error("VM temporary verification data cannot be removed") from exc


def _image_info(image: Path, *, timeout: float = _COMMAND_TIMEOUT) -> int:
    code, output = _command([str(_QEMU_IMG), "info", "--output=json", str(image)], timeout=timeout)
    try:
        value = json.loads(output.decode("utf-8"))
        forbidden = {"backing-filename", "full-backing-filename", "data-file", "data-file-raw"}

        def unsafe(item: Any) -> bool:
            if isinstance(item, dict):
                return any((key in forbidden and bool(child)) or unsafe(child) for key, child in item.items())
            if isinstance(item, list):
                return any(unsafe(child) for child in item)
            return False

        size = value["virtual-size"]
        if code != 0 or not isinstance(value, dict) or value.get("format") != "qcow2" or type(size) is not int or not 0 < size <= _MAX_VIRTUAL_SIZE or unsafe(value):
            raise ValueError
        return size
    except (UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise _source_error("VM image format is unsafe") from exc


def _receipt(root: Path, value: dict[str, Any]) -> Path:
    target = root / _RECEIPT_NAME
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".vm-image.", suffix=".new", dir=root)
        temporary = Path(name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
        os.chmod(target, 0o600)
    except OSError as exc:
        raise _error("staging-failed", "VM image receipt could not be written") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
    return target


def _read_receipt(root: Path) -> dict[str, Any] | None:
    path = root / _RECEIPT_NAME
    try:
        raw = _read_fd(path, maximum=_MAX_METADATA, owner=os.geteuid(), mode=0o600)
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict) or value.get("schema") != _SCHEMA:
            return None
        return value
    except (QualificationError, UnicodeError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _valid_cached_image(cache: Path, pins: dict[str, Any], *, deadline: float) -> tuple[str, int, int] | None:
    image = cache / _CACHE_NAME
    try:
        digest, size = _sha256(image, maximum=pins["max_image_bytes"], owner=os.geteuid(), mode=0o400, deadline=deadline)
        if digest != pins["image_sha256"]:
            return None
        return digest, size, _image_info(image, timeout=min(_COMMAND_TIMEOUT, _remaining(deadline)))
    except QualificationError as exc:
        if exc.code == "runner-timeout":
            raise
        _remaining(deadline)
        return None


def _remove_invalid_cache(image: Path) -> None:
    try:
        info = image.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise _source_error("VM image cache is unavailable") from exc
    if image.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
        raise _source_error("VM image cache is unsafe")
    try:
        image.unlink()
    except OSError as exc:
        raise _source_error("VM image cache cannot be replaced") from exc


def prepare(config_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Verify and cache the sole pinned base image for a later isolated guest."""
    path = Path(config_path) if config_path is not None else Path.home() / ".config/anvil-connect/qualification.toml"
    config = _read_config(path)
    deadline = time.monotonic() + min(_DOWNLOAD_TIMEOUT, config.timeout_seconds)
    cache = _private_cache(config)
    pins = _pins(config)
    tools = _tool_metadata(deadline=deadline)
    _remaining(deadline)
    if tools["keyring"]["sha256"] != pins["keyring_sha256"]:
        raise _error("tool-invalid", "VM verification keyring does not match its pin")
    image = cache / _CACHE_NAME
    cached = _valid_cached_image(cache, pins, deadline=deadline)
    _remaining(deadline)
    reused = cached is not None
    if cached is None:
        _remove_invalid_cache(image)
        temporary: Path | None = None
        try:
            _signed_sums(cache, pins, timeout=_remaining(deadline))
            descriptor, name = tempfile.mkstemp(prefix=".vm-image.", suffix=".download", dir=cache)
            os.close(descriptor)
            temporary = Path(name)
            temporary.unlink()
            digest, size = _download(pins["image_url"], temporary, maximum=pins["max_image_bytes"], timeout=_remaining(deadline))
            if digest != pins["image_sha256"]:
                raise _source_error("VM image does not match its pin")
            # Recheck completed bytes even when a future downloader implementation
            # changes; reported network bytes are not publication evidence.
            if _sha256(temporary, maximum=pins["max_image_bytes"], owner=os.geteuid(), deadline=deadline) != (digest, size):
                raise _source_error("VM image completed bytes do not match their pin")
            virtual_size = _image_info(temporary, timeout=min(_COMMAND_TIMEOUT, _remaining(deadline)))
            _remaining(deadline)
            os.chmod(temporary, 0o400)
            os.replace(temporary, image)
            temporary = None
            cached = digest, size, virtual_size
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise _source_error("VM image temporary data cannot be removed") from exc
    assert cached is not None
    digest, image_bytes, virtual_size = cached
    receipt_value = {
        "schema": _SCHEMA,
        "image": _CACHE_NAME,
        "image_sha256": digest,
        "image_bytes": image_bytes,
        "virtual_size": virtual_size,
        "image_url": pins["image_url"],
        "sums_sha256": pins["sums_sha256"],
        "signature_sha256": pins["signature_sha256"],
        "signer_fingerprint": pins["signer_fingerprint"],
        "keyring_sha256": pins["keyring_sha256"],
        "tools": tools,
        "reused": reused,
    }
    _remaining(deadline)
    previous = _read_receipt(config.artifact_root)
    if previous != receipt_value:
        _receipt(config.artifact_root, receipt_value)
    _remaining(deadline)
    return {
        "schema": _SCHEMA,
        "ok": True,
        "artifact_dir": str(cache),
        "image_path": str(image),
        "image_sha256": digest,
        "image_bytes": image_bytes,
        "virtual_size": virtual_size,
        "reused": reused,
    }

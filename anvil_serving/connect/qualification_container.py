"""Explicit, digest-pinned container preparation for Connect qualification.

Unlike :mod:`qualification`, this module may download public build inputs.  Its
build context is deliberately closed so it never sends an operator checkout or
ambient Docker credentials to the daemon.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import tempfile
import time
from typing import Any

from .qualification import QualificationConfig, _error, _private_directory, _read_config

_SCHEMA = "anvil-connect.qualification-container/v1"
_LABEL = "io.fakoli.anvil-connect.qualification.input-digest"
_DOCKER = "/usr/bin/docker"
_DOCKER_HOST = "unix:///var/run/docker.sock"
_MAX_FILE = 2 * 1024 * 1024
_MAX_INSPECT = 32 * 1024
_BUILD_TIMEOUT = 600
_CONTEXT = (
    ("connect/test/container/Dockerfile", "Dockerfile"),
    ("connect/test/container/pins.json", "pins.json"),
    ("connect/test/container/install_tools.py", "install_tools.py"),
    ("connect/lab/edge-tools.json", "edge-tools.json"),
    ("connect/transport.lock.json", "transport.lock.json"),
    ("connect/go.mod", "go.mod"),
    ("connect/go.sum", "go.sum"),
    ("connect/package.json", "package.json"),
    ("connect/package-lock.json", "package-lock.json"),
)


def _regular_under(root: Path, relative: str) -> bytes:
    """Read one closed context file without following any source ancestor."""
    parts = Path(relative).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise _error("source-invalid", "qualification container input is unsafe")
    directory: int | None = None
    descriptor: int | None = None
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in parts[:-1]:
            next_directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= _MAX_FILE:
            raise OSError("unsafe context input")
        content = bytearray()
        while len(content) <= _MAX_FILE:
            chunk = os.read(descriptor, min(64 * 1024, _MAX_FILE + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        if len(content) > _MAX_FILE:
            raise OSError("oversized context input")
        return bytes(content)
    except OSError as exc:
        raise _error("source-invalid", "qualification container input is unavailable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


def _context_inputs(config: QualificationConfig) -> list[tuple[str, bytes]]:
    try:
        root_info = config.source_root.lstat()
    except OSError as exc:
        raise _error("source-invalid", "qualification container source is unavailable") from exc
    if config.source_root.is_symlink() or not stat.S_ISDIR(root_info.st_mode):
        raise _error("source-invalid", "qualification container source is unsafe")
    return [(destination, _regular_under(config.source_root, relative)) for relative, destination in _CONTEXT]


def _pins(inputs: dict[str, bytes]) -> tuple[str, str, str]:
    try:
        pins = json.loads(inputs["pins.json"].decode("utf-8"))
        package = json.loads(inputs["package.json"].decode("utf-8"))
        lock = json.loads(inputs["package-lock.json"].decode("utf-8"))
        required = {"schema", "platform", "base_image", "playwright_version", "nss_tools_version", "go", "sources"}
        if not isinstance(pins, dict) or set(pins) != required or pins["schema"] != "anvil-connect.qualification-container-pins/v1":
            raise ValueError
        base, platform, playwright, nss = pins["base_image"], pins["platform"], pins["playwright_version"], pins["nss_tools_version"]
        if not all(isinstance(value, str) and value for value in (base, platform, playwright, nss)) or platform != "linux/amd64":
            raise ValueError
        dockerfile = inputs["Dockerfile"].decode("utf-8")
        if f"FROM {base}\n" not in dockerfile or f"libnss3-tools={nss}" not in dockerfile:
            raise ValueError
        if package["devDependencies"]["@playwright/test"] != playwright or lock["packages"]["node_modules/playwright"]["version"] != playwright:
            raise ValueError
    except (UnicodeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _error("source-invalid", "qualification container pins do not match build inputs") from exc
    return base, platform, playwright


def _input_digest(inputs: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for name, data in inputs:
        encoded = name.encode("ascii")
        digest.update(len(encoded).to_bytes(2, "big"))
        digest.update(encoded)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _stop_docker(process: subprocess.Popen[bytes] | None) -> None:
    if process is None:
        return
    try:
        # The isolated group can outlive its leader (for example a buildx helper).
        # Always finish the group, including after an already-observed leader exit.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=2)
    except (OSError, subprocess.SubprocessError):
        raise _error("runner-failed", "qualification Docker helper cleanup failed", stage="cleanup") from None


def _docker(args: list[str], *, env: dict[str, str], capture: bool) -> tuple[int, bytes]:
    """Run Docker only through the local Unix socket and never retain stderr."""
    binary = Path(_DOCKER)
    try:
        info = binary.lstat()
        socket = os.stat("/var/run/docker.sock")
    except OSError as exc:
        raise _error("tool-invalid", "local Docker is unavailable") from exc
    if binary.is_symlink() or not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111 or not stat.S_ISSOCK(socket.st_mode):
        raise _error("tool-invalid", "local Docker is unavailable")
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen([str(binary), "--host", _DOCKER_HOST, *args], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=env, start_new_session=True, close_fds=True)
        if not capture:
            process.wait(timeout=_BUILD_TIMEOUT)
            return process.returncode, b""
        assert process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        output = bytearray()
        deadline = time.monotonic() + 15
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                for key, _ in selector.select(min(0.05, remaining)):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    elif len(output) + len(chunk) > _MAX_INSPECT:
                        raise OSError("Docker inspect output exceeds bound")
                    else:
                        output.extend(chunk)
            if selector.get_map():
                raise OSError("Docker inspect timed out")
        finally:
            selector.close()
        process.wait(timeout=2)
        return process.returncode, bytes(output)
    except KeyboardInterrupt:
        _stop_docker(process)
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        _stop_docker(process)
        raise _error("runner-failed", "qualification container Docker operation failed") from exc
    finally:
        if process is not None and process.stdout is not None:
            process.stdout.close()


def _image(tag: str, digest: str, *, env: dict[str, str]) -> str | None:
    code, output = _docker(["image", "inspect", tag], env=env, capture=True)
    if code != 0:
        return None
    try:
        value = json.loads(output.decode("utf-8"))
        image = value[0]
        image_id = image["Id"]
        label = image["Config"]["Labels"][_LABEL]
        if not isinstance(image_id, str) or len(image_id) != 71 or not image_id.startswith("sha256:") or any(c not in "0123456789abcdef" for c in image_id[7:]) or label != digest:
            raise ValueError
    except (UnicodeError, IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return image_id


def _cached_receipt(root: Path, digest: str) -> str | None:
    path = root / "container-image.json"
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > _MAX_INSPECT:
                return None
            raw = handle.read(_MAX_INSPECT + 1)
        value = json.loads(raw.decode("utf-8"))
        if set(value) != {"schema", "image_id", "input_digest", "base_image", "platform"} or value["schema"] != _SCHEMA or value["input_digest"] != digest:
            return None
        image_id = value["image_id"]
        if not isinstance(image_id, str) or len(image_id) != 71 or not image_id.startswith("sha256:") or any(character not in "0123456789abcdef" for character in image_id[7:]):
            return None
        return image_id
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _receipt(root: Path, value: dict[str, str]) -> Path:
    target = root / "container-image.json"
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".container-image.", suffix=".new", dir=root)
        temporary = Path(name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
        os.chmod(target, 0o600)
    except OSError as exc:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        raise _error("staging-failed", "qualification container receipt could not be written") from exc
    return target


def prepare(config_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Build or reuse the explicit pinned qualification image.

    This is the only Connect qualification path allowed to contact public build
    sources, through Docker's declared build process.
    """
    path = Path(config_path) if config_path is not None else Path.home() / ".config/anvil-connect/qualification.toml"
    config = _read_config(path)
    _private_directory(config.artifact_root, create=True)
    inputs = _context_inputs(config)
    base, platform, _playwright = _pins(dict(inputs))
    digest = _input_digest(inputs)
    tag = "anvil-connect-qualification:sha256-" + digest
    with tempfile.TemporaryDirectory(prefix="container-context-", dir=config.artifact_root) as context, tempfile.TemporaryDirectory(prefix="docker-config-", dir=config.artifact_root) as docker_config:
        context_root, config_root = Path(context), Path(docker_config)
        _private_directory(context_root)
        _private_directory(config_root)
        for destination, data in inputs:
            target = context_root / destination
            target.write_bytes(data)
            os.chmod(target, 0o600 if destination != "install_tools.py" else 0o700)
        environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "DOCKER_CONFIG": str(config_root)}
        image_id = _image(tag, digest, env=environment)
        reused = image_id is not None and _cached_receipt(config.artifact_root, digest) == image_id
        if not reused:
            code, _ = _docker(["build", "--platform", platform, "--label", f"{_LABEL}={digest}", "--tag", tag, str(context_root)], env=environment, capture=False)
            if code != 0:
                raise _error("runner-failed", "qualification container build failed")
            image_id = _image(tag, digest, env=environment)
            if image_id is None:
                raise _error("runner-failed", "qualification container image validation failed")
    receipt = _receipt(config.artifact_root, {"schema": _SCHEMA, "image_id": image_id, "input_digest": digest, "base_image": base, "platform": platform})
    return {"schema": _SCHEMA, "image_id": image_id, "input_digest": digest, "base_image": base, "platform": platform, "reused": reused, "receipt": str(receipt)}

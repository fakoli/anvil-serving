"""Offline systemd guest contract for Connect service-isolation qualification.

This runner accepts only a fixed synthetic protocol.  It never accepts an
operator deployment, network endpoint, credentials, host share, or guest
command as input.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tarfile
import time
from typing import Any
import uuid
import xml.etree.ElementTree as ET

from ._qualification_vm_process import execute
from .qualification import (
    QualificationConfig, QualificationError, _error, _private_directory,
    _read_config, _safe_cache, _source_metadata, _locks,
)
from .qualification_vm import (
    _CACHE_NAME, _MAX_VIRTUAL_SIZE, _pins, _private_cache, _read_receipt,
    _valid_cached_image,
)

_GiB = 1024 ** 3
_CASES = (
    "legacy_activation_rejected_pre_state",
    "rendered_units_isolated",
    "managed_gateway_connector_readiness",
    "caddy_tls_authelia_discovery",
    "ingress_peer_denials_then_edge_success",
    "ingress_socket_ownership",
    "role_private_state_and_admin_denials",
    "managed_restart_and_rollback",
)
_MARKER = b"ANVIL_CONNECT_ISOLATION_GUEST_RESULT "
_MAX_RESULT = 64 * 1024
_MAX_PAYLOAD = 512 * 1024 * 1024
_MAX_FILES = 4096
_TIMEOUT = 600
_QEMU = Path("/usr/bin/qemu-system-x86_64")
_QEMU_IMG = Path("/usr/bin/qemu-img")
_XORRISO = Path("/usr/bin/xorriso")
_FIRMWARE = Path("/usr/share/seabios/bios.bin")
_SCHEMA = "anvil-connect.isolation-qualification/v1"
_PAYLOAD_SCHEMA = "anvil-connect.isolation-guest-payload/v1"
_BUILD_SCHEMA = "anvil-connect.isolation-guest-build/v1"


def _cases(raw: bytes) -> list[dict[str, str]]:
    def closed_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        if len(raw) > _MAX_RESULT:
            raise ValueError
        data = json.loads(raw, object_pairs_hook=closed_object)
        if (not isinstance(data, dict) or set(data) != {"schema", "cases", "ok"}
                or data["schema"] != "anvil-connect.isolation-guest/v1"
                or type(data["ok"]) is not bool or not isinstance(data["cases"], list)
                or len(data["cases"]) != len(_CASES)):
            raise ValueError
        for case, name in zip(data["cases"], _CASES, strict=True):
            if (not isinstance(case, dict) or set(case) != {"name", "status"}
                    or case["name"] != name or case["status"] not in {"passed", "failed"}):
                raise ValueError
        if data["ok"] != all(case["status"] == "passed" for case in data["cases"]):
            raise ValueError
        return data["cases"]
    except (UnicodeError, TypeError, ValueError, KeyError) as exc:
        failure = _error("runner-failed", "VM guest result is invalid", execution_started=True, stage="execution")
        failure.reason = "record-invalid"
        raise failure from exc


def _capacity(root: Path) -> tuple[int, ...]:
    if os.geteuid() == 0 or os.getegid() == 0:
        raise _error("config-invalid", "VM qualification requires an unprivileged host user")
    cpus = tuple(sorted(os.sched_getaffinity(0))[-2:])
    try:
        memory = Path("/proc/meminfo").read_text(encoding="ascii")
        available = next(int(line.split()[1]) * 1024 for line in memory.splitlines() if line.startswith("MemAvailable:"))
        descriptor = os.open("/dev/kvm", os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        os.close(descriptor)
        if len(cpus) != 2 or available < 16 * _GiB or shutil.disk_usage(root).free < 16 * _GiB:
            raise OSError
    except (OSError, ValueError, StopIteration) as exc:
        raise _error("runner-unavailable", "VM qualification capacity or KVM is unavailable") from exc
    return cpus


def _qemu_argv(base: int, overlay: int, seed: int, payload: int, firmware: int) -> list[str]:
    descriptors = (base, overlay, seed, payload, firmware)
    if any(type(fd) is not int or fd < 3 for fd in descriptors) or len(set(descriptors)) != len(descriptors):
        raise _error("config-invalid", "VM file descriptors are invalid")

    def descriptor(fd: int) -> str:
        return f"/proc/self/fd/{fd}"

    def block(value: dict[str, Any]) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    return [
        str(_QEMU), "-no-user-config", "-nodefaults", "-machine", "q35,accel=kvm",
        "-cpu", "host", "-smp", "2", "-m", "2048", "-nic", "none",
        "-display", "none", "-monitor", "none", "-serial", "stdio", "-no-reboot",
        "-bios", descriptor(firmware), "-boot", "order=c,strict=on",
        "-blockdev", block({"driver": "file", "filename": descriptor(base), "node-name": "base-file", "read-only": True}),
        "-blockdev", block({"driver": "qcow2", "file": "base-file", "node-name": "base", "read-only": True}),
        "-blockdev", block({"driver": "qcow2", "file": {"driver": "file", "filename": descriptor(overlay)}, "backing": "base", "node-name": "disk"}),
        "-device", "virtio-blk-pci,drive=disk",
        "-drive", f"file={descriptor(seed)},format=raw,media=cdrom,readonly=on,if=ide,index=0",
        "-drive", f"file={descriptor(payload)},format=raw,media=cdrom,readonly=on,if=ide,index=1",
    ]


def _seed(root: Path, instance: str) -> None:
    if len(instance) != 32 or any(char not in "0123456789abcdef" for char in instance):
        raise _error("config-invalid", "VM instance identity is invalid")
    user_data = """#cloud-config
users: []
disable_root: true
ssh_pwauth: false
ssh_deletekeys: true
ssh_genkeytypes: []
datasource_list: [NoCloud]
network: {config: disabled}
package_update: false
package_upgrade: false
bootcmd:
  - [systemctl, mask, --now, ssh.service, ssh.socket, sshd.service, sshd.socket]
runcmd:
  - [mkdir, -p, /opt/anvil-test]
  - [mount, -t, iso9660, -o, 'ro,nosuid,nodev', /dev/disk/by-label/ANVILTEST, /opt/anvil-test]
  - [python3, -I, /opt/anvil-test/guest.py]
power_state:
  delay: now
  mode: poweroff
  timeout: 30
  condition: true
"""
    root.mkdir(mode=0o700)
    (root / "user-data").write_text(user_data, encoding="utf-8")
    (root / "meta-data").write_text(f"instance-id: acq-{instance}\nlocal-hostname: connect-test\n", encoding="ascii")
    (root / "network-config").write_text("version: 2\nethernets: {}\n", encoding="ascii")


def _digest(path: Path, *, maximum: int = _MAX_PAYLOAD) -> tuple[str, int]:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size < 0 or info.st_size > maximum:
            raise OSError
        value = hashlib.sha256()
        total = 0
        while block := os.read(descriptor, 64 * 1024):
            total += len(block)
            if total > maximum:
                raise OSError
            value.update(block)
        return value.hexdigest(), total
    except OSError as exc:
        raise _error("staging-failed", "VM qualification input is unsafe") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _regular(path: Path, *, executable: bool = False, owner: int | None = None) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise _error("runner-unavailable", "VM qualification tool is unavailable") from exc
    if (path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_size < 1
            or info.st_mode & 0o022 or (owner is not None and info.st_uid != owner)
            or (executable and not info.st_mode & 0o111)):
        raise _error("runner-unavailable", "VM qualification tool is unsafe")
    return info


def _system_metadata() -> dict[str, dict[str, str]]:
    values: dict[str, dict[str, str]] = {}
    for name, path, executable in (("qemu", _QEMU, True), ("qemu_img", _QEMU_IMG, True),
                                   ("xorriso", _XORRISO, True), ("firmware", _FIRMWARE, False)):
        _regular(path, executable=executable, owner=0)
        values[name] = {"sha256": _digest(path, maximum=64 * 1024 * 1024)[0]}
    return values


def _copy_file(source: Path, target: Path, files: dict[str, str], root: Path, *, executable: bool = False) -> None:
    _regular(source, executable=executable)
    relative = target.relative_to(root)
    if target.exists() or target.is_symlink() or not relative.parts or ".." in relative.parts:
        raise _error("staging-failed", "VM payload path is unsafe")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(source_fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_mode & 0o022
                or before.st_size > _MAX_PAYLOAD or (executable and not before.st_mode & 0o111)):
            raise _error("staging-failed", "VM payload source is unsafe")
        output_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(output_fd, "wb") as output:
            copied = 0
            while block := os.read(source_fd, 64 * 1024):
                copied += len(block)
                if copied > _MAX_PAYLOAD:
                    raise _error("staging-failed", "VM payload source exceeds its bound")
                output.write(block)
            os.fchmod(output.fileno(), 0o755 if executable else 0o644)
        after = os.fstat(source_fd)
        if copied != before.st_size or any(getattr(before, key) != getattr(after, key)
                                           for key in ("st_size", "st_mtime_ns", "st_ctime_ns")):
            raise _error("staging-failed", "VM payload source changed during copy")
    finally:
        os.close(source_fd)
    files[str(relative)] = _digest(target)[0]



def _copy_python(source: Path, payload: Path, files: dict[str, str]) -> None:
    """Copy only the immutable archive's public Python files."""
    root = source / "anvil_serving"
    paths = sorted(root.rglob("*.py")) if root.is_dir() else []
    paths.append(root / "connect/components.lock.json")
    if not paths or len(paths) > _MAX_FILES:
        raise _error("source-invalid", "qualification staged source is incomplete")
    for path in paths:
        relative = path.relative_to(source)
        if path.is_symlink() or not path.is_file() or "__pycache__" in path.parts:
            raise _error("source-invalid", "qualification staged source is unsafe")
        _copy_file(path, payload / "python" / relative, files, payload)


def _write_json(path: Path, value: Any, *, mode: int = 0o644) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.chmod(path, mode)


def _run_build(config: QualificationConfig, argv: list[str], *, cwd: Path, cache: Path, cpus: tuple[int, ...], deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _error("runner-timeout", "VM qualification exceeded its deadline")
    environment = {
        "CGO_ENABLED": "0", "GOOS": "linux", "GOARCH": "amd64", "GOTOOLCHAIN": "local",
        "GOPROXY": "off", "GOSUMDB": "off", "GOMODCACHE": str(config.go_module_cache),
        "GOCACHE": str(cache), "GOWORK": "off", "GOENV": "off", "GOFLAGS": "-p=2", "GOMAXPROCS": "2",
        "PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
    }
    try:
        result = execute(argv, home=cwd, cwd=cwd, environment=environment, timeout=min(remaining, 120), cpus=cpus,
                         maximum_output=64 * 1024, maximum_record=64 * 1024)
        if result.returncode != 0:
            raise OSError
    except (OSError, QualificationError) as exc:
        raise _error("staging-failed", "VM payload build failed", execution_started=getattr(exc, "execution_started", False), stage="staging") from exc



def _remaining(deadline: float, limit: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _error("runner-timeout", "VM qualification exceeded its deadline")
    return min(remaining, limit)


def _stage_source(source: Path, run_dir: Path, metadata: dict[str, Any], *,
                  deadline: float | None = None, cpus: tuple[int, ...] | None = None) -> Path:
    """Export a bounded immutable archive; reject links and non-regular objects."""
    deadline = deadline if deadline is not None else time.monotonic() + 20
    cpus = cpus or tuple(sorted(os.sched_getaffinity(0))[-2:])
    target, archive = run_dir / "source", run_dir / "source.tar"
    run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        result = execute(["/usr/bin/git", "-C", str(source), "archive", "--format=tar",
                          "--output=" + str(archive), metadata["revision"], "connect", "anvil_serving"],
                         home=run_dir, timeout=_remaining(deadline, 20), cpus=cpus,
                         maximum_output=64 * 1024, file_limit=_MAX_PAYLOAD)
        if result.returncode != 0 or not 0 < archive.stat().st_size <= _MAX_PAYLOAD:
            raise OSError
        target.mkdir(mode=0o700)
        seen: set[str] = set()
        total = 0
        with tarfile.open(archive, mode="r|", bufsize=64 * 1024) as bundle:
            for member in bundle:
                _remaining(deadline, 20)
                name = Path(member.name)
                if (name.is_absolute() or ".." in name.parts or not name.parts
                        or name.parts[0] not in {"connect", "anvil_serving"}
                        or str(name) in seen or len(seen) >= _MAX_FILES
                        or not (member.isfile() or member.isdir())):
                    raise OSError
                seen.add(str(name))
                destination = target / name
                if member.isdir():
                    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
                    continue
                total += member.size
                if member.size < 0 or total > _MAX_PAYLOAD:
                    raise OSError
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                incoming = bundle.extractfile(member)
                if incoming is None:
                    raise OSError
                with incoming, destination.open("xb") as output:
                    while block := incoming.read(64 * 1024):
                        _remaining(deadline, 20)
                        output.write(block)
                destination.chmod(0o600)
        if not (target / "connect/test/vm/guest.py").is_file() or not (target / "connect/go.mod").is_file():
            raise OSError
        return target
    except (OSError, ValueError, tarfile.TarError) as exc:
        raise _error("source-invalid", "qualification tracked source is invalid") from exc
    finally:
        archive.unlink(missing_ok=True)


def _render_declaration(source_root: Path, run_dir: Path, *, cpus: tuple[int, ...], deadline: float) -> tuple[dict[str, Any], dict[str, str]]:
    """Render tracked data with bounded product code; never execute guest.py here."""
    declaration = source_root / "connect/test/vm/deployment.json"
    _digest(declaration, maximum=_MAX_RESULT)
    script = (
        "import json,sys;sys.path.insert(0,sys.argv[1]);"
        "from anvil_serving.connect.config import validate_manifest;"
        "from anvil_serving.connect.render import render_for_inspection;"
        "m=validate_manifest(json.load(open(sys.argv[2])));"
        "print(json.dumps({'manifest':m,'files':render_for_inspection(m)['files']}))"
    )
    rendered_process = execute([sys.executable, "-I", "-c", script, str(source_root), str(declaration)],
                               home=run_dir, timeout=_remaining(deadline, 30), cpus=cpus,
                               maximum_output=1024 * 1024)
    try:
        value = json.loads(rendered_process.output)
        if rendered_process.returncode != 0 or set(value) != {"manifest", "files"}:
            raise ValueError
        manifest, rendered = value["manifest"], value["files"]
        if not isinstance(manifest, dict) or not isinstance(rendered, dict):
            raise ValueError
    except (UnicodeError, TypeError, ValueError, KeyError) as exc:
        raise _error("source-invalid", "qualification guest declaration is invalid") from exc
    return manifest, rendered


def _payload(config: QualificationConfig, run_dir: Path, source: dict[str, Any], *, source_root: Path, cpus: tuple[int, ...], deadline: float) -> tuple[Path, dict[str, str], dict[str, Any]]:
    payload = run_dir / "payload"
    payload.mkdir(mode=0o700)
    files: dict[str, str] = {}
    guest_path = source_root / "connect/test/vm/guest.py"
    _copy_file(guest_path, payload / "guest.py", files, payload)
    manifest, rendered = _render_declaration(source_root, run_dir, cpus=cpus, deadline=deadline)
    _write_json(payload / "deployment.json", manifest)
    files["deployment.json"] = _digest(payload / "deployment.json")[0]
    for name, content in sorted(rendered.items()):
        if not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts or not isinstance(content, str):
            raise _error("source-invalid", "qualification rendered path is invalid")
        target = payload / "rendered" / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        os.chmod(target, 0o644)
        files[str(target.relative_to(payload))] = _digest(target)[0]
    _copy_python(source_root, payload, files)
    for name in ("caddy", "authelia", "wstunnel"):
        _copy_file(config.tools[name], payload / "bin" / name, files, payload, executable=True)
        if files["bin/" + name] != _locks(source_root)["binaries"][name]:
            raise _error("tool-invalid", "VM copied component does not match its lock")
    build = {"schema": _BUILD_SCHEMA, "platform": "linux/amd64", "go": _digest(config.tools["go"], maximum=64 * 1024 * 1024)[0],
             "flags": ["CGO_ENABLED=0", "GOOS=linux", "GOARCH=amd64", "GOPROXY=off", "GOSUMDB=off"]}
    native = payload / "bin/anvil-connect-ctl"
    native.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    go_cache = run_dir / "go-cache"
    go_cache.mkdir(mode=0o700)
    _run_build(config, [str(config.tools["go"]), "build", "-trimpath", "-buildvcs=false", "-o", str(native), "./cmd/anvil-connect"], cwd=source_root / "connect", cache=go_cache, cpus=cpus, deadline=deadline)
    native.chmod(0o755)
    _regular(native, executable=True)
    files["bin/anvil-connect-ctl"] = _digest(native)[0]
    _run_build(config, [str(config.tools["go"]), "test", "-c", "-trimpath", "-o", str(payload / "bin/cli-tests"), "./cmd/anvil-connect"], cwd=source_root / "connect", cache=go_cache, cpus=cpus, deadline=deadline)
    (payload / "bin/cli-tests").chmod(0o755)
    files["bin/cli-tests"] = _digest(payload / "bin/cli-tests")[0]
    if len(files) > _MAX_FILES or sum((payload / name).stat().st_size for name in files) > _MAX_PAYLOAD:
        raise _error("staging-failed", "VM payload exceeds its bound")
    _write_json(payload / "payload.json", {"schema": _PAYLOAD_SCHEMA, "source": source, "build": {"schema": _BUILD_SCHEMA, "platform": "linux/amd64"}, "files": dict(sorted(files.items()))})
    return payload, files, build


def _iso(argv_root: Path, output: Path, label: str, *, cpus: tuple[int, ...], deadline: float) -> int:
    descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    os.fchmod(descriptor, 0o600)
    try:
        result = execute([str(_XORRISO), "-as", "mkisofs", "-quiet", "-r", "-iso-level", "3", "-volid", label,
                          "-output", f"/dev/fd/{descriptor}", str(argv_root)],
                         home=argv_root, timeout=_remaining(deadline, 60), cpus=cpus,
                         pass_fds=(descriptor,), maximum_output=64 * 1024, file_limit=_MAX_PAYLOAD * 2)
        if result.returncode != 0:
            raise OSError
        os.fchmod(descriptor, 0o400)
        _verify_fd(descriptor, owner=os.geteuid(), mode=0o400, maximum=_MAX_PAYLOAD * 2, deadline=deadline)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_pinned(path: Path, *, writable: bool = False) -> int:
    flags = (os.O_RDWR if writable else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    try:
        descriptor = os.open(path, flags)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError
        return descriptor
    except OSError as exc:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        raise _error("staging-failed", "VM pinned input is unsafe") from exc


def _verify_fd(descriptor: int, *, owner: int, mode: int, maximum: int,
               deadline: float, expected: str | None = None) -> tuple[str, int]:
    """Hash the retained object, checking metadata before and after bounded reads."""
    info = os.fstat(descriptor)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != mode or not 0 < info.st_size <= maximum):
        raise _error("staging-failed", "VM pinned input metadata is unsafe")
    digest = hashlib.sha256()
    offset = 0
    while offset < info.st_size:
        _remaining(deadline, 30)
        block = os.pread(descriptor, min(64 * 1024, info.st_size - offset), offset)
        if not block:
            raise _error("staging-failed", "VM pinned input changed during verification")
        offset += len(block)
        digest.update(block)
    final = os.fstat(descriptor)
    attributes = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
    value = digest.hexdigest()
    if any(getattr(info, key) != getattr(final, key) for key in attributes) or (expected is not None and value != expected):
        raise _error("staging-failed", "VM pinned input does not match its verified bytes")
    return value, offset


def _image_fd_info(descriptor: int, *, home: Path, cpus: tuple[int, ...], deadline: float,
                   base_fd: int | None = None) -> int:
    inherited = (descriptor,) if base_fd is None else (descriptor, base_fd)
    result = execute([str(_QEMU_IMG), "info", "--output=json", f"/proc/self/fd/{descriptor}"],
                     home=home, timeout=_remaining(deadline, 30), cpus=cpus,
                     pass_fds=inherited, maximum_output=64 * 1024)
    try:
        value = json.loads(result.output)
        size = value["virtual-size"]
        backing = None if base_fd is None else f"/proc/self/fd/{base_fd}"

        def unsafe(item: Any) -> bool:
            if isinstance(item, dict):
                for key, child in item.items():
                    if key in {"data-file", "data-file-raw", "corrupt"} and child:
                        return True
                    if key in {"backing-filename", "full-backing-filename"} and child not in {None, backing}:
                        return True
                    if unsafe(child):
                        return True
            elif isinstance(item, list):
                return any(unsafe(child) for child in item)
            return False

        if (result.returncode != 0 or value.get("format") != "qcow2" or type(size) is not int
                or not 0 < size <= _MAX_VIRTUAL_SIZE or unsafe(value)
                or (backing is not None and value.get("backing-filename") != backing)):
            raise ValueError
        return size
    except (UnicodeError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise _error("staging-failed", "VM pinned image format is unsafe") from exc


def _overlay(base_fd: int, output: Path, *, cpus: tuple[int, ...], deadline: float) -> int:
    """Create, inspect, and retain the same object through QEMU execution."""
    descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    os.fchmod(descriptor, 0o600)
    try:
        result = execute([str(_QEMU_IMG), "create", "-f", "qcow2", "-F", "qcow2", "-b",
                          f"/proc/self/fd/{base_fd}", f"/proc/self/fd/{descriptor}"],
                         home=output.parent, timeout=_remaining(deadline, 30), cpus=cpus,
                         pass_fds=(base_fd, descriptor), maximum_output=64 * 1024)
        if result.returncode != 0:
            raise _error("staging-failed", "VM overlay staging failed")
        _verify_fd(descriptor, owner=os.geteuid(), mode=0o600, maximum=9 * _GiB, deadline=deadline)
        _image_fd_info(descriptor, home=output.parent, cpus=cpus, deadline=deadline, base_fd=base_fd)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _counts(cases: list[dict[str, str]]) -> dict[str, int]:
    return {"passed": sum(item["status"] == "passed" for item in cases),
            "failed": sum(item["status"] == "failed" for item in cases),
            "skipped": sum(item["status"] == "skipped" for item in cases),
            "not_run": sum(item["status"] == "not-run" for item in cases),
            "unavailable": sum(item["status"] == "unavailable" for item in cases)}


def _junit(path: Path, cases: list[dict[str, str]], *, infrastructure_error: str | None = None) -> None:
    root = ET.Element("testsuite", name="connect-isolation-vm", tests=str(len(cases) + bool(infrastructure_error)), failures=str(sum(case["status"] == "failed" for case in cases)),
                      errors=str(sum(case["status"] == "unavailable" for case in cases) + bool(infrastructure_error)),
                      skipped=str(sum(case["status"] in {"not-run", "skipped"} for case in cases)))
    for case in cases:
        node = ET.SubElement(root, "testcase", name=case["name"], classname="connect.isolation")
        if case["status"] != "passed":
            tag = {"failed": "failure", "unavailable": "error", "not-run": "skipped", "skipped": "skipped"}[case["status"]]
            ET.SubElement(node, tag, type=case["status"], message="isolated guest result: " + case["status"])
    if infrastructure_error:
        node = ET.SubElement(root, "testcase", name="runner", classname="connect.isolation.infrastructure")
        ET.SubElement(node, "error", type=infrastructure_error, message="qualification runner failed")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    os.chmod(path, 0o600)


def _artifact(path: Path, value: Any) -> None:
    _write_json(path, value, mode=0o600)


def _remove_created(path: Path) -> None:
    """Remove one runner-created artifact without following a substituted link."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise _error("staging-failed", "VM private cleanup failed", execution_started=True, stage="cleanup") from exc
    try:
        if stat.S_ISLNK(info.st_mode):
            raise OSError
        if stat.S_ISDIR(info.st_mode):
            shutil.rmtree(path)
        elif stat.S_ISREG(info.st_mode):
            path.unlink()
        else:
            raise OSError
        if path.exists() or path.is_symlink():
            raise OSError
    except OSError as exc:
        raise _error("staging-failed", "VM private cleanup failed", execution_started=True, stage="cleanup") from exc


def _closed_artifacts(run_dir: Path, *, source: dict[str, Any], state: str, code: str | None,
                      cases: list[dict[str, str]], details: dict[str, Any]) -> None:
    counts = _counts(cases)
    evidence = {"schema": _SCHEMA, "state": state, "error_code": code, "source": source,
                "cases": cases, "counts": counts, **details}
    _artifact(run_dir / "evidence.json", evidence)
    _artifact(run_dir / "result.json", {"schema": _SCHEMA, "state": state, "ok": state == "passed", "counts": counts, "error_code": code})
    _junit(run_dir / "junit.xml", cases, infrastructure_error=code if state != "passed" else None)
    sums = run_dir / "SHA256SUMS"
    sums.write_text("".join(f"{_digest(run_dir / name, maximum=_MAX_RESULT)[0]}  {name}\n" for name in ("evidence.json", "result.json", "junit.xml")), encoding="ascii")
    os.chmod(sums, 0o600)
    if {item.name for item in run_dir.iterdir()} != {"evidence.json", "result.json", "junit.xml", "SHA256SUMS"}:
        raise _error("staging-failed", "VM qualification artifact cleanup failed", execution_started=True, stage="cleanup")


def _cleanup_owned(paths: list[Path], descriptors: list[int]) -> None:
    errors = False
    for descriptor in descriptors:
        try:
            os.close(descriptor)
        except OSError:
            errors = True
    descriptors.clear()
    for path in paths:
        try:
            _remove_created(path)
        except QualificationError:
            errors = True
    if errors:
        raise _error("staging-failed", "VM private cleanup failed", execution_started=True, stage="cleanup")


def _cleanup_residuals(run_dir: Path) -> bool:
    """Clear the owned private directory, unlinking links without following targets."""
    residual = False
    for path in run_dir.iterdir():
        residual = True
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            shutil.rmtree(path)
        else:
            path.unlink()
    return residual


def qualify(config_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Run the fixed, offline, no-network isolation guest qualification."""
    path = Path(config_path) if config_path is not None else Path.home() / ".config/anvil-connect/qualification.toml"
    config = _read_config(path)
    _private_directory(config.artifact_root, create=True)
    _safe_cache(config.go_module_cache)
    deadline = time.monotonic() + min(_TIMEOUT, config.timeout_seconds)
    source = _source_metadata(config.source_root)
    locks = _locks(config.source_root)
    if source["dirty"]:
        raise _error("source-version", "VM qualification requires a clean tracked source")
    for name in ("caddy", "authelia", "wstunnel"):
        if _digest(config.tools[name], maximum=64 * 1024 * 1024)[0] != locks["binaries"][name]:
            raise _error("tool-invalid", "VM component digest does not match its lock")
    cpus = _capacity(config.artifact_root)
    cache = _private_cache(config)
    pins = _pins(config)
    cached = _valid_cached_image(cache, pins, deadline=deadline)
    receipt = _read_receipt(config.artifact_root)
    if cached is None or receipt is None:
        raise _error("source-invalid", "VM qualification requires a prepared verified image")
    digest, image_bytes, virtual_size = cached
    if (receipt.get("image") != _CACHE_NAME or receipt.get("image_sha256") != digest
            or receipt.get("image_bytes") != image_bytes or receipt.get("virtual_size") != virtual_size
            or receipt.get("keyring_sha256") != pins["keyring_sha256"]):
        raise _error("source-invalid", "VM image receipt does not match the prepared image")
    tools = _system_metadata()
    run_dir = config.artifact_root / ("isolation-" + uuid.uuid4().hex)
    run_dir.mkdir(mode=0o700)
    payload, source_stage, seed_tree = run_dir / "payload", run_dir / "source", run_dir / "seed"
    seed_iso, payload_iso, overlay, go_cache = run_dir / "cidata.iso", run_dir / "payload.iso", run_dir / "overlay.qcow2", run_dir / "go-cache"
    owned = [overlay, payload, seed_tree, seed_iso, payload_iso, go_cache, source_stage]
    descriptors: list[int] = []
    execution_started = False
    interrupted = None
    cases = None
    details: dict[str, Any] = {"image": {"sha256": digest, "bytes": image_bytes, "virtual_size": virtual_size}, "tools": tools}
    try:
        source_stage = _stage_source(config.source_root, run_dir, source, deadline=deadline, cpus=cpus)
        if _locks(source_stage) != locks:
            raise _error("source-invalid", "qualification tracked source changed during staging")
        payload, files, build = _payload(config, run_dir, source, source_root=source_stage, cpus=cpus, deadline=deadline)
        details["payload"] = {"files": files, "build": build}
        _seed(seed_tree, uuid.uuid4().hex)
        base_fd = _open_pinned(cache / _CACHE_NAME)
        descriptors.append(base_fd)
        if _verify_fd(base_fd, owner=os.geteuid(), mode=0o400, maximum=pins["max_image_bytes"],
                      deadline=deadline, expected=pins["image_sha256"]) != (digest, image_bytes):
            raise _error("source-invalid", "VM pinned image differs from its receipt")
        if _image_fd_info(base_fd, home=run_dir, cpus=cpus, deadline=deadline) != virtual_size:
            raise _error("source-invalid", "VM pinned image geometry differs from its receipt")
        descriptors.append(_overlay(base_fd, overlay, cpus=cpus, deadline=deadline))
        descriptors.append(_iso(seed_tree, seed_iso, "CIDATA", cpus=cpus, deadline=deadline))
        descriptors.append(_iso(payload, payload_iso, "ANVILTEST", cpus=cpus, deadline=deadline))
        descriptors.append(_open_pinned(_FIRMWARE))
        input_hashes = {}
        for name, fd, owner, mode, maximum, expected in (
                ("seed_iso", descriptors[2], os.geteuid(), 0o400, _MAX_PAYLOAD * 2, None),
                ("payload_iso", descriptors[3], os.geteuid(), 0o400, _MAX_PAYLOAD * 2, None),
                ("firmware", descriptors[4], 0, 0o644, 64 * 1024 * 1024, tools["firmware"]["sha256"])):
            value, size = _verify_fd(fd, owner=owner, mode=mode, maximum=maximum, deadline=deadline, expected=expected)
            input_hashes[name] = {"sha256": value, "bytes": size}
        details["boot_inputs"] = input_hashes
        cpus = _capacity(config.artifact_root)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _error("runner-timeout", "VM qualification exceeded its deadline", execution_started=True, stage="execution")
        execution_started = True
        process = execute(_qemu_argv(*descriptors), home=run_dir, cwd=run_dir, timeout=remaining, cpus=cpus,
                          pass_fds=tuple(descriptors), maximum_output=1024 * 1024, retained_prefix=_MARKER,
                          maximum_record=_MAX_RESULT, watched_file=Path(f"/proc/self/fd/{descriptors[1]}"))
        execution_started = True
        details["measurements"] = {"elapsed_ms": process.elapsed_ms, "peak_rss_bytes": process.peak_rss_bytes,
                                   "output_bytes": process.output_bytes, "returncode": process.returncode}
        cases = _cases(process.output)
        if process.returncode != 0:
            details["failure_reason"] = "child-exit"
            raise _error("runner-failed", "VM guest did not shut down cleanly", execution_started=True, stage="execution")
        _cleanup_owned(owned, descriptors)
        if _cleanup_residuals(run_dir):
            raise _error("staging-failed", "VM private cleanup found unexpected files", stage="cleanup")
        _closed_artifacts(run_dir, source=source, state="passed" if all(item["status"] == "passed" for item in cases) else "failed", code=None,
                          cases=cases, details={**details, "cleanup": {"complete": True}})
        return {"schema": _SCHEMA, "ok": all(item["status"] == "passed" for item in cases), "artifact_dir": str(run_dir), "counts": _counts(cases)}
    except KeyboardInterrupt as exc:
        interrupted = exc
        failure = _error("runner-interrupted", "VM qualification interrupted", execution_started=execution_started, stage="execution")
    except QualificationError as exc:
        failure = exc
    except (OSError, ValueError, TypeError, shutil.Error):
        failure = _error("staging-failed", "VM qualification preparation failed", execution_started=execution_started, stage="staging")
    reason = getattr(failure, "reason", None)
    if isinstance(reason, str) and reason in {"timeout", "rss-limit", "file-limit", "output-limit", "line-limit", "record-duplicate",
                  "record-oversized", "record-incomplete", "record-invalid", "child-exit", "process-error", "cleanup-failure"}:
        details["failure_reason"] = reason
    measurements = getattr(failure, "measurements", None)
    if isinstance(measurements, dict):
        safe = {key: value for key, value in measurements.items()
                if key in {"elapsed_ms", "peak_rss_bytes", "output_bytes", "returncode"}
                and ((value is None and key == "returncode") or (type(value) is int
                     and (-(2 ** 31) if key == "returncode" else 0) <= value < 2 ** 63))}
        if safe:
            details["measurements"] = safe
    try:
        _cleanup_owned(owned, descriptors)
        _cleanup_residuals(run_dir)
        if cases is None:
            cases = [{"name": name, "status": "unavailable" if execution_started else "not-run"}
                     for name in _CASES]
        _closed_artifacts(run_dir, source=source, state="failed", code=failure.code, cases=cases, details={**details, "cleanup": {"complete": reason != "cleanup-failure",
                          "filesystem_complete": True, "process_complete": reason != "cleanup-failure"}})
    except QualificationError as cleanup:
        raise cleanup from None
    failure.execution_started = execution_started
    failure.case_counts = _counts(cases)
    if interrupted is not None:
        interrupted.execution_started = execution_started
        interrupted.case_counts = _counts(cases)
        raise interrupted
    raise failure

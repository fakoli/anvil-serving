"""Offline, private-artifact qualification for the standalone Connect edge.

The runner intentionally has no CLI side effects: callers receive a small safe
result envelope, while machine-readable evidence is written only below a
private artifact root supplied by the operator.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import selectors
import sys
import stat
import tempfile
import subprocess
import time
import tomllib
from typing import Any, Iterable
import uuid
import xml.etree.ElementTree as ET

_SCHEMA = "anvil-connect.qualification/v1"
_CONFIG_SCHEMA = "anvil-connect.qualification-config/v1"
_MAX_TIMEOUT = 900
_DEFAULT_TIMEOUT = 600
_MAX_OUTPUT = 64 * 1024
_TOOL_NAMES = frozenset({"go", "node", "chromium", "certutil", "caddy", "authelia", "wstunnel"})
_TESTS = (
    "pinned Caddy and Authelia browser edge retains Connect and native controls",
    "managed gateway and connector lifecycle retain the real browser edge",
)
_SAFE_CODES = frozenset({
    "config-invalid", "config-missing", "source-invalid", "source-version",
    "artifact-root-invalid", "tool-invalid", "lock-invalid", "staging-failed",
    "runner-unavailable", "runner-timeout", "runner-interrupted", "runner-failed",
})
_VERSION_TOKEN = re.compile(r"(?<![A-Za-z0-9])v?[0-9]+(?:\.[0-9]+){1,3}(?:[-+._A-Za-z0-9]*)?")


class QualificationError(RuntimeError):
    """Safe preflight failure.  ``safe_message`` never includes child output."""

    def __init__(self, code: str, safe_message: str, *, execution_started: bool = False, stage: str = "preflight", case_counts: dict[str, int] | None = None) -> None:
        if code not in _SAFE_CODES:
            code = "config-invalid"
        if stage not in {"preflight", "staging", "execution", "cleanup"}:
            stage = "preflight"
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.execution_started = execution_started
        self.stage = stage
        self.case_counts = case_counts


@dataclass(frozen=True)
class QualificationConfig:
    source_root: Path
    artifact_root: Path
    playwright_root: Path
    go_module_cache: Path
    tools: dict[str, Path]
    timeout_seconds: int = _DEFAULT_TIMEOUT


def _error(code: str, message: str, *, execution_started: bool = False, stage: str = "preflight", case_counts: dict[str, int] | None = None) -> QualificationError:
    return QualificationError(code, message, execution_started=execution_started, stage=stage, case_counts=case_counts)


def _execution_error(error: QualificationError, *, execution_started: bool, stage: str, case_counts: dict[str, int] | None) -> QualificationError:
    """Preserve a safe code while attaching the point at which it occurred."""
    return _error(error.code, error.safe_message, execution_started=execution_started, stage=stage, case_counts=case_counts)


def _absolute_path(value: Any, name: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise _error("config-invalid", f"{name} must be an absolute path")
    path = Path(value)
    if not path.is_absolute():
        raise _error("config-invalid", f"{name} must be an absolute path")
    return path


def _read_config(path: Path) -> QualificationConfig:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024:
                raise _error("config-invalid", "qualification configuration must be a bounded regular file")
            content = handle.read(64 * 1024 + 1)
            if len(content) > 64 * 1024:
                raise _error("config-invalid", "qualification configuration is too large")
        raw = tomllib.loads(content.decode("utf-8"))
    except FileNotFoundError as exc:
        raise _error("config-missing", "qualification configuration is missing") from exc
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise _error("config-invalid", "qualification configuration is invalid") from exc
    expected = {"schema", "source_root", "artifact_root", "playwright_root", "go_module_cache", "tools", "timeout_seconds"}
    if not isinstance(raw, dict) or set(raw) != expected or raw.get("schema") != _CONFIG_SCHEMA:
        raise _error("config-invalid", "qualification configuration has an unknown or missing field")
    tools = raw["tools"]
    if not isinstance(tools, dict) or set(tools) != _TOOL_NAMES:
        raise _error("config-invalid", "qualification tools are not closed")
    timeout = raw["timeout_seconds"]
    if type(timeout) is not int or not 1 <= timeout <= _MAX_TIMEOUT:
        raise _error("config-invalid", "qualification timeout is invalid")
    return QualificationConfig(
        source_root=_absolute_path(raw["source_root"], "source_root"),
        artifact_root=_absolute_path(raw["artifact_root"], "artifact_root"),
        playwright_root=_absolute_path(raw["playwright_root"], "playwright_root"),
        go_module_cache=_absolute_path(raw["go_module_cache"], "go_module_cache"),
        tools={name: _absolute_path(value, "tools." + name) for name, value in tools.items()},
        timeout_seconds=timeout,
    )


def _regular(path: Path, *, executable: bool = False) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise _error("tool-invalid", "a qualification tool is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_size < 1 or path.is_symlink() or (executable and not info.st_mode & 0o111):
        raise _error("tool-invalid", "a qualification tool is unsafe")
    return info


def _private_directory(path: Path, *, create: bool = False) -> None:
    if create:
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise _error("artifact-root-invalid", "qualification artifact root cannot be created") from exc
    try:
        info = path.lstat()
    except OSError as exc:
        raise _error("artifact-root-invalid", "qualification artifact root is unavailable") from exc
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise _error("artifact-root-invalid", "qualification artifact root must be owned private directory")


def _sha256(path: Path) -> str:
    _regular(path)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for part in iter(lambda: handle.read(64 * 1024), b""):
                digest.update(part)
    except OSError as exc:
        raise _error("tool-invalid", "qualification file cannot be read") from exc
    return digest.hexdigest()


def _git(source_root: Path, args: list[str]) -> bytes:
    try:
        result = subprocess.run(
            ["/usr/bin/git", "-C", str(source_root), *args], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, timeout=5,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _error("source-invalid", "qualification source metadata is unavailable") from exc
    if result.returncode != 0 or len(result.stdout) > 256:
        raise _error("source-invalid", "qualification source metadata is invalid")
    return result.stdout.strip()


def _git_dirty(source_root: Path) -> bool:
    for args in (["diff", "--quiet"], ["diff", "--cached", "--quiet"]):
        try:
            result = subprocess.run(["/usr/bin/git", "-C", str(source_root), *args], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=5,
                env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
        except (OSError, subprocess.SubprocessError) as exc:
            raise _error("source-invalid", "qualification source metadata is unavailable") from exc
        if result.returncode == 1:
            return True
        if result.returncode != 0:
            raise _error("source-invalid", "qualification source metadata is invalid")
    return False


def _source_metadata(source_root: Path) -> dict[str, Any]:
    try:
        info = source_root.lstat()
    except OSError as exc:
        raise _error("source-invalid", "qualification source is unavailable") from exc
    if source_root.is_symlink() or not stat.S_ISDIR(info.st_mode):
        raise _error("source-invalid", "qualification source is unsafe")
    required = (source_root / "connect/lab/edge-tools.json", source_root / "connect/transport.lock.json", source_root / "connect/test/browser_edge.spec.mjs")
    if not all(path.is_file() and not path.is_symlink() for path in required):
        raise _error("source-invalid", "qualification source is incomplete")
    revision = _git(source_root, ["rev-parse", "HEAD"]).decode("ascii", "strict")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise _error("source-invalid", "qualification source revision is invalid")
    return {"revision": revision, "dirty": _git_dirty(source_root)}


def _locks(source_root: Path) -> dict[str, Any]:
    paths = {"edge_tools": source_root / "connect/lab/edge-tools.json", "transport": source_root / "connect/transport.lock.json"}
    try:
        edge = json.loads(paths["edge_tools"].read_text(encoding="utf-8"))
        transport = json.loads(paths["transport"].read_text(encoding="utf-8"))
        components = {item["name"]: item["binary_sha256"] for item in edge["components"]}
        tunnel = transport["artifacts"]["linux/amd64"]["binary_sha256"]
        if edge.get("schema") != "anvil-connect.edge-tools/v1" or transport.get("schema") != "anvil-connect.transport-lock/v1" or set(components) != {"caddy", "authelia"}:
            raise ValueError
        expected = {**components, "wstunnel": tunnel}
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in expected.values()):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise _error("lock-invalid", "qualification lock files are invalid") from exc
    return {"files": {name: _sha256(path) for name, path in paths.items()}, "binaries": expected}


def _tool_metadata(tools: dict[str, Path]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for name in sorted(tools):
        path = tools[name]
        _regular(path, executable=True)
        if name == "certutil":
            # NSS certutil has no stable version contract on every supported
            # distribution; its pinned path and hash remain evidence.
            result[name] = {"sha256": _sha256(path), "version": "not-reported"}
            continue
        command = [str(path), "version"] if name == "go" else [str(path), "--version"]
        try:
            version = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, check=False, timeout=5,
                env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}).stdout[:512].decode("utf-8", "replace").splitlines()
        except (OSError, subprocess.SubprocessError) as exc:
            raise _error("tool-invalid", "a qualification tool cannot be inspected") from exc
        version_text = version[0] if version else ""
        if name == "go":
            version_text = version_text.removeprefix("go version go")
        match = _VERSION_TOKEN.search(version_text)
        # Keep a parsed version token only. Raw tool output is never evidence.
        result[name] = {"sha256": _sha256(path), "version": match.group(0) if match else "unavailable"}
    return result


def _make_private_tree(root: Path) -> None:
    """Restrict a copied tree without retaining or following staged symlinks."""
    for directory, _, files in os.walk(root, topdown=False, followlinks=False):
        path = Path(directory)
        if path.is_symlink():
            raise OSError("staged symlink")
        os.chmod(path, 0o700)
        for name in files:
            child = path / name
            if child.is_symlink():
                raise OSError("staged symlink")
            mode = child.stat(follow_symlinks=False).st_mode
            os.chmod(child, 0o700 if mode & 0o111 else 0o600)


def _tracked_connect_files(source_root: Path) -> list[Path]:
    try:
        result = subprocess.run(["/usr/bin/git", "-C", str(source_root), "ls-files", "-z", "--", "connect"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, timeout=10,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError) as exc:
        raise _error("source-invalid", "qualification tracked source is unavailable") from exc
    if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
        raise _error("source-invalid", "qualification tracked source is invalid")
    names = [Path(item.decode("utf-8", "strict")) for item in result.stdout.split(b"\0") if item]
    if not names or any(name.is_absolute() or ".." in name.parts or name.parts[0] != "connect" for name in names):
        raise _error("source-invalid", "qualification tracked source is invalid")
    return names


def _copy_node_modules(source: Path, destination: Path) -> None:
    root = source.resolve(strict=True)
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(directory) / name
            if path.is_symlink() and not path.resolve(strict=True).is_relative_to(root):
                raise OSError("dependency link escapes root")
    # Dereference only verified in-root links, so the private stage has no link
    # back to public source/dependencies. Playwright is invoked via cli.js.
    shutil.copytree(root, destination, symlinks=False)


def _copy_stage(config: QualificationConfig, run_dir: Path, tracked: list[Path] | None = None) -> Path:
    stage = run_dir / "source"
    try:
        stage.mkdir(mode=0o700)
        for relative in tracked if tracked is not None else _tracked_connect_files(config.source_root):
            source = config.source_root / relative
            target = stage / relative
            if source.is_symlink() or not source.is_file():
                raise OSError("unsafe tracked file")
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copy2(source, target, follow_symlinks=False)
        modules = config.playwright_root / "node_modules"
        if modules.is_symlink() or not modules.is_dir():
            raise OSError("node modules unavailable")
        _copy_node_modules(modules, stage / "connect/node_modules")
        supervisor = Path(__file__).with_name("_qualification_supervisor.py")
        if supervisor.is_symlink() or not supervisor.is_file():
            raise OSError("supervisor unavailable")
        shutil.copy2(supervisor, run_dir / "supervisor.py", follow_symlinks=False)
        _make_private_tree(stage)
        os.chmod(run_dir / "supervisor.py", 0o700)
    except (OSError, UnicodeError, shutil.Error) as exc:
        raise _error("staging-failed", "qualification private staging failed") from exc
    for path in (stage, stage / "connect", stage / "connect/node_modules"):
        _private_directory(path)
    return stage


def _safe_cache(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise _error("config-invalid", "qualification Go cache is unavailable") from exc
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o002:
        raise _error("config-invalid", "qualification Go cache is unsafe")


def _require_linux_pidfds() -> None:
    if sys.platform != "linux" or not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise _error("runner-unavailable", "qualification requires Linux pidfd support")
    try:
        descriptor = os.pidfd_open(os.getpid(), 0)
    except OSError as exc:
        raise _error("runner-unavailable", "qualification requires Linux pidfd support") from exc
    os.close(descriptor)


def _fixture_temp_root() -> Path:
    """Create the short private root owned by the qualification lifecycle."""
    root: Path | None = None
    try:
        root = Path(tempfile.mkdtemp(prefix="acq-", dir="/tmp"))
        os.chmod(root, 0o700)
        _private_directory(root)
        return root
    except (OSError, QualificationError, KeyboardInterrupt) as exc:
        if root is not None:
            try:
                if root.is_symlink():
                    root.unlink()
                else:
                    shutil.rmtree(root)
            except OSError:
                pass
        if isinstance(exc, (QualificationError, KeyboardInterrupt)):
            raise
        raise _error("staging-failed", "qualification fixture temporary root is unavailable") from exc


def _remove_fixture_temp_root(root: Path) -> None:
    try:
        _private_directory(root)
        shutil.rmtree(root)
        if root.exists() or root.is_symlink():
            raise OSError("fixture temporary root remains")
    except OSError as exc:
        raise _error("staging-failed", "qualification fixture temporary cleanup failed") from exc


def _environment(config: QualificationConfig, run_dir: Path, *, fixture_tmp: Path) -> dict[str, str]:
    home = run_dir / "home"
    for path in (home, home / ".pki", home / ".pki/nssdb", run_dir / "tmp", run_dir / "xdg-config", run_dir / "xdg-cache", run_dir / "xdg-data", run_dir / "go-cache"):
        path.mkdir(mode=0o700, exist_ok=True)
        _private_directory(path)
    _private_directory(fixture_tmp)
    return {
        "PATH": str(config.tools["go"].parent) + ":" + str(config.tools["certutil"].parent) + ":/usr/bin:/bin",
        "LANG": "C", "LC_ALL": "C",
        "HOME": str(home), "TMPDIR": str(fixture_tmp),
        "XDG_CONFIG_HOME": str(run_dir / "xdg-config"), "XDG_CACHE_HOME": str(run_dir / "xdg-cache"), "XDG_DATA_HOME": str(run_dir / "xdg-data"),
        "GOTOOLCHAIN": "local", "GOPROXY": "off", "GOSUMDB": "off", "GOMAXPROCS": "2", "GOFLAGS": "-p=2",
        "GOMODCACHE": str(config.go_module_cache), "GOCACHE": str(run_dir / "go-cache"),
        "NSS_DEFAULT_DB_TYPE": "sql", "PLAYWRIGHT_BROWSERS_PATH": "0",
        "ANVIL_CONNECT_EDGE_CADDY": str(config.tools["caddy"]),
        "ANVIL_CONNECT_EDGE_AUTHELIA": str(config.tools["authelia"]),
        "ANVIL_CONNECT_WSTUNNEL": str(config.tools["wstunnel"]),
        "ANVIL_CONNECT_CHROMIUM": str(config.tools["chromium"]),
        "ANVIL_CONNECT_QUALIFICATION": "1",
        "ANVIL_CONNECT_GO": str(config.tools["go"]),
    }


def _supervisor_status(output: bytes) -> tuple[str, bool]:
    try:
        value = json.loads(output.decode("utf-8", "strict"))
        stages = {"build", "fixture-startup", "browser-launch-cert", "browser-connection", "browser-dns", "browser-navigation", "browser-assertion", "report-parsing", "timeout", "interrupted", "supervisor"}
        marker = value.get("fixture_marker")
        if set(value) != {"status", "escalated", "failure_stage", "fixture_marker"} or value["status"] not in {"passed", "skipped", "runner-timeout", "runner-interrupted", "runner-failed"} or type(value["escalated"]) is not bool or value["failure_stage"] not in stages | {None} or (marker is not None and not re.fullmatch(r"(?:browser_(?:edge|runtime)_fixture_test\.go|browser_edge\.spec\.mjs):[1-9][0-9]{0,4}", marker)):
            raise ValueError
        status = "runner-failed" if value["escalated"] else value["status"]
        if status == "runner-failed" and value["failure_stage"]:
            status += "-" + value["failure_stage"]
            if marker is not None:
                status += "@" + marker
        return status, value["escalated"]
    except (UnicodeError, TypeError, ValueError, json.JSONDecodeError):
        return "runner-failed-supervisor", True


def _run_test(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float, expected_name: str, supervisor: Path | None = None) -> tuple[str, float, bool]:
    """Ask an isolated supervisor to run one fixture and return its safe status."""
    started = time.monotonic()
    supervisor = supervisor or Path(__file__).with_name("_qualification_supervisor.py")
    try:
        process = subprocess.Popen([sys.executable, str(supervisor), "--timeout", str(timeout), "--expected-name", expected_name, "--", *argv], cwd=cwd, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    except OSError as exc:
        raise _error("runner-unavailable", "qualification browser runner is unavailable") from exc
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    captured = bytearray()
    deadline = started + timeout + 5
    sent_stop = False
    supervisor_escalated = False
    status = "runner-failed"
    try:
        while selector.get_map() and time.monotonic() < deadline:
            for key, _ in selector.select(min(0.05, max(0.0, deadline - time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if len(captured) + len(chunk) > 4096:
                    break
                captured.extend(chunk)
        if time.monotonic() >= deadline and process.poll() is None:
            process.send_signal(signal.SIGTERM)
            sent_stop = True
        if process.poll() is None:
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=1)
                return "runner-failed", time.monotonic() - started, True
        status, supervisor_escalated = _supervisor_status(bytes(captured)) if process.returncode == 0 else ("runner-failed", True)
    except KeyboardInterrupt:
        process.send_signal(signal.SIGTERM)
        sent_stop = True
        try:
            process.wait(timeout=4)
            captured.extend(process.stdout.read(max(0, 4096 - len(captured))))
            status, supervisor_escalated = _supervisor_status(bytes(captured)) if process.returncode == 0 else ("runner-failed", True)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=1)
            status = "runner-failed"
    finally:
        selector.close()
        process.stdout.close()
    return status, time.monotonic() - started, supervisor_escalated or (sent_stop and status == "runner-failed")


def _playwright_version(stage: Path) -> str:
    try:
        lock = json.loads((stage / "connect/package-lock.json").read_text(encoding="utf-8"))
        expected = lock["packages"]["node_modules/playwright"]["version"]
        installed = json.loads((stage / "connect/node_modules/playwright/package.json").read_text(encoding="utf-8"))["version"]
        if expected != "1.63.0" or installed != expected:
            raise ValueError
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _error("tool-invalid", "qualification Playwright dependency is not pinned") from exc
    return expected


def _counts(cases: Iterable[dict[str, Any]]) -> dict[str, int]:
    entries = list(cases)
    return {
        "passed": sum(case["status"] == "passed" for case in entries),
        "failed": sum(case["status"] not in {"passed", "skipped", "not-run"} for case in entries),
        "skipped": sum(case["status"] == "skipped" for case in entries),
        "not_run": sum(case["status"] == "not-run" for case in entries),
    }


def _junit(path: Path, cases: Iterable[dict[str, Any]]) -> None:
    entries = list(cases)
    counts = _counts(entries)
    root = ET.Element("testsuite", name="anvil-connect.browser-edge", tests=str(len(entries)), failures=str(counts["failed"]), skipped=str(counts["skipped"] + counts["not_run"]))
    for case in entries:
        node = ET.SubElement(root, "testcase", name=case["name"], time=f"{case['duration_seconds']:.3f}")
        if case["status"] in {"skipped", "not-run"}:
            ET.SubElement(node, "skipped", type=case["status"], message="qualification browser edge was not executed")
        elif case["status"] != "passed":
            ET.SubElement(node, "failure", type=case["status"], message="qualification browser edge failed")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    os.chmod(path, 0o600)


def qualify(config_path: str | os.PathLike[str] | None = None, *, lane: str = "baseline") -> dict[str, Any]:
    """Qualify the two offline browser-edge fixtures and return safe evidence.

    Configuration/preflight errors raise :class:`QualificationError`; a fixture
    failure returns a failed envelope with static codes and private artifacts.
    """
    if lane != "baseline":
        raise _error("config-invalid", "qualification lane is unsupported")
    path = Path(config_path) if config_path is not None else Path.home() / ".config/anvil-connect/qualification.toml"
    config = _read_config(path)
    _private_directory(config.artifact_root, create=True)
    metadata = _source_metadata(config.source_root)
    locks = _locks(config.source_root)
    for tool in config.tools.values():
        _regular(tool, executable=True)
    _safe_cache(config.go_module_cache)
    _require_linux_pidfds()
    for name, expected in locks["binaries"].items():
        if _sha256(config.tools[name]) != expected:
            raise _error("tool-invalid", "qualification component digest does not match its lock")
    run_dir = config.artifact_root / ("run-" + uuid.uuid4().hex)
    run_dir.mkdir(mode=0o700)
    fixture_tmp: Path | None = None
    execution_started = False
    phase = "staging"
    cases: list[dict[str, Any]] = []
    try:
        fixture_tmp = _fixture_temp_root()
        tracked = _tracked_connect_files(config.source_root)
        stage = _copy_stage(config, run_dir, tracked)
        environment = _environment(config, run_dir, fixture_tmp=fixture_tmp)
        source_files = {
            relative.as_posix(): _sha256(stage / relative)
            for relative in tracked
        }
        metadata = {**metadata, "staged_files_sha256": hashlib.sha256(
            json.dumps(source_files, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(), "supervisor_sha256": _sha256(run_dir / "supervisor.py")}
        evidence: dict[str, Any] = {
            "schema": _SCHEMA, "lane": lane, "source": metadata, "locks": locks["files"],
            "tools": _tool_metadata(config.tools),
            "network": {"fixture_loopback": True, "network_isolation": "not_enforced"},
            "tests": cases, "cleanup": {"owned_process_group": True, "escalation_required": False},
        }
        runner = stage / "connect/node_modules/playwright/cli.js"
        _regular(runner)
        evidence["playwright_version"] = _playwright_version(stage)
        phase = "execution"
        execution_started = True
        run_deadline = time.monotonic() + config.timeout_seconds
        for index, name in enumerate(_TESTS):
            remaining = run_deadline - time.monotonic()
            if remaining <= 0:
                result, duration, escalated = "runner-timeout", 0.0, False
            else:
                result, duration, escalated = _run_test(
                    [str(config.tools["node"]), str(runner), "test", "test/browser_edge.spec.mjs", "--reporter=json", "--grep", re.escape(name) + "$"],
                    cwd=stage / "connect", env=environment, timeout=remaining, expected_name=name, supervisor=run_dir / "supervisor.py",
                )
            failure_stage = result.removeprefix("runner-failed-") if result.startswith("runner-failed-") else None
            fixture_marker = None
            if failure_stage is not None and "@" in failure_stage:
                failure_stage, fixture_marker = failure_stage.split("@", 1)
            status = "runner-failed" if failure_stage else result
            item = {"name": name, "status": status, "duration_seconds": round(duration, 3)}
            if failure_stage is not None:
                item["failure_stage"] = failure_stage
            if fixture_marker is not None:
                item["fixture_marker"] = fixture_marker
            evidence["tests"].append(item)
            evidence["cleanup"]["escalation_required"] |= escalated
            if status != "passed":
                evidence["tests"].extend(
                    {"name": later, "status": "not-run", "duration_seconds": 0.0}
                    for later in _TESTS[index + 1:]
                )
                break
        _junit(run_dir / "junit.xml", evidence["tests"])
        evidence["cleanup"]["fixture_graceful_eof"] = all(item["status"] == "passed" for item in evidence["tests"])
        passed = all(item["status"] == "passed" for item in evidence["tests"])
        error_code = None if passed else next(item["status"] for item in evidence["tests"] if item["status"] != "passed")
        evidence["ok"] = passed
        evidence["state"] = "passed" if passed else ("skipped" if error_code == "skipped" else "failed")
        evidence["error_code"] = error_code
        evidence["counts"] = _counts(evidence["tests"])
        phase = "cleanup"
        volatile_paths = (run_dir / "supervisor.py", run_dir / "home", run_dir / "tmp", run_dir / "xdg-config", run_dir / "xdg-cache", run_dir / "xdg-data", run_dir / "go-cache")
        try:
            shutil.rmtree(stage)
            (run_dir / "supervisor.py").unlink()
            for volatile in volatile_paths[1:]:
                shutil.rmtree(volatile, ignore_errors=True)
            _remove_fixture_temp_root(fixture_tmp)
            fixture_tmp = None
        except OSError as exc:
            raise _error("staging-failed", "qualification private cleanup failed") from exc
        removed = not stage.exists() and all(not path.exists() for path in volatile_paths)
        evidence["cleanup"]["private_stage_removed"] = not stage.exists()
        if not removed:
            raise _error("staging-failed", "qualification private cleanup failed")
        evidence_path = run_dir / "evidence.json"
        evidence_path.write_text(json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        os.chmod(evidence_path, 0o600)
        result_path = run_dir / "result.json"
        result_path.write_text(json.dumps({
            "schema": _SCHEMA, "lane": lane, "source": metadata,
            "ok": passed, "state": evidence["state"], "error_code": error_code,
            "counts": evidence["counts"], "evidence": "evidence.json",
        }, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(result_path, 0o600)
        manifest = run_dir / "SHA256SUMS"
        manifest.write_text("".join(
            f"{_sha256(run_dir / name)}  {name}\n"
            for name in ("evidence.json", "junit.xml", "result.json")
        ), encoding="ascii")
        os.chmod(manifest, 0o600)
        return {"schema": _SCHEMA, "ok": passed, "state": evidence["state"], "error_code": error_code, "artifact_dir": str(run_dir), "counts": evidence["counts"]}
    except KeyboardInterrupt:
        if fixture_tmp is not None:
            try:
                _remove_fixture_temp_root(fixture_tmp)
            except QualificationError:
                pass
        shutil.rmtree(run_dir, ignore_errors=True)
        raise _error("runner-interrupted", "qualification interrupted during preparation", execution_started=execution_started, stage=phase, case_counts=_counts(cases) if len(cases) == len(_TESTS) else None) from None
    except QualificationError as exc:
        if fixture_tmp is not None:
            try:
                _remove_fixture_temp_root(fixture_tmp)
            except QualificationError:
                pass
        shutil.rmtree(run_dir, ignore_errors=True)
        raise _execution_error(exc, execution_started=execution_started, stage=phase, case_counts=_counts(cases) if len(cases) == len(_TESTS) else None) from None
    except (OSError, ValueError, subprocess.SubprocessError):
        if fixture_tmp is not None:
            try:
                _remove_fixture_temp_root(fixture_tmp)
            except QualificationError:
                pass
        shutil.rmtree(run_dir, ignore_errors=True)
        raise _error("staging-failed", "qualification preparation failed", execution_started=execution_started, stage=phase, case_counts=_counts(cases) if len(cases) == len(_TESTS) else None) from None

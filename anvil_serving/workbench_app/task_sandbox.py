"""Pinned, no-network task evidence sandbox.

Pi may have changed its private checkout.  This module is the only boundary
that asks Git to inspect it, and does so inside an unprivileged container with
an empty ambient environment.  The host uses Git only while creating private
clones from the fresh Anvil claim worktree, before Pi receives a checkout.
"""

from __future__ import annotations

import os
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from ..observability.dashboard.contracts import ObservatoryError
from .task_artifacts import MAX_COMMANDS, MAX_OUTPUT_BYTES, TaskArtifactSandbox


_GIT_CONFIG = (
    "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.attributesfile=/dev/null",
    "-c", "diff.external=", "-c", "filter.lfs.process=", "-c", "filter.lfs.required=false",
)
_GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
_CONTAINER_TIMEOUT = 15 * 60
_HOST_TIMEOUT = 120


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    duration_seconds: float


def bounded_command(argv, *, cwd: Path, timeout: int, limit: int, env=None) -> CommandResult:
    """Run a fixed argv with simultaneous bounded stdout/stderr draining."""
    child_env = {key: os.environ[key] for key in ("PATH", "LANG", "SYSTEMROOT") if key in os.environ}
    child_env.update(_GIT_ENV)
    if env:
        child_env.update(env)
    started = time.monotonic()
    process = subprocess.Popen(argv, cwd=cwd, env=child_env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    output, error = bytearray(), bytearray()
    streams = {process.stdout: output, process.stderr: error}
    try:
        with selectors.DefaultSelector() as selector:
            for stream in streams:
                selector.register(stream, selectors.EVENT_READ)
            deadline = started + timeout
            while selector.get_map():
                if time.monotonic() >= deadline:
                    raise TimeoutError("sandbox command timed out")
                for key, _ in selector.select(timeout=min(0.25, max(0, deadline - time.monotonic()))):
                    block = os.read(key.fileobj.fileno(), 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                    else:
                        streams[key.fileobj].extend(block)
                        if len(output) + len(error) > limit:
                            raise ValueError("sandbox command output bound")
        code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        return CommandResult(code, bytes(output), bytes(error), time.monotonic() - started)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        for stream in streams:
            stream.close()


class TaskSandbox(TaskArtifactSandbox):
    """Server-configured Docker boundary for workbench task artifacts."""

    def __init__(self, pi_config, *, run=bounded_command):
        self.engine = pi_config["engine_binary"]
        self.image = pi_config["image"]
        self.uid, self.gid = pi_config["uid"], pi_config["gid"]
        self.cpus = pi_config.get("cpus", 2)
        self.memory = pi_config.get("memory_bytes", 2 * 1024**3)
        self.pids = pi_config.get("pids", 256)
        self.run = run

    def provision(self, source: Path, destination: Path, verification_destination: Path):
        self._absolute(source, destination, verification_destination)
        if destination.exists() or verification_destination.exists():
            raise self._error("sandbox_destination_exists", "The server-owned isolated checkout already exists.")
        clean = self._host_git(source, ("status", "--porcelain=v1", "--untracked-files=all"), allowed=(0,)).stdout
        if clean:
            raise self._error("claim_not_pristine", "The fresh claimed workspace is not clean.")
        baseline = self._host_git(source, ("rev-parse", "HEAD"), allowed=(0,)).stdout.decode("ascii", "strict").strip()
        if len(baseline) not in {40, 64} or any(char not in "0123456789abcdef" for char in baseline):
            raise self._error("sandbox_baseline_invalid", "The claimed workspace did not report a stable baseline.")
        try:
            for destination_path in (destination, verification_destination):
                self._host_git(source.parent, ("clone", "--no-hardlinks", "--local", "--no-checkout", "--", str(source), str(destination_path)), allowed=(0,))
                self._host_git(destination_path, ("checkout", "--detach", "--force", baseline), allowed=(0,))
                self._host_git(destination_path, ("remote", "remove", "origin"), allowed=(0, 2))
                self._container_owner(destination_path)
        except Exception:
            # These paths are created only for this request and have never been
            # mounted into Pi.  Remove them without touching any claim.
            for destination_path in (destination, verification_destination):
                if destination_path.exists():
                    shutil.rmtree(destination_path)
            raise
        return {"runner_checkout": str(destination), "verification_checkout": str(verification_destination), "baseline_sha": baseline}

    def capture(self, source: Path, baseline_sha: str):
        self._absolute(source)
        mounts = ((source, "/runner", True),)
        # Pi may legitimately commit while it works.  Prove the frozen baseline
        # still resolves, then diff from it so committed, staged, unstaged, and
        # untracked changes all remain part of one reviewed artifact.
        baseline = self._container_git("/runner", ("rev-parse", f"{baseline_sha}^{{commit}}"), mounts, allowed=(0,)).stdout.decode("ascii", "strict").strip()
        if baseline != baseline_sha:
            raise self._error("baseline_changed", "The isolated Pi checkout no longer contains its claimed baseline.")
        patch = bytearray(self._container_git("/runner", ("diff", "--binary", "--no-ext-diff", "--no-textconv", baseline_sha), mounts, allowed=(0,)).stdout)
        tracked = self._name_status(self._container_git("/runner", ("diff", "--raw", "-z", "--no-abbrev", baseline_sha), mounts, allowed=(0,)).stdout)
        untracked = self._container_git("/runner", ("ls-files", "--others", "--exclude-standard", "-z"), mounts, allowed=(0,)).stdout.split(b"\0")
        files = list(tracked)
        for raw_path in untracked:
            if not raw_path:
                continue
            path = raw_path.decode("utf-8", "strict")
            self._safe_relative(path)
            result = self._container_git("/runner", ("diff", "--no-index", "--binary", "--no-ext-diff", "--no-textconv", "--", "/dev/null", path), mounts, allowed=(0, 1))
            patch.extend(result.stdout)
            files.append({"path": path, "status": "A", "mode": self._new_file_mode(result.stdout)})
        if len(patch) > 2 * 1024 * 1024:
            raise self._error("artifact_too_large", "The captured patch exceeds the task evidence bound.")
        return {"baseline_sha": baseline_sha, "patch": bytes(patch), "files": files}

    def verify_transfer(self, source: Path, verification_base: Path, target: Path, baseline_sha: str, patch: bytes, commands: tuple[str, ...], *, already_transferred=False):
        self._absolute(source, verification_base, target)
        if not commands or len(commands) > MAX_COMMANDS:
            raise self._error("verification_contract_invalid", "The frozen work packet has no supported verification commands.")
        with tempfile.TemporaryDirectory(prefix="anvil-workbench-evidence-") as raw:
            root = Path(raw)
            patch_file, commands_dir, results_dir = root / "reviewed.patch", root / "commands", root / "results"
            commands_dir.mkdir(mode=0o700)
            results_dir.mkdir(mode=0o700)
            patch_file.write_bytes(patch)
            os.chmod(patch_file, 0o600)
            for index, command in enumerate(commands):
                item = commands_dir / str(index)
                item.write_text(command, encoding="utf-8")
                os.chmod(item, 0o600)
            os.chmod(commands_dir, 0o755)
            os.chmod(results_dir, 0o777)
            os.chmod(patch_file, 0o644)
            self._container_owner(verification_base)
            # The verification container never receives the canonical claim
            # mount. Frozen commands can only alter their disposable verifier.
            mounts = ((verification_base, "/verify", False), (patch_file, "/patch/reviewed.patch", True), (commands_dir, "/commands", True), (results_dir, "/results", False))
            result = self._container(("/bin/bash", "-ceu", self._verify_script(len(commands)), "sandbox"), mounts=mounts, workdir="/tmp", timeout=_CONTAINER_TIMEOUT, extra_env=(f"BASELINE={baseline_sha}",))
            if result.returncode != 0:
                raise self._error("sandbox_verification_failed", "The isolated verifier could not prepare or transfer the reviewed patch.")
            rows = []
            for index, command in enumerate(commands):
                try:
                    code, duration = (results_dir / f"{index}.meta").read_text(encoding="ascii").strip().split(" ", 1)
                    rows.append({"command": command, "exit_code": int(code), "duration_seconds": float(duration),
                                 "stdout": self._read_limited(results_dir / f"{index}.out"), "stderr": self._read_limited(results_dir / f"{index}.err")})
                except (OSError, ValueError):
                    raise self._error("verification_result_invalid", "The isolated verifier did not retain a complete command result.") from None
            passed = all(row["exit_code"] == 0 for row in rows)
        if not passed:
            return {"baseline_sha": baseline_sha, "verification_clean": True, "applied": False, "commands": rows}
        state = self.transfer_state(verification_base, target, baseline_sha, patch)
        if already_transferred:
            if state != "exact":
                raise self._error("transfer_recovery_required", "The claimed workspace no longer matches the reviewed patch exactly.")
            return {"baseline_sha": baseline_sha, "verification_clean": True, "applied": True, "commands": rows}
        if state != "pristine":
            raise self._error("transfer_refused", "The claimed workspace is not pristine for this reviewed transfer.")
        self._transfer(target, patch)
        return {"baseline_sha": baseline_sha, "verification_clean": True, "applied": True, "commands": rows}

    def _verify_script(self, count: int) -> str:
        # Command text is read from server-written files, never interpolated in
        # this control script.  Each child shell has a hard wall-time and output
        # file-size bound.  Failed commands are all retained and skip transfer.
        return "\n".join((
            "set -eu",
            "git -C /verify -c core.hooksPath=/dev/null -c core.fsmonitor=false -c core.attributesfile=/dev/null -c diff.external= reset --hard \"$BASELINE\"",
            "git -C /verify -c core.hooksPath=/dev/null -c core.fsmonitor=false -c core.attributesfile=/dev/null -c diff.external= clean -ffdqx",
            "test \"$(git -C /verify rev-parse HEAD)\" = \"$BASELINE\"",
            "test -z \"$(git -C /verify status --porcelain=v1 --untracked-files=all)\"",
            "git -C /verify apply --binary --whitespace=nowarn /patch/reviewed.patch",
            "failed=0",
            f"for ((n=0; n<{count}; n++)); do",
            "  started=$(date +%s)",
            "  set +e",
            "  (ulimit -f 128; cd /verify && timeout --signal=KILL 300s /bin/bash /commands/$n) > /results/$n.out 2> /results/$n.err",
            "  code=$?",
            "  set -e",
            "  elapsed=$(( $(date +%s) - started ))",
            "  printf '%s %s\\n' \"$code\" \"$elapsed\" > /results/$n.meta",
            "  test \"$code\" -eq 0 || failed=1",
            "done",
            "if test \"$failed\" -ne 0; then exit 0; fi",
        ))

    def transfer_state(self, verification_base: Path, target: Path, baseline_sha: str, patch: bytes) -> str:
        self._absolute(verification_base, target)
        with tempfile.TemporaryDirectory(prefix="anvil-workbench-reconcile-") as raw:
            root = Path(raw)
            patch_file, git_mask = root / "reviewed.patch", root / "git-metadata-mask"
            patch_file.write_bytes(patch)
            git_mask.write_bytes(b"")
            os.chmod(patch_file, 0o644)
            os.chmod(git_mask, 0o444)
            self._container_owner(verification_base)
            self._container_owner(target, change_owner=False)
            mounts = ((verification_base, "/verify", False), *self._claim_mounts(target, git_mask), (patch_file, "/patch/reviewed.patch", True))
            script = "\n".join((
                "set -eu",
                "tree_manifest() { ( cd \"$1\"; LC_ALL=C find . -path ./.git -prune -o -printf '%y %m %p\\0' | LC_ALL=C sort -z; ); }",
                "tree_equal() { diff -qr --no-dereference -x .git \"$1\" \"$2\" >/dev/null && diff -q <(tree_manifest \"$1\") <(tree_manifest \"$2\") >/dev/null; }",
                "git -C /verify -c core.hooksPath=/dev/null -c core.fsmonitor=false -c core.attributesfile=/dev/null -c diff.external= reset --hard \"$BASELINE\" >/dev/null",
                "git -C /verify -c core.hooksPath=/dev/null -c core.fsmonitor=false -c core.attributesfile=/dev/null -c diff.external= clean -ffdqx",
                "if tree_equal /verify /claim; then printf pristine; exit 0; fi",
                "git -C /verify apply --binary --whitespace=nowarn /patch/reviewed.patch",
                "if tree_equal /verify /claim; then printf exact; else printf other; fi",
            ))
            result = self._container(("/bin/bash", "-ceu", script, "sandbox"), mounts=mounts, workdir="/tmp", timeout=120, extra_env=(f"BASELINE={baseline_sha}",))
            if result.returncode != 0 or result.stdout.decode("ascii", "replace") not in {"pristine", "exact", "other"}:
                raise self._error("transfer_recovery_invalid", "The claimed workspace could not be reconciled safely.")
            return result.stdout.decode("ascii")

    def _transfer(self, target: Path, patch: bytes) -> None:
        with tempfile.TemporaryDirectory(prefix="anvil-workbench-transfer-") as raw:
            root = Path(raw)
            patch_file, git_mask = root / "reviewed.patch", root / "git-metadata-mask"
            patch_file.write_bytes(patch)
            git_mask.write_bytes(b"")
            os.chmod(patch_file, 0o644)
            os.chmod(git_mask, 0o444)
            self._container_owner(target, change_owner=False)
            result = self._container(("/bin/bash", "-ceu", "cd /claim; GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -c core.hooksPath=/dev/null -c core.fsmonitor=false -c core.attributesfile=/dev/null -c diff.external= apply --no-index --binary --whitespace=nowarn /patch/reviewed.patch", "sandbox"), mounts=(*self._claim_mounts(target, git_mask), (patch_file, "/patch/reviewed.patch", True)), workdir="/tmp", timeout=120)
            if result.returncode != 0:
                raise self._error("transfer_refused", "The reviewed patch could not be transferred safely.")

    def _host_git(self, cwd: Path, args: tuple[str, ...], *, allowed: tuple[int, ...]):
        result = self.run(("git", *_GIT_CONFIG, *args), cwd=cwd, timeout=_HOST_TIMEOUT, limit=MAX_OUTPUT_BYTES)
        if result.returncode not in allowed:
            raise self._error("sandbox_host_git_failed", "The fresh claim workspace could not be prepared safely.")
        return result

    def _container_git(self, workdir: str, args: tuple[str, ...], mounts, *, allowed):
        result = self._container(("git", *_GIT_CONFIG, *args), mounts=mounts, workdir=workdir, timeout=120)
        if result.returncode not in allowed:
            raise self._error("sandbox_git_failed", "The isolated checkout could not be inspected safely.")
        return result

    def _container(self, command, *, mounts, workdir: str, timeout: int, extra_env=()):
        argv = [self.engine, "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", f"{self.uid}:{self.gid}", "--cpus", str(self.cpus), "--memory", str(self.memory), "--pids-limit", str(self.pids), "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m", "--workdir", workdir]
        for source, target, readonly in mounts:
            self._mount_path(source)
            argv.extend(("--mount", f"type=bind,source={source},target={target}" + (",readonly" if readonly else "")))
        for value in extra_env:
            argv.extend(("--env", value))
        # Override any image default entrypoint: evidence operations may only
        # execute this fixed argv or the server-written frozen command files.
        argv.extend(("--entrypoint", "/bin/bash", self.image, "-ceu", "exec \"$@\"", "sandbox", *command))
        return self.run(tuple(argv), cwd=Path("/"), timeout=timeout, limit=MAX_OUTPUT_BYTES * 2, env=_GIT_ENV)

    @staticmethod
    def _name_status(raw: bytes):
        chunks = raw.split(b"\0")
        result, index = [], 0
        while index < len(chunks) - 1:
            header = chunks[index].decode("ascii", "strict")
            index += 1
            if not header.startswith(":"):
                raise ValueError("malformed raw status")
            fields = header[1:].split()
            if len(fields) != 5:
                raise ValueError("malformed raw status")
            old_mode, new_mode, _old_hash, _new_hash, status = fields
            if (old_mode != "000000" and not TaskSandbox._regular_mode(old_mode)) or (new_mode != "000000" and not TaskSandbox._regular_mode(new_mode)):
                raise ValueError("unsupported raw file mode")
            if status.startswith(("R", "C")):
                if index + 1 >= len(chunks):
                    raise ValueError("truncated rename status")
                old, new = chunks[index].decode("utf-8", "strict"), chunks[index + 1].decode("utf-8", "strict")
                index += 2
                TaskSandbox._safe_relative(old)
                TaskSandbox._safe_relative(new)
                result.extend(({"path": old, "status": "R", "mode": old_mode}, {"path": new, "status": "R", "mode": new_mode}))
            else:
                if index >= len(chunks):
                    raise ValueError("truncated name status")
                path = chunks[index].decode("utf-8", "strict")
                index += 1
                TaskSandbox._safe_relative(path)
                mode = old_mode if status.startswith("D") else new_mode
                result.append({"path": path, "status": status[:1], "mode": mode})
        return result

    @staticmethod
    def _new_file_mode(patch: bytes) -> str:
        for line in patch.splitlines():
            if line.startswith(b"new file mode "):
                mode = line.removeprefix(b"new file mode ").decode("ascii", "strict")
                if TaskSandbox._regular_mode(mode):
                    return mode
        raise ValueError("untracked patch did not report a supported file mode")

    @staticmethod
    def _regular_mode(mode: str) -> bool:
        return len(mode) == 6 and mode.startswith("100") and all(char in "01234567" for char in mode[3:])

    @staticmethod
    def _read_limited(path: Path) -> str:
        try:
            return path.read_bytes()[:MAX_OUTPUT_BYTES].decode("utf-8", "replace")
        except OSError:
            return ""

    @staticmethod
    def _absolute(*paths: Path) -> None:
        if any(not path.is_absolute() for path in paths):
            raise ValueError("task sandbox paths must be absolute")

    @staticmethod
    def _mount_path(path: Path) -> None:
        if not path.is_absolute() or any(char in str(path) for char in ("\n", "\x00", ",")):
            raise ValueError("task sandbox mount path is invalid")

    @staticmethod
    def _claim_mounts(target: Path, git_mask: Path):
        # Anvil worktrees have a linked .git *file* pointing outside the mount.
        # Mask it. A disposable normal repository has an in-tree .git directory
        # and therefore has no shared metadata to conceal.
        mounts = [(target, "/claim", False)]
        if (target / ".git").is_file():
            mounts.append((git_mask, "/claim/.git", True))
        return tuple(mounts)

    def _container_owner(self, path: Path, *, change_owner=True) -> None:
        """Make only server-created/private mounts writable by the pinned UID."""
        if path.stat().st_uid == self.uid:
            return
        if not change_owner or os.geteuid() != 0:
            raise self._error("sandbox_owner_mismatch", "The configured sandbox user cannot write its private workspace.")
        os.chown(path, self.uid, self.gid)

    @staticmethod
    def _safe_relative(path: str) -> None:
        value = Path(path)
        if not path or value.is_absolute() or "\\" in path or any(part in {"", ".", "..", ".git", ".anvil"} for part in value.parts):
            raise ObservatoryError("unsafe_artifact_path", "The isolated checkout reported an unsupported path.", 409)

    @staticmethod
    def _error(code: str, message: str) -> ObservatoryError:
        return ObservatoryError(code, message, 409)


ProductionTaskSandbox = TaskSandbox

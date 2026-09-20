"""Bounded, descriptor-relative reads from server-declared project roots."""

from __future__ import annotations

import hashlib
import os
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote

from ..observability.dashboard.contracts import ObservatoryError, digest, identifier


MAX_TREE_ENTRIES = 200
MAX_RELATIVE_PATH_BYTES = 512
MAX_TEXT_BYTES = 256 * 1024
MAX_GIT_BYTES = 512 * 1024
READ_DEADLINE_SECONDS = 2.0
_LOCAL_OWNER_ID = "local-owner"
_LOCAL_RUNTIME_ID = "local-runtime"
_SECRET_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".kdbx")
_GIT_CONFIG = (
    "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c",
    "core.attributesfile=/dev/null", "-c", "diff.external=", "-c",
    "filter.lfs.process=", "-c", "filter.lfs.required=false",
)


def _error(code: str, message: str, status: int = 409) -> ObservatoryError:
    return ObservatoryError(code, message, status)


def _safe_relative(value: object, *, allow_empty: bool) -> tuple[str, ...]:
    if type(value) is not str:
        raise _error("unsafe_project_path", "Select a declared relative project path.")
    decoded = value
    for _ in range(MAX_RELATIVE_PATH_BYTES):
        candidate = unquote(decoded)
        if candidate == decoded:
            break
        decoded = candidate
    else:
        raise _error("unsafe_project_path", "Select a declared relative project path.")
    try:
        raw = decoded.encode("utf-8", "strict")
    except UnicodeError:
        raise _error("unsafe_project_path", "Select a declared relative project path.") from None
    if (
        len(raw) > MAX_RELATIVE_PATH_BYTES
        or "\\" in decoded
        or any(ord(char) < 32 or ord(char) == 127 for char in decoded)
    ):
        raise _error("unsafe_project_path", "Select a declared relative project path.")
    if not decoded:
        if allow_empty:
            return ()
        raise _error("unsafe_project_path", "Select a declared relative project path.")
    path = Path(decoded)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in decoded.split("/")):
        raise _error("unsafe_project_path", "Select a declared relative project path.")
    parts = tuple(path.parts)
    if any(_protected_name(part) for part in parts):
        raise _error("project_file_unavailable", "This project file is unavailable.")
    return parts


def _protected_name(name: str) -> bool:
    lowered = name.casefold()
    return (
        lowered in {".git", ".anvil", ".ssh"}
        or lowered.startswith(".env")
        or lowered in {"credentials", "credential", "secrets", "secret", "id_rsa", "id_ed25519"}
        or "secret" in lowered
        or "credential" in lowered
        or lowered.endswith(_SECRET_SUFFIXES)
    )


class ProjectFiles:
    """Read only local, declared project roots after ``Projects.project`` authorizes."""

    def __init__(self, projects, *, clock=time.monotonic, deadline_seconds=READ_DEADLINE_SECONDS,
                 diff_runner=None):
        if type(deadline_seconds) not in (int, float) or not 0 < deadline_seconds <= 10:
            raise ValueError("project read deadline must be between zero and ten seconds")
        self.projects = projects
        self.clock = clock
        self.deadline_seconds = float(deadline_seconds)
        self.diff_runner = diff_runner or self._native_diff

    def tree(self, session, project_id, root_id, relative_path=""):
        root = self._root(session, project_id, root_id)
        return self._tree(root, relative_path)

    def frozen_roots(self, session, binding_id):
        """Return safe identifiers for a prepared task's immutable root set."""
        row = self.projects.binding(session, binding_id)
        roots = self._checked_frozen_roots(row)
        review = row.get("root_review") if isinstance(row.get("root_review"), dict) else {}
        reviewed = {
            item.get("root_id") for item in review.get("roots", ())
            if isinstance(item, dict)
        }
        return {
            "binding_id": row["id"],
            "source": "frozen-task-workspace",
            "roots": [
                {"root_id": root["root_id"], "baseline_sha": root["baseline_sha"],
                 "reviewed": root["root_id"] in reviewed}
                for root in roots
            ],
        }

    def frozen_tree(self, session, binding_id, root_id, relative_path=""):
        _, root = self._frozen_root(session, binding_id, root_id)
        return self._tree({"id": root["root_id"], "path": root["runner_checkout"]}, relative_path)

    def _tree(self, root, relative_path):
        deadline = self._deadline()
        with self._root_fd(root) as root_fd:
            with self._open_directory(root_fd, _safe_relative(relative_path, allow_empty=True)) as directory:
                items = []
                truncated = False
                inspected = 0
                try:
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            self._check_deadline(deadline)
                            if inspected >= MAX_TREE_ENTRIES * 4:
                                truncated = True
                                break
                            inspected += 1
                            if len(items) >= MAX_TREE_ENTRIES:
                                truncated = True
                                break
                            try:
                                _safe_relative(entry.name, allow_empty=False)
                            except ObservatoryError:
                                continue
                            if _protected_name(entry.name) or entry.is_symlink():
                                continue
                            try:
                                metadata = entry.stat(follow_symlinks=False)
                            except OSError:
                                continue
                            if stat.S_ISDIR(metadata.st_mode):
                                kind = "directory"
                            elif stat.S_ISREG(metadata.st_mode):
                                kind = "file"
                            else:
                                continue
                            items.append({"name": entry.name, "kind": kind})
                except OSError:
                    raise _error("project_file_unavailable", "This project directory is unavailable.") from None
        items.sort(key=lambda item: (item["kind"] != "directory", item["name"].casefold(), item["name"]))
        return {
            "root_id": root["id"],
            "path": "/".join(_safe_relative(relative_path, allow_empty=True)),
            "items": items,
            "truncated": truncated,
        }

    def text(self, session, project_id, root_id, relative_path):
        root = self._root(session, project_id, root_id)
        return self._text(root, relative_path)

    def frozen_text(self, session, binding_id, root_id, relative_path):
        _, root = self._frozen_root(session, binding_id, root_id)
        return self._text({"id": root["root_id"], "path": root["runner_checkout"]}, relative_path)

    def _text(self, root, relative_path):
        deadline = self._deadline()
        parts = _safe_relative(relative_path, allow_empty=False)
        try:
            with self._root_fd(root) as root_fd:
                with self._open_file(root_fd, parts) as descriptor:
                    metadata = os.fstat(descriptor)
                    if not stat.S_ISREG(metadata.st_mode):
                        raise _error("project_file_unavailable", "This project file is unavailable.")
                    if metadata.st_size > MAX_TEXT_BYTES:
                        raise _error("project_file_too_large", "This project file exceeds the text preview limit.", 413)
                    data = bytearray()
                    while True:
                        self._check_deadline(deadline)
                        chunk = os.read(descriptor, min(65536, MAX_TEXT_BYTES + 1 - len(data)))
                        if not chunk:
                            break
                        data.extend(chunk)
                        if len(data) > MAX_TEXT_BYTES:
                            raise _error("project_file_too_large", "This project file exceeds the text preview limit.", 413)
        except OSError:
            raise _error("project_file_unavailable", "This project file is unavailable.") from None
        if b"\0" in data:
            raise _error("project_text_unavailable", "This project file is not text.")
        try:
            content = bytes(data).decode("utf-8", "strict")
        except UnicodeDecodeError:
            raise _error("project_text_unavailable", "This project file is not text.") from None
        return {"root_id": root["id"], "path": "/".join(parts), "content": content}

    def frozen_diff(self, session, binding_id, root_id):
        """Return only a revalidated reviewed patch; never invoke host Git here."""
        row, root = self._frozen_root(session, binding_id, root_id)
        review = row.get("root_review")
        if not isinstance(review, dict) or review.get("rootset_digest") != row.get("root_binding_digest"):
            raise _error("evidence_unavailable", "A reviewed frozen patch is unavailable.")
        entries = review.get("roots")
        if not isinstance(entries, list) or digest(entries) != review.get("manifest_digest"):
            raise _error("evidence_tampered", "The reviewed frozen patch is unavailable.")
        frozen = {item["root_id"] for item in self._checked_frozen_roots(row)}
        reviewed = {item.get("root_id"): item for item in entries if isinstance(item, dict)}
        if len(reviewed) != len(entries) or set(reviewed) != frozen:
            raise _error("evidence_tampered", "The reviewed frozen patch is unavailable.")
        entry = reviewed.get(root["root_id"])
        if not isinstance(entry, dict) or entry.get("baseline_sha") != root["baseline_sha"]:
            raise _error("evidence_tampered", "The reviewed frozen patch is unavailable.")
        artifact_digest = entry.get("artifact_digest")
        if type(artifact_digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", artifact_digest):
            raise _error("evidence_tampered", "The reviewed frozen patch is unavailable.")
        artifact_row = row | root
        try:
            preview = self.projects.artifacts.load(artifact_row, artifact_digest)
            with self._root_fd({"id": root["root_id"], "path": root["artifact_root"]}) as artifact_fd:
                with self._open_file(artifact_fd, (artifact_digest + ".patch",)) as descriptor:
                    patch = self._read_descriptor(descriptor, MAX_GIT_BYTES, self._deadline())
        except (OSError, ObservatoryError):
            raise _error("evidence_tampered", "The reviewed frozen patch is unavailable.") from None
        if (
            hashlib.sha256(patch).hexdigest() != artifact_digest
            or preview.get("files") != entry.get("files")
            or preview.get("baseline_sha") != root["baseline_sha"]
            or preview.get("packet_digest") != row.get("packet_digest")
        ):
            raise _error("evidence_tampered", "The reviewed frozen patch is unavailable.")
        if b"\0" in patch:
            raise _error("project_diff_unavailable", "This reviewed patch is unavailable.")
        try:
            text = patch.decode("utf-8", "strict")
        except UnicodeDecodeError:
            raise _error("project_diff_unavailable", "This reviewed patch is unavailable.") from None
        return {"root_id": root["root_id"], "kind": "reviewed", "diff": text,
                "artifact_digest": artifact_digest}

    def frozen_worktree(self, session, binding_id, root_id):
        """Expose immutable task identity without consulting Git in an agent checkout."""
        row, root = self._frozen_root(session, binding_id, root_id)
        transfer = row.get("root_transfer") if isinstance(row.get("root_transfer"), dict) else {}
        return {
            "root_id": root["root_id"],
            "source": "frozen-task-workspace",
            "baseline_sha": root["baseline_sha"],
            "packet_digest": root["packet_digest"],
            "binding_digest": row["root_binding_digest"],
            "transfer_state": (transfer.get(root["root_id"]) or {}).get("state", "not_reviewed"),
        }

    def diff(self, session, project_id, root_id, relative_path, *, kind="working"):
        root = self._root(session, project_id, root_id)
        parts = _safe_relative(relative_path, allow_empty=False)
        if kind not in {"working", "staged"}:
            raise _error("invalid_project_diff", "Select a supported project diff.")
        deadline = self._deadline()
        with self._root_fd(root) as root_fd:
            with self._open_file(root_fd, parts) as descriptor:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise _error("project_file_unavailable", "This project file is unavailable.")
                working = self._read_descriptor(descriptor, MAX_GIT_BYTES, deadline)
            self._checkout(root_fd, deadline)
            pathspec = f":(literal){'/'.join(parts)}"
            head = self._tree_blob(root_fd, "HEAD", pathspec, deadline)
            index = self._index_blob(root_fd, pathspec, deadline)
        if kind == "working":
            before, after = (index if index is not None else b""), working
        else:
            before, after = (head if head is not None else b""), (index if index is not None else b"")
        if b"\0" in before or b"\0" in after:
            raise _error("project_diff_unavailable", "This project diff is unavailable.")
        try:
            before.decode("utf-8", "strict")
            after.decode("utf-8", "strict")
            rendered = self.diff_runner(before, after, parts, deadline)
            self._check_deadline(deadline)
            if type(rendered) is not bytes or len(rendered) > MAX_GIT_BYTES:
                raise _error("project_diff_too_large", "The project diff exceeds its display limit.", 413)
            text = rendered.decode("utf-8", "strict")
        except UnicodeDecodeError:
            raise _error("project_diff_unavailable", "This project diff is unavailable.") from None
        return {"root_id": root["id"], "path": "/".join(parts), "kind": kind, "diff": text}

    def worktree(self, session, project_id, root_id):
        root = self._root(session, project_id, root_id)
        deadline = self._deadline()
        with self._root_fd(root) as root_fd:
            head = self._checkout(root_fd, deadline)
            branch = self._git(root_fd, ("symbolic-ref", "--short", "-q", "HEAD"), deadline, allowed=(0, 1))
            filters = self._filter_overrides(root_fd, deadline)
            status = self._git(root_fd, ("status", "--porcelain=v1", "--untracked-files=no"), deadline, config=filters)
        try:
            branch_text = branch.decode("utf-8", "strict").strip() or "detached"
            dirty = bool(status)
        except UnicodeDecodeError:
            raise _error("project_worktree_unavailable", "This project worktree is unavailable.") from None
        return {"root_id": root["id"], "head": head, "branch": branch_text, "dirty": dirty}

    def _root(self, session, project_id, root_id):
        project = self.projects.project(session, project_id)
        selected = identifier(root_id)
        root = next((item for item in project.get("roots", ()) if item["id"] == selected), None)
        if root is None:
            raise _error("project_root_not_found", "This project root is unavailable.", 404)
        if root["owner_id"] != _LOCAL_OWNER_ID or root["runtime_id"] != _LOCAL_RUNTIME_ID:
            raise _error("project_root_unsupported", "This project root owner is not available for local reads.", 503)
        if not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW")):
            raise _error("project_root_unsupported", "This platform cannot safely read project roots.", 503)
        return root

    def _frozen_root(self, session, binding_id, root_id):
        row = self.projects.binding(session, binding_id)
        selected = identifier(root_id)
        roots = self._checked_frozen_roots(row)
        root = next((item for item in roots if item["root_id"] == selected), None)
        if root is None:
            raise _error("project_root_not_found", "This frozen task root is unavailable.", 404)
        if not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW")):
            raise _error("project_root_unsupported", "This platform cannot safely read task roots.", 503)
        return row, root

    def _checked_frozen_roots(self, row):
        roots = self.projects._frozen_roots(row)
        for root in roots:
            try:
                root_id = root["root_id"]
                identifier(root_id)
                baseline = root["baseline_sha"]
                packet = root["packet_digest"]
                checkout = root["runner_checkout"]
                artifact_root = root["artifact_root"]
            except (KeyError, TypeError, ObservatoryError):
                raise _error("task_root_binding_lost", "The frozen task root binding is unavailable.") from None
            if (
                type(baseline) is not str
                or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", baseline)
                or type(packet) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", packet)
                or any(type(path) is not str or not os.path.isabs(path) or any(ord(char) < 32 for char in path)
                       for path in (checkout, artifact_root))
            ):
                raise _error("task_root_binding_lost", "The frozen task root binding is unavailable.")
        return roots

    def _deadline(self):
        return self.clock() + self.deadline_seconds

    def _check_deadline(self, deadline):
        if self.clock() >= deadline:
            raise _error("project_read_timeout", "The project owner did not complete this read in time.", 503)

    @contextmanager
    def _root_fd(self, root):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        descriptor = None
        try:
            parts = Path(root["path"]).parts
            descriptor = os.open(Path(root["path"]).anchor, flags)
            for part in parts[1:]:
                next_descriptor = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise _error("project_root_unavailable", "This project root is unavailable.")
            yield descriptor
        except OSError:
            raise _error("project_root_unavailable", "This project root is unavailable.") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @contextmanager
    def _open_directory(self, root_fd, parts):
        descriptor = os.dup(root_fd)
        try:
            for part in parts:
                next_descriptor = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = next_descriptor
            yield descriptor
        except OSError:
            raise _error("project_file_unavailable", "This project directory is unavailable.") from None
        finally:
            os.close(descriptor)

    @contextmanager
    def _open_file(self, root_fd, parts):
        descriptor = os.dup(root_fd)
        try:
            for part in parts[:-1]:
                next_descriptor = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = next_descriptor
            file_descriptor = os.open(
                parts[-1], os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=descriptor,
            )
            yield file_descriptor
        except OSError:
            raise _error("project_file_unavailable", "This project file is unavailable.") from None
        finally:
            os.close(descriptor)
            if "file_descriptor" in locals():
                os.close(file_descriptor)

    def _checkout(self, root_fd, deadline):
        top_level = self._git(root_fd, ("rev-parse", "--show-toplevel"), deadline)
        head = self._git(root_fd, ("rev-parse", "HEAD"), deadline)
        try:
            checkout = Path(top_level.decode("utf-8", "strict").strip())
            metadata = checkout.stat()
            head_text = head.decode("ascii", "strict").strip()
        except (OSError, UnicodeDecodeError):
            raise _error("project_worktree_unavailable", "This project worktree is unavailable.") from None
        root_metadata = os.fstat(root_fd)
        if (
            (metadata.st_dev, metadata.st_ino) != (root_metadata.st_dev, root_metadata.st_ino)
            or not re.fullmatch(r"[0-9a-f]{40,64}", head_text)
        ):
            raise _error("project_root_not_checkout", "This declared root is not its execution checkout.")
        return head_text

    def _filter_overrides(self, root_fd, deadline):
        names = self._git(
            root_fd,
            ("config", "--null", "--includes", "--name-only", "--get-regexp", r"^filter\."),
            deadline,
            allowed=(0, 1),
        )
        overrides = []
        for raw_name in names.split(b"\0"):
            if not raw_name:
                continue
            try:
                name = raw_name.decode("ascii", "strict")
            except UnicodeDecodeError:
                raise _error("project_git_unavailable", "This project Git read is unavailable.") from None
            match = re.fullmatch(r"filter\.([A-Za-z0-9_-]{1,64})\.(?:clean|smudge|process|required)", name)
            if match is None:
                raise _error("project_git_unavailable", "This project Git read is unavailable.")
            driver = match.group(1)
            overrides.extend((
                "-c", f"filter.{driver}.clean=", "-c", f"filter.{driver}.smudge=", "-c",
                f"filter.{driver}.process=", "-c", f"filter.{driver}.required=false",
            ))
        return tuple(overrides)

    def _read_descriptor(self, descriptor, maximum, deadline):
        if os.fstat(descriptor).st_size > maximum:
            raise _error("project_diff_too_large", "The project diff exceeds its display limit.", 413)
        result = bytearray()
        while True:
            self._check_deadline(deadline)
            block = os.read(descriptor, min(65536, maximum + 1 - len(result)))
            if not block:
                return bytes(result)
            result.extend(block)
            if len(result) > maximum:
                raise _error("project_diff_too_large", "The project diff exceeds its display limit.", 413)

    def _native_diff(self, before, after, parts, deadline):
        with tempfile.TemporaryDirectory(prefix="anvil-workbench-diff-") as directory:
            root = Path(directory)
            left, right = root / "before", root / "after"
            left.write_bytes(before)
            right.write_bytes(after)
            return self._bounded_child(
                ("diff", "-u", "--label", "a/" + "/".join(parts), "--label", "b/" + "/".join(parts),
                 str(left), str(right)),
                deadline,
                allowed=(0, 1),
            )

    def _bounded_child(self, arguments, deadline, *, allowed):
        environment = {key: os.environ[key] for key in ("PATH", "LANG", "SYSTEMROOT") if key in os.environ}
        return self._bounded_process(
            arguments,
            deadline,
            allowed=allowed,
            environment=environment,
            unavailable_code="project_diff_unavailable",
            unavailable_message="This project diff is unavailable.",
        )

    def _bounded_process(self, arguments, deadline, *, allowed, environment, unavailable_code,
                         unavailable_message, cwd=None, pass_fds=()):
        try:
            process = subprocess.Popen(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
                env=environment,
                cwd=cwd,
                pass_fds=pass_fds,
            )
        except OSError:
            raise _error(unavailable_code, unavailable_message) from None
        output = bytearray()
        assert process.stdout is not None
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    self._check_deadline(deadline)
                    for key, _ in selector.select(min(0.1, max(0.0, deadline - self.clock()))):
                        block = os.read(key.fileobj.fileno(), 65536)
                        if not block:
                            selector.unregister(key.fileobj)
                            continue
                        output.extend(block)
                        if len(output) > MAX_GIT_BYTES:
                            raise _error("project_diff_too_large", "The project diff exceeds its display limit.", 413)
            self._check_deadline(deadline)
            code = process.wait(timeout=max(0.01, deadline - self.clock()))
        except (OSError, subprocess.SubprocessError):
            raise _error(unavailable_code, unavailable_message) from None
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            process.stdout.close()
        if code not in allowed:
            raise _error(unavailable_code, unavailable_message)
        return bytes(output)

    def _tree_blob(self, root_fd, tree, pathspec, deadline):
        raw = self._git(root_fd, ("ls-tree", "-z", tree, "--", pathspec), deadline)
        object_id = self._entry_object(raw, pathspec)
        return self._git(root_fd, ("cat-file", "blob", object_id), deadline) if object_id else None

    def _index_blob(self, root_fd, pathspec, deadline):
        raw = self._git(root_fd, ("ls-files", "--stage", "-z", "--", pathspec), deadline)
        object_id = self._entry_object(raw, pathspec)
        return self._git(root_fd, ("cat-file", "blob", object_id), deadline) if object_id else None

    def _entry_object(self, raw, pathspec):
        entries = [entry for entry in raw.split(b"\0") if entry]
        if not entries:
            return None
        if len(entries) != 1:
            raise _error("project_diff_unavailable", "This project diff is unavailable.")
        try:
            header, name = entries[0].split(b"\t", 1)
            fields = header.split()
            mode = fields[0]
            object_id = fields[2] if len(fields) == 3 and fields[1] == b"blob" else fields[1]
        except (IndexError, ValueError):
            raise _error("project_diff_unavailable", "This project diff is unavailable.") from None
        try:
            name_text = name.decode("utf-8", "strict")
        except UnicodeDecodeError:
            raise _error("project_diff_unavailable", "This project diff is unavailable.") from None
        if name_text != pathspec.removeprefix(":(literal)") or mode not in {b"100644", b"100755"}:
            raise _error("project_diff_unavailable", "This project diff is unavailable.")
        try:
            object_text = object_id.decode("ascii", "strict")
        except UnicodeDecodeError:
            raise _error("project_diff_unavailable", "This project diff is unavailable.") from None
        if not re.fullmatch(r"[0-9a-f]{40,64}", object_text):
            raise _error("project_diff_unavailable", "This project diff is unavailable.")
        # The blob object is selected only after the literal, safe path lookup.
        return object_id

    def _git(self, root_fd, arguments, deadline, *, allowed=(0,), config=()):
        executable_cwd = f"/proc/self/fd/{root_fd}"
        environment = {key: os.environ[key] for key in ("PATH", "LANG", "SYSTEMROOT") if key in os.environ}
        environment.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL="/dev/null",
            GIT_NO_LAZY_FETCH="1",
            GIT_OPTIONAL_LOCKS="0",
            GIT_TERMINAL_PROMPT="0",
        )
        return self._bounded_process(
            ("git", *_GIT_CONFIG, *config, *arguments),
            deadline,
            allowed=allowed,
            environment=environment,
            cwd=executable_cwd,
            pass_fds=(root_fd,),
            unavailable_code="project_git_unavailable",
            unavailable_message="This project Git read is unavailable.",
        )

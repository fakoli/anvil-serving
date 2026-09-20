"""Disposable project-tree regression coverage for the read-only explorer owner."""

from __future__ import annotations

import os
import subprocess

import pytest

from anvil_serving.observability.dashboard.contracts import ObservatoryError
from anvil_serving.workbench_app import project_files
from anvil_serving.workbench_app.project_files import MAX_GIT_BYTES, MAX_TEXT_BYTES, ProjectFiles


_SAFE_DESCRIPTOR_ROOTS = os.name == "posix" and all(
    hasattr(os, name) for name in ("O_DIRECTORY", "O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK")
)
requires_safe_project_roots = pytest.mark.skipif(
    not _SAFE_DESCRIPTOR_ROOTS,
    reason="requires safe POSIX project-root descriptors",
)
requires_safe_fifo = pytest.mark.skipif(
    not (_SAFE_DESCRIPTOR_ROOTS and hasattr(os, "mkfifo")),
    reason="requires safe POSIX project-root descriptors and FIFO support",
)


class Projects:
    def __init__(self, project):
        self.project_row = project
        self.calls = []

    def project(self, session, project_id):
        self.calls.append((session, project_id))
        if project_id != self.project_row["id"]:
            raise ObservatoryError("not_found", "This project is unavailable.", 404)
        return self.project_row


def _root(root_id, path, **extra):
    root = {
        "id": root_id,
        "label": root_id.title(),
        "owner_id": "local-owner",
        "runtime_id": "local-runtime",
        "task_access": "read-write",
        "path": str(path),
    }
    root.update(extra)
    return root


def _reader(tmp_path, roots=None, **kwargs):
    root = tmp_path / "checkout"
    root.mkdir(exist_ok=True)
    project = {"id": "product", "resource_id": "serve-a", "roots": roots or [_root("primary", root)]}
    projects = Projects(project)
    return ProjectFiles(projects, **kwargs), projects, root


def _error(call, code):
    with pytest.raises(ObservatoryError) as caught:
        call()
    assert caught.value.code == code


@requires_safe_project_roots
def test_tree_and_text_are_authorized_bounded_and_never_return_absolute_paths(tmp_path):
    reader, projects, root = _reader(tmp_path)
    (root / "docs").mkdir()
    (root / "docs" / "guide.txt").write_text("hello", encoding="utf-8")
    (root / ".env").write_text("private", encoding="utf-8")
    (root / "secrets.txt").write_text("private", encoding="utf-8")
    (root / ".git").mkdir()
    (root / "line\r\nname").write_text("private", encoding="utf-8")
    (root / "pipe").symlink_to(root / "docs" / "guide.txt")

    tree = reader.tree("session", "product", "primary")
    assert tree == {"root_id": "primary", "path": "", "items": [{"name": "docs", "kind": "directory"}], "truncated": False}
    text = reader.text("session", "product", "primary", "docs/guide.txt")
    assert text == {"root_id": "primary", "path": "docs/guide.txt", "content": "hello"}
    assert str(root) not in repr(tree) + repr(text)
    assert projects.calls == [("session", "product"), ("session", "product")]


@requires_safe_project_roots
@pytest.mark.parametrize("path", ["/etc/passwd", "../escape", "%252e%252e/escape", "docs%2f..%2fescape", "docs//guide", "docs/./guide", "docs/line\r\n", ".env"])
def test_paths_reject_absolute_encoded_and_secret_input(tmp_path, path):
    reader, _, _ = _reader(tmp_path)
    _error(lambda: reader.text("session", "product", "primary", path), "unsafe_project_path" if path != ".env" else "project_file_unavailable")


@requires_safe_fifo
def test_regular_open_rejects_symlink_special_binary_and_oversized_files(tmp_path):
    reader, _, root = _reader(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (root / "link.txt").symlink_to(outside)
    (root / "binary.bin").write_bytes(b"a\0b")
    (root / "large.txt").write_bytes(b"x" * (MAX_TEXT_BYTES + 1))
    os.mkfifo(root / "fifo")
    cases = [
        ("link.txt", "project_file_unavailable"),
        ("fifo", "project_file_unavailable"),
        ("binary.bin", "project_text_unavailable"),
        ("large.txt", "project_file_too_large"),
    ]

    for path, code in cases:
        _error(lambda path=path: reader.text("session", "product", "primary", path), code)


@requires_safe_project_roots
def test_openat_keeps_the_original_root_when_configured_path_is_replaced(tmp_path, monkeypatch):
    reader, _, root = _reader(tmp_path)
    (root / "safe.txt").write_text("safe", encoding="utf-8")
    escape = tmp_path / "escape"
    escape.mkdir()
    (escape / "safe.txt").write_text("escape", encoding="utf-8")
    original = project_files.os.open
    moved = tmp_path / "moved"
    replaced = False

    def raced(path, flags, *args, **kwargs):
        nonlocal replaced
        descriptor = original(path, flags, *args, **kwargs)
        if not replaced and path == root.name and "dir_fd" in kwargs:
            replaced = True
            root.rename(moved)
            root.symlink_to(escape, target_is_directory=True)
        return descriptor

    monkeypatch.setattr(project_files.os, "open", raced)
    assert reader.text("session", "product", "primary", "safe.txt")["content"] == "safe"
    assert replaced


@requires_safe_project_roots
def test_root_open_refuses_a_symlinked_ancestor(tmp_path):
    real_parent = tmp_path / "real"
    root = real_parent / "checkout"
    root.mkdir(parents=True)
    (root / "safe.txt").write_text("safe", encoding="utf-8")
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    project = {"id": "product", "resource_id": "serve-a", "roots": [_root("primary", linked_parent / "checkout")]}
    _error(lambda: ProjectFiles(Projects(project)).text("session", "product", "primary", "safe.txt"), "project_root_unavailable")


def test_unresolved_owner_and_cross_project_never_open_a_root(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    reader, projects, _ = _reader(tmp_path, [_root("remote", root, owner_id="remote-owner")])
    _error(lambda: reader.tree("session", "product", "remote"), "project_root_unsupported")
    _error(lambda: reader.tree("session", "other-project", "remote"), "not_found")
    assert projects.calls == [("session", "product"), ("session", "other-project")]


def test_missing_safe_descriptor_flags_fail_closed(tmp_path, monkeypatch):
    reader, _, root = _reader(tmp_path)
    (root / "safe.txt").write_text("safe", encoding="utf-8")
    monkeypatch.delattr(project_files.os, "O_DIRECTORY", raising=False)
    _error(lambda: reader.text("session", "product", "primary", "safe.txt"), "project_root_unsupported")


def _git(root, *args):
    return subprocess.run(("git", *args), cwd=root, check=True, capture_output=True)


@requires_safe_project_roots
def test_diff_and_worktree_use_the_declared_checkout_and_literal_path_arguments(tmp_path):
    reader, _, root = _reader(tmp_path)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "config", "user.name", "Test")
    (root / "-literal.txt").write_text("old\n", encoding="utf-8")
    _git(root, "add", "--", "-literal.txt")
    _git(root, "commit", "-qm", "initial")
    (root / "-literal.txt").write_text("new\n", encoding="utf-8")

    diff = reader.diff("session", "product", "primary", "-literal.txt")
    worktree = reader.worktree("session", "product", "primary")
    assert "-literal.txt" in diff["diff"] and diff["kind"] == "working" and str(root) not in diff["diff"]
    assert worktree["root_id"] == "primary" and worktree["dirty"] is True
    assert len(worktree["head"]) == 40 and str(root) not in repr(worktree)


@requires_safe_project_roots
def test_diff_uses_literal_safe_regular_files_and_disables_hostile_filters(tmp_path):
    reader, _, root = _reader(tmp_path)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "config", "user.name", "Test")
    (root / "normal.txt").write_text("old\n", encoding="utf-8")
    (root / ".gitattributes").write_text("normal.txt filter=hostile\n", encoding="utf-8")
    _git(root, "add", "normal.txt", ".gitattributes")
    _git(root, "commit", "-qm", "initial")
    (root / "normal.txt").write_text("new\n", encoding="utf-8")
    working = reader.diff("session", "product", "primary", "normal.txt")
    _git(root, "add", "normal.txt")
    staged = reader.diff("session", "product", "primary", "normal.txt", kind="staged")
    marker = tmp_path / "host-command-ran"
    include = tmp_path / "hostile-filter.ini"
    include.write_text(f'[filter "hostile"]\n\tclean = sh -c "touch {marker}"\n', encoding="utf-8")
    _git(root, "config", "include.path", str(include))
    (root / "normal.txt").write_text("newer\n", encoding="utf-8")
    (root / ":(glob)**").write_text("literal\n", encoding="utf-8")
    _git(root, "add", "--", ":(literal):(glob)**")
    (root / "nested").mkdir()
    (root / "nested" / ".env").write_text("private", encoding="utf-8")

    filtered = reader.diff("session", "product", "primary", "normal.txt")
    literal = reader.diff("session", "product", "primary", ":(glob)**", kind="staged")
    worktree = reader.worktree("session", "product", "primary")
    assert "new" in working["diff"] and staged["kind"] == "staged" and "literal" in literal["diff"]
    assert "newer" in filtered["diff"]
    assert worktree["dirty"] is True
    assert not marker.exists()
    _error(lambda: reader.diff("session", "product", "primary", "nested"), "project_file_unavailable")
    _error(lambda: reader.diff("session", "product", "primary", "normal.txt", kind="all"), "invalid_project_diff")


@requires_safe_project_roots
def test_diff_does_not_follow_a_deleted_or_nonregular_target(tmp_path):
    reader, _, root = _reader(tmp_path)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "config", "user.name", "Test")
    (root / "deleted.txt").write_text("old\n", encoding="utf-8")
    _git(root, "add", "deleted.txt")
    _git(root, "commit", "-qm", "initial")
    (root / "deleted.txt").unlink()

    _error(lambda: reader.diff("session", "product", "primary", "deleted.txt"), "project_file_unavailable")


@requires_safe_project_roots
def test_staged_empty_file_remains_a_staged_deletion(tmp_path):
    reader, _, root = _reader(tmp_path)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "config", "user.name", "Test")
    (root / "empty.txt").write_text("old\n", encoding="utf-8")
    _git(root, "add", "empty.txt")
    _git(root, "commit", "-qm", "initial")
    (root / "empty.txt").write_bytes(b"")
    _git(root, "add", "empty.txt")

    staged = reader.diff("session", "product", "primary", "empty.txt", kind="staged")
    assert "-old" in staged["diff"] and "+old" not in staged["diff"]


@requires_safe_project_roots
def test_diff_refuses_binary_bytes_and_bounded_renderer_failures(tmp_path):
    reader, projects, root = _reader(tmp_path)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "config", "user.name", "Test")
    (root / "binary.bin").write_bytes(b"old\0")
    (root / "invalid.txt").write_bytes(b"old\xff")
    _git(root, "add", "binary.bin", "invalid.txt")
    _git(root, "commit", "-qm", "initial")
    (root / "binary.bin").write_bytes(b"new\0")
    _error(lambda: reader.diff("session", "product", "primary", "binary.bin"), "project_diff_unavailable")
    (root / "invalid.txt").write_bytes(b"new\xff")
    _error(lambda: reader.diff("session", "product", "primary", "invalid.txt"), "project_diff_unavailable")

    (root / "text.txt").write_text("content\n", encoding="utf-8")
    def oversized(*_args):
        return b"x" * (MAX_GIT_BYTES + 1)
    oversized_reader = ProjectFiles(projects, diff_runner=oversized)
    _error(lambda: oversized_reader.diff("session", "product", "primary", "text.txt"), "project_diff_too_large")

    slow_reader = ProjectFiles(projects)
    def slow(*args):
        slow_reader.clock = lambda: args[-1] + 1
        return b""
    slow_reader.diff_runner = slow
    _error(lambda: slow_reader.diff("session", "product", "primary", "text.txt"), "project_read_timeout")


@requires_safe_project_roots
def test_diff_keeps_descriptor_pinned_working_bytes_after_parent_replacement(tmp_path, monkeypatch):
    reader, _, root = _reader(tmp_path)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "config", "user.name", "Test")
    (root / "safe.txt").write_text("old\n", encoding="utf-8")
    _git(root, "add", "safe.txt")
    _git(root, "commit", "-qm", "initial")
    (root / "safe.txt").write_text("pinned\n", encoding="utf-8")
    escape = tmp_path / "escape"
    escape.mkdir()
    (escape / "safe.txt").write_text("escaped\n", encoding="utf-8")
    moved = tmp_path / "moved"
    original = project_files.os.open
    replaced = False

    def raced(path, flags, *args, **kwargs):
        nonlocal replaced
        descriptor = original(path, flags, *args, **kwargs)
        if not replaced and path == "safe.txt" and "dir_fd" in kwargs:
            replaced = True
            root.rename(moved)
            root.symlink_to(escape, target_is_directory=True)
        return descriptor

    monkeypatch.setattr(project_files.os, "open", raced)
    result = reader.diff("session", "product", "primary", "safe.txt")
    assert replaced and "pinned" in result["diff"] and "escaped" not in result["diff"]


@requires_safe_project_roots
def test_owner_deadline_refuses_a_slow_text_read(tmp_path):
    reader, _, root = _reader(tmp_path, clock=iter((0.0, 0.0, 3.0)).__next__, deadline_seconds=1)
    (root / "slow.txt").write_text("content", encoding="utf-8")
    _error(lambda: reader.text("session", "product", "primary", "slow.txt"), "project_read_timeout")

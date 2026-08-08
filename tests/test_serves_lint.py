"""`serves lint` — manifest defects that no other surface makes visible.

Every check here exists because the defect it finds occurred live on
2026-08-08 while every command reported success. See
docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md.
"""
import json
import textwrap

import pytest

from anvil_serving import serves


def _write(tmp_path, filename, body):
    path = tmp_path / filename
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(path)


def _entry(name, container, up=""):
    return f"""
        [[serve]]
        name = "{name}"
        container = "{container}"
        runtime = "docker"
        port = 30001
        model = "m"
        engine = "vllm"
        {up}
    """


def _lenient(path):
    """Load a manifest set the strict loader refuses.

    `serves lint` exists to report duplicate names, so it must be able to read
    a set that `load_manifest_set` rejects.
    """
    return serves.load_manifest_set(path, reject_duplicates=False)


def _never_worktree(*_a, **_k):
    raise AssertionError("git must not be consulted for this case")


def test_clean_manifest_reports_nothing(tmp_path):
    path = _write(tmp_path, "serves.toml", _entry("a", "ca") + _entry("b", "cb"))
    report = serves.lint_manifest_set(serves.load_manifest_set(path))
    assert report["findings"] == []
    assert report["serves_checked"] == 2


def test_duplicate_name_across_files_is_an_error(tmp_path):
    # The 2026-08-08 incident: the same serve declared in two files with
    # DIFFERENT containers. De-dup is by container, so both survive, name
    # selection becomes ambiguous, and the real running container reads as
    # unmanaged.
    main = _write(tmp_path, "serves.toml", _entry("dup", "container-old"))
    _write(tmp_path, "serves.extra.toml", _entry("dup", "container-new"))

    report = serves.lint_manifest_set(_lenient(main))
    dupes = [f for f in report["findings"] if f["check"] == "duplicate-serve-name"]
    assert len(dupes) == 1
    assert dupes[0]["severity"] == "error"
    assert dupes[0]["serve"] == "dup"
    assert "container-old" in dupes[0]["detail"]
    assert "container-new" in dupes[0]["detail"]
    assert len(dupes[0]["files"]) == 2
    assert report["errors"] == 1


def test_mirrored_entry_sharing_a_container_is_not_a_finding(tmp_path):
    # Sharing a container across files is the supported read-only mirror
    # pattern (see load_manifest_set); it must not be reported as a defect.
    main = _write(tmp_path, "serves.toml", _entry("m", "shared", up='up = "echo go"'))
    _write(tmp_path, "serves.mirror.toml", _entry("m", "shared"))

    report = serves.lint_manifest_set(serves.load_manifest_set(main))
    assert report["findings"] == []


def test_missing_registry_in_up_command_is_an_error(tmp_path):
    missing = (tmp_path / "gone.toml").as_posix()
    path = _write(tmp_path, "serves.toml", _entry(
        "r", "cr", up=f'up = "loader --registry {missing} --confirm"'))

    report = serves.lint_manifest_set(
        serves.load_manifest_set(path), _run=_never_worktree)
    findings = [f for f in report["findings"] if f["check"] == "missing-registry"]
    assert len(findings) == 1
    assert findings[0]["severity"] == "error"
    assert "gone.toml" in findings[0]["detail"]


def test_registry_inside_a_linked_worktree_is_a_warning(tmp_path):
    registry = tmp_path / "recipe.toml"
    registry.write_text("schema = 'x'\n", encoding="utf-8")
    path = _write(tmp_path, "serves.toml", _entry(
        "w", "cw", up=f'up = "loader --registry {registry.as_posix()} --confirm"'))

    class _Result:
        def __init__(self, out):
            self.returncode = 0
            self.stdout = out

    def _linked(argv, **_k):
        # A linked worktree reports a --git-dir under the main checkout's
        # --git-common-dir; the two differ only for linked worktrees.
        if "--git-common-dir" in argv:
            return _Result(str(tmp_path / "main" / ".git"))
        return _Result(str(tmp_path / "main" / ".git" / "worktrees" / "wt"))

    report = serves.lint_manifest_set(
        serves.load_manifest_set(path), _run=_linked)
    findings = [f for f in report["findings"]
                if f["check"] == "worktree-anchored-registry"]
    assert len(findings) == 1
    assert findings[0]["severity"] == "warning"
    assert report["errors"] == 0
    assert report["warnings"] == 1


def test_main_checkout_registry_is_not_flagged(tmp_path):
    registry = tmp_path / "recipe.toml"
    registry.write_text("schema = 'x'\n", encoding="utf-8")
    path = _write(tmp_path, "serves.toml", _entry(
        "w", "cw", up=f'up = "loader --registry {registry.as_posix()} --confirm"'))

    class _Result:
        returncode = 0
        stdout = ".git"

    report = serves.lint_manifest_set(
        serves.load_manifest_set(path), _run=lambda *a, **k: _Result())
    assert report["findings"] == []


def test_registry_equals_form_is_recognized(tmp_path):
    missing = (tmp_path / "gone.toml").as_posix()
    path = _write(tmp_path, "serves.toml", _entry(
        "r", "cr", up=f'up = "loader --registry={missing}"'))
    report = serves.lint_manifest_set(
        serves.load_manifest_set(path), _run=_never_worktree)
    assert [f["check"] for f in report["findings"]] == ["missing-registry"]


def test_cmd_lint_exits_nonzero_on_errors(tmp_path, capsys):
    main = _write(tmp_path, "serves.toml", _entry("dup", "c1"))
    _write(tmp_path, "serves.extra.toml", _entry("dup", "c2"))

    assert serves.cmd_lint(_lenient(main)) == 1
    assert "duplicate-serve-name" in capsys.readouterr().out


def test_cmd_lint_exits_zero_when_clean(tmp_path, capsys):
    clean_dir = tmp_path / "ok"
    clean_dir.mkdir()
    path = _write(clean_dir, "serves.toml", _entry("solo", "c3"))

    assert serves.cmd_lint(serves.load_manifest_set(path)) == 0
    assert "no findings" in capsys.readouterr().out


def test_cmd_lint_json_is_machine_readable(tmp_path, capsys):
    main = _write(tmp_path, "serves.toml", _entry("dup", "c1"))
    _write(tmp_path, "serves.extra.toml", _entry("dup", "c2"))

    assert serves.cmd_lint(_lenient(main), as_json=True) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["errors"] == 1
    assert report["findings"][0]["check"] == "duplicate-serve-name"


def test_strict_loader_rejects_duplicate_names(tmp_path):
    # Feature 2: prevention. The strict loader refuses what lint reports, so a
    # shadowed edit can no longer reach a live command.
    main = _write(tmp_path, "serves.toml", _entry("dup", "c1"))
    _write(tmp_path, "serves.extra.toml", _entry("dup", "c2"))

    with pytest.raises(ValueError, match="duplicate serve name"):
        serves.load_manifest_set(main)

    # ...and the message points at the tool that shows the whole picture.
    try:
        serves.load_manifest_set(main)
    except ValueError as exc:
        assert "serves lint" in str(exc)
        assert "c1" in str(exc) and "c2" in str(exc)


def test_strict_loader_still_accepts_container_mirrors(tmp_path):
    main = _write(tmp_path, "serves.toml", _entry("m", "shared", up='up = "echo go"'))
    _write(tmp_path, "serves.mirror.toml", _entry("m", "shared"))

    (only,) = serves.load_manifest_set(main)
    assert only["name"] == "m"
    assert only["up"] == ["echo", "go"]

"""`serves lint` — manifest defects that no other surface makes visible.

Every check here exists because the defect it finds occurred live on
2026-08-08 while every command reported success. See
docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md.
"""
import textwrap


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

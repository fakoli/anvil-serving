"""Daemonless lint of the repo-root `Dockerfile` + `.dockerignore` (router-service:T002,
ADR-0004). Asserts invariants by parsing the file text -- no Docker daemon, no build,
no network required, so this runs in plain CI.
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"


def _text() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def test_dockerfile_exists_at_repo_root():
    assert DOCKERFILE.is_file(), "Dockerfile must exist at repo root"


def test_dockerfile_uses_slim_python_311_plus_base():
    text = _text()
    m = re.search(r"^FROM\s+python:(\S+)", text, re.MULTILINE)
    assert m, "Dockerfile must have a `FROM python:...` base image"
    tag = m.group(1)
    assert "slim" in tag, f"base image tag should be a slim variant, got {tag!r}"
    version_match = re.match(r"(\d+)\.(\d+)", tag)
    assert version_match, f"could not parse a Python version out of tag {tag!r}"
    major, minor = int(version_match.group(1)), int(version_match.group(2))
    assert (major, minor) >= (3, 11), f"base image Python must be >=3.11, got {tag!r}"


def test_dockerfile_installs_package_with_no_extras():
    text = _text()
    # `pip install .` (no extras like `.[dev]`) -- stdlib-only runtime image.
    install_lines = [
        line for line in text.splitlines() if "pip install" in line and "RUN" in line
    ]
    assert install_lines, "Dockerfile must `pip install` the package"
    assert any(
        re.search(r"pip install[^\n]*\s\.(\s|\"|$)", line) for line in install_lines
    ), f"expected a bare `pip install .` (no extras), got: {install_lines}"
    assert not any(".[" in line for line in install_lines), (
        f"Dockerfile must not install optional extras, got: {install_lines}"
    )


def test_dockerfile_runs_as_non_root_user():
    text = _text()
    user_lines = [line for line in text.splitlines() if line.strip().startswith("USER ")]
    assert user_lines, "Dockerfile must set a non-root USER"
    for line in user_lines:
        user = line.strip().split(None, 1)[1].strip()
        assert user not in ("root", "0"), f"USER must not be root, got {line!r}"


def test_dockerfile_exposes_port_8000():
    text = _text()
    assert re.search(r"^EXPOSE\s+8000\b", text, re.MULTILINE), "Dockerfile must EXPOSE 8000"


def test_dockerfile_has_healthcheck_on_healthz():
    text = _text()
    assert "HEALTHCHECK" in text, "Dockerfile must declare a HEALTHCHECK"
    # The HEALTHCHECK instruction (which may wrap onto following lines via `\`) must
    # probe /healthz.
    idx = text.index("HEALTHCHECK")
    # Grab up to the next blank line or next top-level instruction as the HEALTHCHECK block.
    block_lines = []
    for line in text[idx:].splitlines():
        block_lines.append(line)
        if not line.rstrip().endswith("\\") and line is not block_lines[0]:
            break
    block = "\n".join(block_lines)
    assert "/healthz" in block, f"HEALTHCHECK must probe /healthz, got: {block!r}"


def test_dockerfile_entrypoint_runs_canonical_router_command():
    text = _text()
    assert "ENTRYPOINT" in text or "CMD" in text, (
        "Dockerfile must declare an ENTRYPOINT or CMD"
    )
    tail = text[text.index("ENTRYPOINT") if "ENTRYPOINT" in text else text.index("CMD"):]
    assert "anvil-serving router run" in tail, (
        "entrypoint must run `anvil-serving router run`"
    )
    assert "--host" in tail and "0.0.0.0" in tail, (
        "entrypoint must bind 0.0.0.0 INSIDE the container (host-side exposure is "
        "controlled by the published port, not the in-container bind -- CLAUDE.md "
        "gotcha #1 is about the host side)"
    )
    assert "--port" in tail and "8000" in tail, "entrypoint must bind port 8000"
    assert "ANVIL_CONFIG" in tail, (
        "entrypoint must read --config from ${ANVIL_CONFIG:-/etc/anvil/config.toml}"
    )


def test_dockerfile_never_hardcodes_a_secret_looking_literal():
    text = _text()
    # Defense-in-depth: no ANVIL_ROUTER_TOKEN value baked into the image build.
    assert "ANVIL_ROUTER_TOKEN=" not in text.replace(" ", "")


def test_dockerignore_exists_and_excludes_vcs_tests_docs_caches():
    assert DOCKERIGNORE.is_file(), ".dockerignore must exist at repo root"
    text = DOCKERIGNORE.read_text(encoding="utf-8")
    lines = {line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")}
    assert ".git" in lines, ".dockerignore must exclude .git"
    assert "tests" in lines, ".dockerignore must exclude tests"
    assert "docs" in lines, ".dockerignore must exclude docs"
    assert any("__pycache__" in line for line in lines), ".dockerignore must exclude __pycache__"
    # Agent worktrees under .claude/worktrees/ must not bloat the build context.
    assert ".claude" in lines, ".dockerignore must exclude .claude (agent worktrees)"

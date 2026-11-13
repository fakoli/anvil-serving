"""Exercise the pinned runtime's integrity gate without loading model weights."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "configs/runtime/ninfer-70434721-directio/verify-artifact.sh"
pytestmark = pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="Linux runtime verifier")


def invoke(path, expected, *, env=None):
    return subprocess.run(["bash", str(SCRIPT), str(path), expected, "4096"], env=env,
                          capture_output=True, text=True, timeout=10, check=False)


def test_digest_mismatch_blocks_startup(tmp_path):
    artifact = tmp_path / "model.bin"
    artifact.write_bytes(b"x" * 4096)
    # Exercise the digest branch independently of the runner filesystem's O_DIRECT support.
    fake_dd = tmp_path / "dd"
    fake_dd.write_text('#!/bin/sh\nset -eu\ntest "$#" -eq 4\n'
                       'test "$2" = iflag=direct\ntest "$3" = bs=4M\n'
                       'test "$4" = status=none\ncase "$1" in if=*) ;; *) exit 9;; esac\n'
                       'printf invoked > "${1#if=}.dd-invoked"\n'
                       'cat -- "${1#if=}"\n')
    fake_dd.chmod(0o755)
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]}
    assert invoke(artifact, hashlib.sha256(artifact.read_bytes()).hexdigest(), env=env).returncode == 0
    assert artifact.with_suffix(".bin.dd-invoked").read_text() == "invoked"
    result = invoke(artifact, "0" * 64, env=env)
    assert result.returncode != 0
    assert "mismatch" in result.stderr


def test_read_error_blocks_even_when_partial_output_digest_matches(tmp_path):
    fake_dd = tmp_path / "dd"
    fake_dd.write_text("#!/bin/sh\nprintf partial\nexit 7\n")
    fake_dd.chmod(0o755)
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]}
    artifact = tmp_path / "unused.bin"
    artifact.write_bytes(b"x" * 4096)
    result = invoke(artifact, hashlib.sha256(b"partial").hexdigest(), env=env)
    assert result.returncode != 0
    assert "verified" not in result.stdout

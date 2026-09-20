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


def test_direct_read_verifies_aligned_artifact_and_literal_path(tmp_path):
    artifact = tmp_path / "weights $(false); sample.bin"
    payload = b"qualified-model\0" * 256
    assert len(payload) == 4096
    artifact.write_bytes(payload)
    result = invoke(artifact, hashlib.sha256(payload).hexdigest())
    assert result.returncode == 0, result.stderr
    assert "verified with direct I/O" in result.stdout


def test_digest_mismatch_blocks_startup(tmp_path):
    artifact = tmp_path / "model.bin"
    artifact.write_bytes(b"x" * 4096)
    result = invoke(artifact, "0" * 64)
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


def test_missing_or_wrong_size_file_fails_before_read(tmp_path):
    missing = tmp_path / "missing.bin"
    assert invoke(missing, "0" * 64).returncode != 0
    missing.write_bytes(b"truncated")
    assert invoke(missing, hashlib.sha256(b"truncated").hexdigest()).returncode != 0


def test_file_growth_after_read_is_rejected(tmp_path):
    artifact = tmp_path / "growing.bin"
    payload = b"x" * 4096
    artifact.write_bytes(payload)
    fake_dd = tmp_path / "dd"
    fake_dd.write_text('#!/bin/sh\ncat -- "${1#if=}"\nprintf x >> "${1#if=}"\n')
    fake_dd.chmod(0o755)
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]}
    result = invoke(artifact, hashlib.sha256(payload).hexdigest(), env=env)
    assert result.returncode != 0
    assert "verified" not in result.stdout

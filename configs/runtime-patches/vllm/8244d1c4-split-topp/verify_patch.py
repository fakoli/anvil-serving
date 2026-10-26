"""Offline patch/hash/compile check; --install is for the derived-image build."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ORIGINAL_SHA256 = "0b5fc337058e22bc28a8530ccec904d6ccd89b9548b02955c657ac429b1669d2"
UPSTREAM_SHA256 = "af4e82ffd0fa8fc3b2ab7493c589d0c64c336d7b135e082575329670cc610c90"
PATCHED_SHA256 = "2b5305c406d8495e515f048ce0322c7ae5946a49e5f27bd5bbbf30a1e1fea0e6"
SOURCE_RELATIVE_PATH = Path("vllm/v1/sample/ops/topk_topp_triton.py")
ARTIFACT_DIRECTORY = Path(__file__).resolve().parent


def check_hash(path, expected):
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f"Source hash mismatch: {path.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--source", type=Path)
    mode.add_argument("--install", action="store_true")
    arguments = parser.parse_args()
    source = arguments.source
    if arguments.install:
        package = importlib.util.find_spec("vllm")
        if package is None or package.origin is None:
            raise RuntimeError("The pinned vLLM package is missing")
        source = Path(package.origin).parent.parent / SOURCE_RELATIVE_PATH
    check_hash(source, ORIGINAL_SHA256)
    with tempfile.TemporaryDirectory() as temporary_directory:
        replacement = Path(temporary_directory) / SOURCE_RELATIVE_PATH
        replacement.parent.mkdir(parents=True)
        shutil.copyfile(source, replacement)
        for patch_name, expected_hash in (
            ("split-topp.patch", UPSTREAM_SHA256),
            ("trace.patch", PATCHED_SHA256),
        ):
            subprocess.run(
                ["patch", "--batch", "--forward", "--fuzz=0", "-p1",
                 "-d", temporary_directory, "-i", str(ARTIFACT_DIRECTORY / patch_name)],
                check=True,
            )
            check_hash(replacement, expected_hash)
        for path in (source, replacement, *ARTIFACT_DIRECTORY.glob("*.py")):
            compile(path.read_bytes(), path.name, "exec")
        if arguments.install:
            shutil.copyfile(source, ARTIFACT_DIRECTORY / "baseline.py")
            shutil.copyfile(replacement, source)
            check_hash(source, PATCHED_SHA256)
    print(json.dumps({"offline_patch_check": "passed", "installed": arguments.install,
                      "source_sha256": ORIGINAL_SHA256, "patched_sha256": PATCHED_SHA256}))


if __name__ == "__main__":
    main()

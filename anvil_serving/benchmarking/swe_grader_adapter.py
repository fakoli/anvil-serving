"""Resource containment for the pinned official grader's Docker create seam.

Executed as a file by the grader's isolated Python environment. Docker is an
existing grader dependency, never an Anvil Serving runtime dependency.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path
import runpy
import subprocess
import sys


GRADER_REVISION = "f7bbbb2ccdf479001d6467c9e34af59e44a840f9"
SOURCE_HASHES = {
    "swebench/harness/docker_build.py": "5278842b60a7d38256f95f93c915dc84de2b8b4f286e9baae1b19280f768e484",
    "swebench/harness/run_evaluation.py": "6959f0b4e4eaf979771f529b88e3e9df1daa7fe86bc4291feec2e7d320bf7f2e",
}
LIMITS = {
    "mem_limit": 8 * 1024**3,
    "memswap_limit": 8 * 1024**3,
    "nano_cpus": 4 * 10**9,
    "pids_limit": 512,
    "network_mode": "none",
}
_PINNED_CREATE_KEYS = {"image", "name", "user", "detach", "command", "platform", "cap_add"}


def contained_kwargs(kwargs):
    """Reject unexpected privileges/options rather than hiding a conflicting policy."""
    unknown = set(kwargs) - _PINNED_CREATE_KEYS - set(LIMITS)
    if unknown or kwargs.get("cap_add") not in (None, []):
        raise RuntimeError("official grader requested unsupported container options")
    for key, expected in LIMITS.items():
        if key in kwargs and (type(kwargs[key]) is not type(expected) or kwargs[key] != expected):
            raise RuntimeError("official grader requested conflicting container limits")
    return {**kwargs, **LIMITS}


def install_container_guard(collection_class, image_ids=None):
    """Guard every create through the exact SDK seam used by pinned docker_build."""
    original = collection_class.create

    def create(self, *args, **kwargs):
        # Pinned source passes image and every other argument by keyword.
        if args:
            raise RuntimeError("official grader create signature changed")
        kwargs = contained_kwargs(kwargs)
        expected_image = None
        if image_ids is not None:
            tag = str(kwargs.get("image", "")).removeprefix("docker.io/")
            expected_image = image_ids.get(tag)
            if expected_image is None or self.client.images.get(tag).id != expected_image:
                raise RuntimeError("paired grader image identity changed or was not selected")
            kwargs["image"] = expected_image
        container = original(self, **kwargs)
        try:
            container.reload()
            config = container.attrs["HostConfig"]
            required = {
                "Memory": LIMITS["mem_limit"], "MemorySwap": LIMITS["memswap_limit"],
                "NanoCpus": LIMITS["nano_cpus"], "PidsLimit": LIMITS["pids_limit"],
                "NetworkMode": "none",
            }
            if any(config.get(k) != v for k, v in required.items()):
                raise RuntimeError("Docker did not enforce grader container limits")
            if config.get("Privileged") or config.get("CapAdd") or config.get("Binds"):
                raise RuntimeError("Docker grader container has unexpected host access")
            if expected_image is not None and container.attrs.get("Image") != expected_image:
                raise RuntimeError("Docker grader container image differs from paired identity")
        except Exception:
            container.remove(force=True)
            raise
        return container

    collection_class.create = create
    return original


def verify_source(root, *, runner=subprocess.run):
    root = Path(root).resolve(strict=True)
    for args in (["rev-parse", "HEAD"], ["status", "--porcelain", "--untracked-files=no"]):
        result = runner(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
        expected = GRADER_REVISION if args[0] == "rev-parse" else ""
        if result.returncode or result.stdout.strip() != expected:
            raise RuntimeError("official grader repository identity changed")
    for relative, expected in SOURCE_HASHES.items():
        path = root / relative
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
            raise RuntimeError("official grader source escaped its pinned checkout")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError("official grader source hash mismatch")
    return root


def verify_imports(root):
    """Reject cached/site-package graders even when the requested checkout is valid."""
    if any(name == "swebench" or name.startswith("swebench.") for name in sys.modules):
        raise RuntimeError("official grader was imported before containment")
    for relative in SOURCE_HASHES:
        module = relative.removesuffix(".py").replace("/", ".")
        spec = importlib.util.find_spec(module)
        if spec is None or spec.origin is None or Path(spec.origin).resolve() != root / relative:
            raise RuntimeError("official grader import is outside its pinned checkout")


def reject_image_build(*_args, **_kwargs):
    raise RuntimeError("contained SWE grading requires prebuilt images; automatic builds are disabled")


def reject_image_pull(*_args, **_kwargs):
    raise RuntimeError("paired SWE grading requires cached images; automatic pulls are disabled")


def validate_image_identities(value):
    if (not isinstance(value, dict) or not value or len(value) > 500
            or any(not isinstance(k, str)
                   or not re.fullmatch(r"swebench/sweb\.eval\.x86_64\.[a-z0-9_.-]+:latest", k)
                   or not isinstance(v, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", v)
                   for k, v in value.items())):
        raise RuntimeError("invalid paired grader image identities")
    return value


def load_image_identities(path, expected_sha256):
    source = Path(path)
    if source.stat().st_size > 262144:
        raise RuntimeError("paired image identity file exceeds size limit")
    value = validate_image_identities(json.loads(source.read_text(encoding="utf-8")))
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != expected_sha256:
        raise RuntimeError("paired image identity policy changed after planning")
    return value


def run_grader(root, argv, *, adapter_sha256, receipt, image_ids=None,
               runner=subprocess.run, run_module=runpy.run_module):
    own_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if own_hash != adapter_sha256:
        raise RuntimeError("grader containment adapter changed after planning")
    if image_ids is not None:
        image_ids = validate_image_identities(image_ids)
    root = verify_source(root, runner=runner)
    original_path = sys.path[:]
    # File launch otherwise shadows third-party requests with our requests.py.
    adapter_directory = Path(__file__).resolve().parent
    sys.path[:] = [str(root), *(entry for entry in original_path
                               if Path(entry).resolve() != adapter_directory)]
    try:
        verify_imports(root)
        # Available only in the pre-existing isolated SWE harness environment.
        from docker.models.containers import ContainerCollection
        from docker.api.build import BuildApiMixin
        if image_ids is not None:
            from docker.models.images import ImageCollection

        original = install_container_guard(ContainerCollection, image_ids)
        original_build = BuildApiMixin.build
        BuildApiMixin.build = reject_image_build
        original_pull = None
        if image_ids is not None:
            original_pull = ImageCollection.pull
            ImageCollection.pull = reject_image_pull
        original_argv = sys.argv
        try:
            Path(receipt).write_text(json.dumps({
                "schema": "anvil-serving.swe-containment/v1", "grader_revision": GRADER_REVISION,
                "source_hashes": SOURCE_HASHES, "adapter_sha256": own_hash,
                "container_limits": LIMITS, "status": "guard-installed",
                "image_build_policy": "prebuilt-only",
                "paired_image_ids": image_ids,
                "image_pull_policy": "never" if image_ids is not None else "upstream-cache-first",
            }, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            sys.argv = ["swebench.harness.run_evaluation", *argv]
            return run_module("swebench.harness.run_evaluation", run_name="__main__")
        finally:
            ContainerCollection.create = original
            BuildApiMixin.build = original_build
            if original_pull is not None:
                ImageCollection.pull = original_pull
            sys.argv = original_argv
    finally:
        sys.path[:] = original_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grader-root", required=True)
    parser.add_argument("--adapter-sha256", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--image-identities")
    parser.add_argument("--image-identities-sha256")
    parser.add_argument("grader_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    tail = args.grader_args
    if not tail or tail[0] != "--":
        parser.error("official grader arguments must follow --")
    image_ids = None
    if bool(args.image_identities) != bool(args.image_identities_sha256):
        parser.error("paired image identity path and digest must be supplied together")
    if args.image_identities:
        image_ids = load_image_identities(args.image_identities, args.image_identities_sha256)
    run_grader(args.grader_root, tail[1:], adapter_sha256=args.adapter_sha256,
               receipt=args.receipt, image_ids=image_ids)


if __name__ == "__main__":
    main()

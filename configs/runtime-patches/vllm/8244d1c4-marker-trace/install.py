"""Hash-bound offline patch application; --install is used only by the image build."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

DIRECTORY = Path(__file__).resolve().parent
SAMPLER_HASH = "2b5305c406d8495e515f048ce0322c7ae5946a49e5f27bd5bbbf30a1e1fea0e6"
LAUNCH_HASH = "24203cc50619e0b4116be44cecb14d800f6b2845298f998e1384c6adeb369cd1"


def check_hash(path, expected):
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("hash mismatch: " + path.name)


def apply(source_root, install=False):
    bindings = json.loads((DIRECTORY / "source-hashes.json").read_text())
    with tempfile.TemporaryDirectory() as temporary:
        destination = Path(temporary)
        for name, hashes in bindings.items():
            original = source_root / name
            check_hash(original, hashes["original"])
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
        subprocess.run(["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-d", temporary,
                        "-i", str(DIRECTORY / "marker-trace.patch")], check=True)
        for name, hashes in bindings.items():
            target = destination / name
            check_hash(target, hashes["patched"])
            compile(target.read_bytes(), name, "exec")
        for path in DIRECTORY.glob("*.py"):
            compile(path.read_bytes(), path.name, "exec")
        if install:
            check_hash(source_root / "vllm/v1/sample/ops/topk_topp_triton.py", SAMPLER_HASH)
            check_hash(Path("/opt/anvil-sampling/launch.py"), LAUNCH_HASH)
            shutil.copyfile(source_root / "vllm/v1/worker/gpu/sample/thinking_budget.py",
                            DIRECTORY / "baseline-thinking_budget.py")
            for name in bindings:
                shutil.copyfile(destination / name, source_root / name)
            shutil.copyfile(DIRECTORY / "anvil_marker_trace.py",
                            source_root / "vllm/v1/worker/gpu/sample/anvil_marker_trace.py")
            check_hash(source_root / "vllm/v1/sample/ops/topk_topp_triton.py", SAMPLER_HASH)
            check_hash(Path("/opt/anvil-sampling/launch.py"), LAUNCH_HASH)
    return bindings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--source-root", type=Path)
    mode.add_argument("--install", action="store_true")
    args = parser.parse_args()
    root = args.source_root
    if args.install:
        spec = importlib.util.find_spec("vllm")
        if spec is None or spec.origin is None:
            raise RuntimeError("Pinned vLLM package unavailable")
        root = Path(spec.origin).parent.parent
    bindings = apply(root, args.install)
    print(json.dumps({"marker_patch_verified": True, "installed": args.install,
                      "source_bindings": bindings}, sort_keys=True))


if __name__ == "__main__":
    main()

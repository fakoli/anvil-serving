"""Check owned bytes; optional local sources also exercise exact reconstruction."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest

CATALOG = Path(__file__).resolve().parent
ENGINE_BASE = "f350cd3ea253a042e205746928271f9d8f71a82d"
RECIPE_COMMIT = "0a5297c912e118490b8bb3734d9e20107df7f6f2"
AUTHOR_TREE = "9edb171eee69a8b8c3138d39755c52b1380c23e4"
FINAL_TREE = "e7acc47a4a42ba8ac5fbaf74fff20ed4e7856c17"
AUTHOR_HASH_MAP = "544dec546163a679ab4a62ccbd62c68a5a27608669b01d2739191ec09416378c"
MANIFEST_HASH = "fbf6b5b9a919e772c3cd9b8e369419aae5c94c2828a212424c4b3a83d7604e1f"
PATCH_HASHES = {
    "anvil-stream-count.patch": "e812fe99b19b57225f91750102a9163e5cc207367f78d103a0f0988f62f00991",
    "anvil-kv-pages-abi.patch": "fabb5ab12637e260ee6383100b839df92448f2bad120da56ea8dcad8f49d8daa",
    "anvil-queue-admission-timing.patch": "3e6b6af145b5fca676849f7edd95384a68386f0ea787f45a4fdb1ddbd7bfb229",
    "anvil-full-context-derived-bound.patch": "e0bfe68b8b28fbfd310e83d11db85425182f0c8571088a34561394c5844966a2",
    "anvil-telemetry-validation.patch": "cc9e6bd2305e3eb703601037b7c4f5bc1490c01c3ce1f12986b80ac2b7bc0ffe",
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def git(repository, *arguments, input_bytes=None):
    return subprocess.run(["git", "-C", str(repository), *arguments], input=input_bytes,
                          capture_output=True, check=True, timeout=60).stdout


class CatalogTests(unittest.TestCase):
    def test_qualified_patch_bytes_order_and_contract(self):
        raw = (CATALOG / "manifest.toml").read_bytes()
        self.assertEqual(digest(raw), MANIFEST_HASH)
        manifest = tomllib.loads(raw.decode())
        self.assertEqual(manifest["engine_base"], ENGINE_BASE)
        self.assertEqual(manifest["recipe_commit"], RECIPE_COMMIT)
        self.assertEqual(manifest["author_source_tree"], AUTHOR_TREE)
        self.assertEqual(manifest["author_patch_count"], 101)
        self.assertEqual(manifest["metric_contract_version"], 1)
        self.assertEqual(manifest["patches"], [
            {"file": name, "sha256": checksum} for name, checksum in PATCH_HASHES.items()
        ])
        self.assertEqual({path.name for path in CATALOG.glob("*.patch")}, set(PATCH_HASHES))
        for name, checksum in PATCH_HASHES.items():
            self.assertEqual(digest((CATALOG / name).read_bytes()), checksum, name)
        self.assertEqual(digest((CATALOG / "CONTRACT.md").read_bytes()),
                         "ac2a0d32997d6c069b3384695038b81d5bcd728dda791bf6c3a15ce3ec7f4a97")
        self.assertEqual(digest((CATALOG / "apply-anvil-patches.sh").read_bytes()),
                         "eba39135b359c38a8be87d9c8599dd32bd6c524d27174f86ddf0d5a31dac69d4")


def applicator_regressions(engine_root, temporary_root):
    """Exercise the retained applicator with dependent synthetic patches."""
    manifest_root = temporary_root / "synthetic-patches"
    manifest_root.mkdir()
    manifest = manifest_root / "manifest.toml"
    patch_entries = []
    for patch_number, before in enumerate(("", "first\n")):
        patch_path = manifest_root / f"anvil-{patch_number}.patch"
        patch = "diff --git a/anvil-manifest-probe b/anvil-manifest-probe\n"
        patch += ("new file mode 100644\n--- /dev/null\n" if not before else "--- a/anvil-manifest-probe\n")
        patch += "+++ b/anvil-manifest-probe\n"
        patch += ("@@ -0,0 +1 @@\n+first\n" if not before else "@@ -1 +1,2 @@\n first\n+second\n")
        patch_path.write_text(patch)
        patch_entries.append(f'[[patches]]\nfile = "{patch_path.name}"\nsha256 = "{digest(patch_path.read_bytes())}"\n')
    header = f'''schema = "anvil-tensorfold-patches/v1"
metric_contract_version = 1
engine_base = "{ENGINE_BASE}"
recipe_commit = "{RECIPE_COMMIT}"
author_patch_count = 101
author_source_tree = "{AUTHOR_TREE}"
'''
    command = ["sh", str(CATALOG / "apply-anvil-patches.sh"), str(engine_root), str(manifest)]
    for scenario in ("ordered", "reversed", "wrong_hash", "wrong_version", "duplicate_version", "source_drift"):
        git(engine_root, "read-tree", "--reset", "-u", AUTHOR_TREE)
        probe = engine_root / "anvil-manifest-probe"
        probe.unlink(missing_ok=True)
        text = header + "\n".join(reversed(patch_entries) if scenario == "reversed" else patch_entries)
        if scenario == "wrong_hash":
            text = text.replace(digest((manifest_root / "anvil-0.patch").read_bytes()), "0" * 64)
        if scenario == "wrong_version":
            text = text.replace("metric_contract_version = 1", "metric_contract_version = 2")
        if scenario == "duplicate_version":
            text = text.replace("metric_contract_version = 1", "metric_contract_version = 1\nmetric_contract_version = 2")
        if scenario == "source_drift":
            (engine_root / "anvil-unexpected-source").write_text("drift\n")
        manifest.write_text(text)
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        assert (result.returncode == 0) == (scenario == "ordered"), (scenario, result.stdout, result.stderr)
        if scenario == "ordered":
            assert probe.read_text() == "first\nsecond\n"
    git(engine_root, "read-tree", "--reset", "-u", AUTHOR_TREE)
    print("PASS: manifest order, hash, contract version and exact source custody")


def reconstruct(engine_source, recipe_source, zig=None):
    """Read pinned author bytes without modifying either original repository."""
    with tempfile.TemporaryDirectory(prefix="anvil-tensorfold-catalog-") as directory:
        temporary_root = Path(directory)
        engine_root = temporary_root / "engine"
        subprocess.run(["git", "clone", "--shared", "--quiet", "--no-checkout",
                        str(engine_source), str(engine_root)], check=True, timeout=60)
        git(engine_root, "checkout", "--quiet", "--detach", ENGINE_BASE)
        author_paths = git(recipe_source, "ls-tree", "-r", "--name-only", RECIPE_COMMIT,
                           "patches/engine").decode().splitlines()
        author_paths = [name for name in author_paths if name.endswith(".patch")]
        author_patches = {name: git(recipe_source, "show", f"{RECIPE_COMMIT}:{name}") for name in author_paths}
        assert len(author_patches) == 101
        author_hashes = {name: digest(raw) for name, raw in author_patches.items()}
        assert digest(json.dumps(author_hashes, sort_keys=True, separators=(",", ":")).encode()) == AUTHOR_HASH_MAP
        for raw in author_patches.values():
            git(engine_root, "apply", "--check", "-", input_bytes=raw)
            git(engine_root, "apply", "-", input_bytes=raw)
        git(engine_root, "add", "-A")
        assert git(engine_root, "write-tree").decode().strip() == AUTHOR_TREE
        applicator_regressions(engine_root, temporary_root)
        # Run the Dockerfile's real pre-compilation checks in the temporary
        # source tree. No Docker daemon, image or original checkout is changed.
        author_directory = temporary_root / "author-patches"
        author_directory.mkdir()
        for name, raw in author_patches.items():
            (author_directory / Path(name).name).write_bytes(raw)
        dockerfile = (CATALOG / "Dockerfile").read_text()
        checks = "export LC_ALL=C" + dockerfile.split("RUN export LC_ALL=C", 1)[1].split("\nRUN --mount=", 1)[0]
        checks = checks.replace("\\\n", "")
        for original, replacement in (("/opt/tf-patches/engine", author_directory),
                                      ("/opt/anvil-tensorfold-telemetry", CATALOG),
                                      ("/src/tensorfold", engine_root)):
            assert not any(character.isspace() for character in str(replacement))
            checks = checks.replace(original, str(replacement))
        first_author_patch = author_directory / Path(author_paths[0]).name
        original_bytes = first_author_patch.read_bytes()
        first_author_patch.write_bytes(original_bytes + b"\n")
        rejected = subprocess.run(["bash", "-ec", checks], cwd=engine_root, capture_output=True, timeout=60)
        assert rejected.returncode != 0, "Dockerfile accepted changed author patch bytes"
        assert git(engine_root, "write-tree").decode().strip() == AUTHOR_TREE
        first_author_patch.write_bytes(original_bytes)
        subprocess.run(["bash", "-ec", checks], cwd=engine_root, check=True, timeout=60)
        git(engine_root, "add", "-A")
        assert git(engine_root, "write-tree").decode().strip() == FINAL_TREE
        if zig:
            commands = [
                [zig, "test", "zig/src/core/lanes/anvil_telemetry.zig"],
                [zig, "test", "-lc", "--dep", "engine_api", "-Mroot=zig/src/server/metrics.zig",
                 "--dep", "lanes", "-Mengine_api=zig/src/core/engine_api.zig", "-Mlanes=zig/src/core/lanes/lanes.zig"],
                [zig, "test", "-lc", "--dep", "lanes", "--dep", "sys", "-Mroot=zig/src/core/lane_host_anvil_test.zig",
                 "--dep", "sys", "-Mlanes=zig/src/core/lanes/lanes.zig", "-Msys=zig/src/platform/sys.zig",
                 "--test-filter", "Anvil"],
            ]
            for command in commands:
                subprocess.run(command, cwd=engine_root, check=True, timeout=120)
        print(json.dumps({"engine_base": ENGINE_BASE, "author_patches_unchanged": len(author_patches),
                          "author_tree": AUTHOR_TREE, "manifest_sha256": MANIFEST_HASH,
                          "final_engine_tree": FINAL_TREE, "CPU_suites_run": bool(zig),
                          "image_build_or_runtime_action": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-source", type=Path)
    parser.add_argument("--recipe-source", type=Path)
    parser.add_argument("--zig", type=Path)
    arguments = parser.parse_args()
    if bool(arguments.engine_source) != bool(arguments.recipe_source) or (arguments.zig and not arguments.engine_source):
        parser.error("supply both local source repositories; --zig also requires them")
    if arguments.engine_source:
        reconstruct(arguments.engine_source.resolve(), arguments.recipe_source.resolve(),
                    str(arguments.zig.resolve()) if arguments.zig else None)
    unittest.main(argv=[__file__])

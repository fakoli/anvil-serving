from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from anvil_serving.benchmarking import swe_grader_adapter as adapter


def test_actual_create_receives_limits_and_inspection_confirms_them():
    calls = []

    class Container:
        attrs = {"HostConfig": {"Memory": 8589934592, "MemorySwap": 8589934592,
                 "NanoCpus": 4000000000, "PidsLimit": 512, "NetworkMode": "none"}}

        def reload(self):
            calls.append("reload")

    class Collection:
        def create(self, **kwargs):
            calls.append(kwargs)
            return Container()

    adapter.install_container_guard(Collection)
    Collection().create(image="pinned-image", command="tail -f /dev/null", cap_add=[])
    assert calls == [{"image": "pinned-image", "command": "tail -f /dev/null", "cap_add": [],
                      "mem_limit": 8589934592, "memswap_limit": 8589934592,
                      "nano_cpus": 4000000000, "pids_limit": 512, "network_mode": "none"}, "reload"]


@pytest.mark.parametrize("kwargs", [
    {"network_mode": "host"}, {"network": "host"}, {"privileged": True},
    {"cap_add": ["SYS_ADMIN"]}, {"volumes": {"/": {"bind": "/host"}}},
    {"mem_limit": 0}, {"memswap_limit": -1}, {"nano_cpus": 1},
    {"pids_limit": True}, {"cpu_quota": -1}, {"pid_mode": "host"},
])
def test_conflicting_or_alternative_access_options_fail_before_create(kwargs):
    with pytest.raises(RuntimeError):
        adapter.contained_kwargs({"image": "pinned", **kwargs})


def test_daemon_failure_to_enforce_removes_created_container_before_start():
    calls = []

    class Container:
        attrs = {"HostConfig": {"Memory": 0}}

        def reload(self):
            pass

        def remove(self, *, force):
            calls.append(("removed", force))

    class Collection:
        def create(self, **kwargs):
            return Container()

    adapter.install_container_guard(Collection)
    with pytest.raises(RuntimeError, match="did not enforce"):
        Collection().create(image="pinned")
    assert calls == [("removed", True)]


def test_source_hash_mismatch_refuses_before_any_grader_import(tmp_path):
    for name in adapter.SOURCE_HASHES:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("changed")

    def git(args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=adapter.GRADER_REVISION if "rev-parse" in args else "")

    with pytest.raises(RuntimeError, match="source hash mismatch"):
        adapter.verify_source(tmp_path, runner=git)


def test_adapter_hash_mismatch_refuses_before_source_or_import(tmp_path):
    with pytest.raises(RuntimeError, match="adapter changed"):
        adapter.run_grader(tmp_path, [], adapter_sha256="0" * 64, receipt=tmp_path / "receipt")


@pytest.mark.parametrize("paired", [False, True])
def test_official_grader_argv_passthrough_guard_installed_and_restored(tmp_path, monkeypatch, paired):
    original_path = [str(Path(adapter.__file__).resolve().parent), *sys.path]
    monkeypatch.setattr(sys, "path", original_path[:])
    class Collection:
        def create(self, **kwargs):
            return kwargs

    original = Collection.create
    module = ModuleType("docker.models.containers")
    module.ContainerCollection = Collection
    monkeypatch.setitem(sys.modules, "docker.models.containers", module)
    monkeypatch.setattr(adapter, "verify_source", lambda root, **kwargs: tmp_path)
    monkeypatch.setattr(adapter, "verify_imports", lambda root: None)
    build_module = ModuleType("docker.api.build")
    class BuildApiMixin:
        def build(self):
            raise AssertionError("unbounded build reached")
    build_module.BuildApiMixin = BuildApiMixin
    monkeypatch.setitem(sys.modules, "docker.api.build", build_module)
    class ImageCollection:
        def pull(self):
            raise AssertionError("unbounded pull reached")
    original_pull = ImageCollection.pull
    images_module = ModuleType("docker.models.images")
    images_module.ImageCollection = ImageCollection
    monkeypatch.setitem(sys.modules, "docker.models.images", images_module)
    seen = []
    def official(name, *, run_name):
        assert str(Path(adapter.__file__).resolve().parent) not in sys.path
        with pytest.raises(RuntimeError, match="prebuilt"):
            BuildApiMixin().build()
        if paired:
            with pytest.raises(RuntimeError, match="automatic pulls"):
                ImageCollection().pull()
        seen.append((name, run_name, sys.argv[:], Collection.create is not original))
        return {"unchanged_grader": True}
    result = adapter.run_grader(tmp_path, ["--max_workers", "1"],
                               adapter_sha256=hashlib.sha256(Path(adapter.__file__).read_bytes()).hexdigest(),
                               receipt=tmp_path / "receipt.json", run_module=official,
                               image_ids={"swebench/sweb.eval.x86_64.example:latest": "sha256:" + "a" * 64} if paired else None)
    assert result == {"unchanged_grader": True}
    assert seen == [("swebench.harness.run_evaluation", "__main__",
                     ["swebench.harness.run_evaluation", "--max_workers", "1"], True)]
    assert Collection.create is original
    assert sys.path == original_path
    assert ImageCollection.pull is original_pull
    assert (tmp_path / "receipt.json").is_file()


@pytest.mark.parametrize("observed", ["a", "b"])
def test_paired_grader_resolves_selected_tag_and_creates_by_immutable_id(observed):
    image_id = "sha256:" + "a" * 64
    tag = "swebench/sweb.eval.x86_64.example:latest"
    calls = []
    class Container:
        attrs = {"Image": image_id, "HostConfig": {
            "Memory": 8589934592, "MemorySwap": 8589934592,
            "NanoCpus": 4000000000, "PidsLimit": 512, "NetworkMode": "none"}}
        def reload(self):
            pass
    class Collection:
        client = SimpleNamespace(images=SimpleNamespace(get=lambda value: SimpleNamespace(id="sha256:" + observed * 64)))
        def create(self, **kwargs):
            calls.append(kwargs)
            return Container()
    adapter.install_container_guard(Collection, {tag: image_id})
    if observed == "a":
        Collection().create(image="docker.io/" + tag)
        assert calls[0]["image"] == image_id
    else:
        with pytest.raises(RuntimeError, match="image identity changed"):
            Collection().create(image=tag)
        assert calls == []


def test_paired_policy_file_digest_prevents_replacement(tmp_path):
    value = {"swebench/sweb.eval.x86_64.example:latest": "sha256:" + "a" * 64}
    digest = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    path = tmp_path / "images.json"
    path.write_text(json.dumps(value))
    assert adapter.load_image_identities(path, digest) == value
    path.write_text(json.dumps({next(iter(value)): "sha256:" + "b" * 64}))
    with pytest.raises(RuntimeError, match="policy changed"):
        adapter.load_image_identities(path, digest)


def test_preimported_grader_rejected(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "swebench", ModuleType("swebench"))
    with pytest.raises(RuntimeError, match="imported before"):
        adapter.verify_imports(tmp_path)


def test_wrong_import_origin_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter.importlib.util, "find_spec", lambda module: SimpleNamespace(origin="elsewhere.py"))
    with pytest.raises(RuntimeError, match="outside"):
        adapter.verify_imports(tmp_path)

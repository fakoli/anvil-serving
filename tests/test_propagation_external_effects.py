"""An installed managed home grants exact files, never the surrounding tree."""
from dataclasses import replace
import os

import pytest

from anvil_serving.propagation_fencing import NativeMutationFence, PropagationFenceError
from tests.test_propagation_fencing import _CONTRACT, _fence, _grant


pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX managed-home custody")


def external_fence(tmp_path):
    home = tmp_path / "owner"
    home.mkdir(mode=0o700)
    original, _ = _fence(home)
    managed = tmp_path / "managed"
    managed.mkdir(mode=0o700)
    target = managed / "config.json"
    target.write_bytes(b"before")
    target.chmod(0o600)
    owner = replace(original.owner, external_effect_paths=(target,),
                    effect_bindings={"catalog": ("catalog-apply", target)})
    return NativeMutationFence(owner, home), target


def test_external_effect_uses_original_journal_backup_and_exact_file_custody(tmp_path):
    fence, target = external_fence(tmp_path)
    grant = _grant(fence, target)
    with fence.transaction(grant, canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
        before = journal.read(target)
        journal.begin_effect("catalog", target, before, b"after")
        backup = journal.backup((target,))
        journal.bind_backup("catalog", backup)
        journal.write("catalog", b"after")
        journal.observe_bytes("catalog", b"after")
        assert backup.is_relative_to(fence.owner.storage_root)
        for unauthorized in (target.parent, target.parent / "other.json", target / "child"):
            for action in (journal.read, journal.files.read, journal.files.mode):
                with pytest.raises(PropagationFenceError, match="untrusted_target_path"):
                    action(unauthorized)
            with pytest.raises(PropagationFenceError, match="untrusted_target_path"):
                journal.files.write(unauthorized, b"denied")
    assert target.read_bytes() == b"after"
    assert fence.journal()["status"] == "completed"
    assert fence.journal_root.is_relative_to(fence.owner.storage_root)
    assert sorted(path.name for path in target.parent.iterdir()) == [target.name]


def test_external_paths_require_exact_installed_effect_binding(tmp_path):
    fence, target = external_fence(tmp_path)
    with pytest.raises(PropagationFenceError, match="untrusted_target_path"):
        replace(fence.owner, external_effect_paths=())
    with pytest.raises(PropagationFenceError, match="untrusted_target_path"):
        replace(fence.owner, external_effect_paths=(target, target.parent / "unbound"))
    with pytest.raises(PropagationFenceError, match="malformed_owner_context"):
        replace(fence.owner, external_effect_paths=(target, target))


@pytest.mark.parametrize("failure", ["symlink", "writable-parent", "replaced-parent"])
def test_external_effect_preserves_descriptor_custody(tmp_path, failure):
    fence, target = external_fence(tmp_path)
    with fence.preview(_grant(fence, target), canonical_contract=_CONTRACT,
                       target_paths=(target,)) as preview:
        assert preview.read(target) == b"before"
        if failure == "writable-parent":
            target.parent.chmod(0o777)
        elif failure == "symlink":
            target.unlink()
            target.symlink_to(fence.owner.storage_root / "catalog.json")
        else:
            target.parent.rename(tmp_path / "detached")
            target.parent.mkdir(mode=0o700)
            target.write_bytes(b"replacement")
        with pytest.raises(PropagationFenceError, match="unsafe_custody_path"):
            preview.read(target)

"""A reviewed approval pin cannot be replaced by request or filesystem input."""

import sys

import pytest

from anvil_serving.control_plane.controller import propagation_approval as approval_module
from anvil_serving.control_plane.controller.propagation_approval import PinnedApprovedContract
from anvil_serving.control_plane.controller.propagation_job_store import PropagationJobError
from anvil_serving.control_plane.propagation import ActiveIdentity, admit_contract, parse_contract
from tests.test_propagation_contracts import _contract, _NOW


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="native propagation owner is Linux-only")


def test_pinned_approval_requires_exact_protected_contract(tmp_path):
    tmp_path.chmod(0o700)
    parsed = parse_contract(_contract())
    path = tmp_path / "approval.json"
    path.write_bytes(parsed.canonical)
    path.chmod(0o600)
    registry = PinnedApprovedContract(path, parsed.digest)
    assert registry.contract_lookup("approval-1") == parsed.canonical
    assert admit_contract(registry.contract_lookup("approval-1"), registry.approval_lookup,
                          ActiveIdentity("activation-1", "a" * 64), _NOW).digest == parsed.digest
    assert registry.approval_lookup("other") is None
    with pytest.raises(PropagationJobError, match="approval_unavailable"):
        registry.contract_lookup("other")

    path.write_bytes(parsed.canonical + b" ")
    with pytest.raises(PropagationJobError, match="approval_unavailable"):
        registry.contract_lookup("approval-1")
    path.write_bytes(parsed.canonical)
    path.chmod(0o666)
    with pytest.raises(PropagationJobError, match="approval_unavailable"):
        registry.contract_lookup("approval-1")
    path.chmod(0o600)
    path.unlink()
    target = tmp_path / "target.json"
    target.write_bytes(parsed.canonical)
    path.symlink_to(target)
    with pytest.raises(PropagationJobError, match="approval_unavailable"):
        registry.contract_lookup("approval-1")


def test_approval_refuses_replaceable_ancestry_and_unpinned_digest(tmp_path):
    tmp_path.chmod(0o700)
    parsed = parse_contract(_contract())
    loose = tmp_path / "loose"
    loose.mkdir()
    loose.chmod(0o777)
    path = loose / "approval.json"
    path.write_bytes(parsed.canonical)
    path.chmod(0o600)
    with pytest.raises(PropagationJobError, match="approval_unavailable"):
        PinnedApprovedContract(path, parsed.digest).contract_lookup("approval-1")
    loose.chmod(0o700)
    with pytest.raises(PropagationJobError, match="approval_unavailable"):
        PinnedApprovedContract(path, "b" * 64).contract_lookup("approval-1")
    alias = tmp_path / "alias"
    alias.symlink_to(loose, target_is_directory=True)
    with pytest.raises(PropagationJobError, match="approval_unavailable"):
        PinnedApprovedContract(alias / "approval.json", parsed.digest).contract_lookup("approval-1")


def test_unsupported_platform_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(approval_module.sys, "platform", "win32")
    with pytest.raises(PropagationJobError, match="approval_unavailable"):
        PinnedApprovedContract(tmp_path / "approval.json", "a" * 64)

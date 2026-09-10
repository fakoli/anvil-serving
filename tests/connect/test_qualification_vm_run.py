import json

import pytest

from anvil_serving.connect import qualification_vm_run as subject
from anvil_serving.connect.qualification import QualificationError


def result():
    return {"schema": "anvil-connect.isolation-guest/v1", "ok": True,
            "cases": [{"name": name, "status": "passed"} for name in subject._CASES]}


def test_result_requires_every_case_in_fixed_order():
    value = result()
    assert subject._cases(json.dumps(value).encode()) == value["cases"]
    value["cases"].reverse()
    with pytest.raises(QualificationError):
        subject._cases(json.dumps(value).encode())


@pytest.mark.parametrize("mutation", ["missing", "extra", "contradiction", "unknown", "skip"])
def test_partial_or_inconsistent_guest_evidence_cannot_pass(mutation):
    value = result()
    if mutation == "missing":
        value["cases"].pop()
    elif mutation == "extra":
        value["secret"] = "unexpected"
    elif mutation == "contradiction":
        value["ok"] = False
    elif mutation == "unknown":
        value["cases"][0]["name"] = "unknown"
    else:
        value["cases"][0]["status"] = "skipped"
    with pytest.raises(QualificationError):
        subject._cases(json.dumps(value).encode())


def test_duplicate_json_key_fails():
    raw = json.dumps(result()).replace('"ok": true', '"ok": true, "ok": true').encode()
    with pytest.raises(QualificationError):
        subject._cases(raw)


def test_qemu_uses_only_pinned_descriptors_and_kvm_without_network():
    args = subject._qemu_argv(10, 11, 12, 13, 14)
    for flag, value in (("-nic", "none"), ("-machine", "q35,accel=kvm"), ("-display", "none"), ("-monitor", "none")):
        assert args[args.index(flag) + 1] == value
    assert "-nodefaults" in args and "-no-user-config" in args
    assert not any("virtfs" in value or "hostfwd" in value or "tcg" in value for value in args)
    with pytest.raises(QualificationError):
        subject._qemu_argv(10, 10, 12, 13, 14)


def test_seed_disables_default_users_networking_and_ssh(tmp_path):
    root = tmp_path / "seed"
    subject._seed(root, "a" * 32)
    user_data = (root / "user-data").read_text()
    assert "users: []" in user_data and "datasource_list: [NoCloud]" in user_data
    assert "ssh_genkeytypes: []" in user_data and "ssh_pwauth: false" in user_data
    assert "ro,nosuid,nodev" in user_data and "guest.py" in user_data
    assert (root / "network-config").read_text() == "version: 2\nethernets: {}\n"


def test_qemu_command_has_no_guest_agent_shares_or_default_devices():
    argv = subject._qemu_argv(20, 21, 22, 23, 24)
    forbidden = ("-enable-kvm", "-net", "-netdev", "-device", "virtio-9p", "vhost-user", "guest-agent", "hostfwd", "socket")
    rendered = "\n".join(argv)
    assert "-nodefaults" in argv and "-nic\nnone" in rendered
    assert all(value not in rendered for value in forbidden if value != "-device")
    assert argv.count("-device") == 1 and argv[argv.index("-device") + 1] == "virtio-blk-pci,drive=disk"


def test_private_cleanup_refuses_substituted_symlink(tmp_path):
    regular = tmp_path / "regular"
    regular.write_bytes(b"fixture")
    subject._remove_created(regular)
    assert not regular.exists()
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    substituted = tmp_path / "substituted"
    substituted.symlink_to(outside)
    with pytest.raises(QualificationError):
        subject._remove_created(substituted)
    assert outside.read_bytes() == b"outside"


def test_guest_result_bound_is_closed():
    with pytest.raises(QualificationError):
        subject._cases(b"x" * (subject._MAX_RESULT + 1))

import json

import pytest

from anvil_serving.connect import qualification_vm_run as subject
from anvil_serving.connect.qualification import QualificationError


def result():
    return {"schema": "anvil-connect.isolation-guest/v1", "ok": True,
            "cases": [{"name": name, "status": "passed"} for name in subject._CASES],
            "service_samples": {
                phase: {
                    role: {"memory_current_bytes": 0, "memory_peak_bytes": 0, "tasks_current": 0,
                           "memory_max_bytes": limits[0], "tasks_max": limits[1]}
                    for role, limits in subject._SERVICE_SAMPLE_LIMITS.items()
                }
                for phase in subject._SERVICE_SAMPLE_PHASES
            }}


def test_result_requires_every_case_in_fixed_order():
    value = result()
    assert subject._cases(json.dumps(value).encode()) == value["cases"]
    value["cases"].reverse()
    with pytest.raises(QualificationError):
        subject._cases(json.dumps(value).encode())


def test_service_samples_require_full_headroom_only_for_passing_packets():
    value = result()
    value["service_samples"]["before_restart"]["gateway"]["memory_peak_bytes"] = 268435457
    with pytest.raises(QualificationError):
        subject._guest_result(json.dumps(value).encode())
    value["ok"] = False
    value["cases"][0]["status"] = "failed"
    cases, samples = subject._guest_result(json.dumps(value).encode())
    assert cases[0]["status"] == "failed"
    assert samples["before_restart"]["gateway"]["memory_peak_bytes"] == 268435457


def test_failed_packet_allows_only_closed_partial_samples_and_labels_absence_unverified():
    value = result()
    value["ok"] = False
    value["cases"][0]["status"] = "failed"
    value["service_samples"] = {"before_restart": {"gateway": value["service_samples"]["before_restart"]["gateway"]}}
    _cases, samples = subject._guest_result(json.dumps(value).encode())
    limits = subject._service_limit_evidence(samples)
    assert limits["gateway"]["enforcement"] == "validated"
    assert limits["edge"]["enforcement"] == "unverified"
    assert limits["client:dashboard-api"]["enforcement"] == "rendered-only"
    value["service_samples"]["before_restart"]["unknown"] = {}
    with pytest.raises(QualificationError):
        subject._guest_result(json.dumps(value).encode())


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


def failure_result():
    value = result()
    value['ok'] = False
    value['cases'][2].update(status='failed', failure={
        'kind': 'os', 'errno': 13, 'returncode': None,
        'frames': [{'source': 'guest', 'line': 123}, {'source': 'manage', 'line': 456}],
    })
    return value


def test_closed_failure_coordinates_are_retained_without_changing_case_counts():
    value = failure_result()
    cases = subject._cases(json.dumps(value).encode())
    assert cases == value['cases']
    assert subject._counts(cases)['failed'] == 1
    assert subject._counts(cases)['passed'] == 7


@pytest.mark.parametrize('mutation', [
    'text', 'path', 'kind', 'extra_frame', 'bool_line', 'large_line',
    'bool_errno', 'large_errno', 'large_returncode', 'too_many_frames', 'passed',
])
def test_failure_coordinates_reject_unbounded_or_untrusted_fields(mutation):
    value = failure_result()
    case = value['cases'][2]
    failure = case['failure']
    if mutation == 'text': failure['message'] = 'SECRET'
    elif mutation == 'path': failure['frames'][0]['source'] = '/private/SECRET'
    elif mutation == 'kind': failure['kind'] = 'SECRET'
    elif mutation == 'extra_frame': failure['frames'][0]['text'] = 'SECRET'
    elif mutation == 'bool_line': failure['frames'][0]['line'] = True
    elif mutation == 'large_line': failure['frames'][0]['line'] = 100_000
    elif mutation == 'bool_errno': failure['errno'] = True
    elif mutation == 'large_errno': failure['errno'] = 4096
    elif mutation == 'large_returncode': failure['returncode'] = 2 ** 31
    elif mutation == 'too_many_frames': failure['frames'] *= 5
    else:
        case['status'] = 'passed'
        value['ok'] = True
    with pytest.raises(QualificationError):
        subject._cases(json.dumps(value).encode())

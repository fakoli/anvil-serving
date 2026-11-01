import json

import pytest

from anvil_serving.connect import qualification_vm_run as subject
from anvil_serving.connect.qualification import QualificationError


def result():
    return {"schema": "anvil-connect.isolation-guest/v1", "ok": True,
            "cases": [{"name": name, "status": "passed"} for name in subject._CASES],
            "service_samples": {
                phase: {
                    role: {"memory_current_bytes": 1, "memory_peak_bytes": 2, "tasks_current": 1,
                           "memory_max_bytes": limits[0], "tasks_max": limits[1]}
                    for role, limits in subject._SERVICE_SAMPLE_LIMITS.items()
                }
                for phase in subject._SERVICE_SAMPLE_PHASES
            }}


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


def failure_result():
    value = result()
    value['ok'] = False
    value['cases'][2].update(status='failed', failure={
        'kind': 'os', 'errno': 13, 'returncode': None,
        'frames': [{'source': 'guest', 'line': 123}, {'source': 'manage', 'line': 456}],
    })
    return value


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

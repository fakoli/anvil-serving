"""Tests for `anvil_serving.gpus` — shared GPU enumeration + index<->UUID
resolution (genericity:T007). `nvidia-smi` is injected via `_run`, so these
run with no GPU and no `nvidia-smi` on PATH.
"""
import subprocess

from anvil_serving import gpus

CSV = (
    "0, GPU-33333333-3333-3333-3333-333333333333, NVIDIA GeForce RTX 5090\n"
    "1, GPU-11111111-1111-1111-1111-111111111111, NVIDIA RTX PRO 6000 Blackwell\n"
)

REORDERED_CSV = (
    "0, GPU-11111111-1111-1111-1111-111111111111, NVIDIA RTX PRO 6000 Blackwell\n"
    "1, GPU-33333333-3333-3333-3333-333333333333, NVIDIA GeForce RTX 5090\n"
)

CAPACITY_CSV = (
    "0, GPU-33333333-3333-3333-3333-333333333333, NVIDIA GeForce RTX 5090, 32607\n"
    "1, GPU-11111111-1111-1111-1111-111111111111, NVIDIA RTX PRO 6000 Blackwell, 97887\n"
)

ROLES = (
    {"id": "fast", "uuid": "GPU-33333333-3333-3333-3333-333333333333"},
    {"id": "heavy", "uuid": "GPU-11111111-1111-1111-1111-111111111111"},
)


def _run_ok(*a, **k):
    return CSV


def _run_missing(*a, **k):
    raise FileNotFoundError("nvidia-smi not found")


def _run_reordered(*a, **k):
    return REORDERED_CSV


# ---- list_gpus ---------------------------------------------------------------

def test_list_gpus_parses_csv():
    rows = gpus.list_gpus(_run=_run_ok)
    assert rows == [
        {"index": 0, "uuid": "GPU-33333333-3333-3333-3333-333333333333",
         "name": "NVIDIA GeForce RTX 5090"},
        {"index": 1, "uuid": "GPU-11111111-1111-1111-1111-111111111111",
         "name": "NVIDIA RTX PRO 6000 Blackwell"},
    ]


def test_list_gpus_empty_when_nvidia_smi_missing():
    assert gpus.list_gpus(_run=_run_missing) == []


def test_list_gpus_query_is_time_bounded():
    def timeout(argv, **kwargs):
        assert kwargs["timeout"] == gpus.DEFAULT_QUERY_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    assert gpus.list_gpus(_run=timeout) == []


def test_list_gpus_empty_on_any_error():
    def boom(*a, **k):
        raise RuntimeError("boom")
    assert gpus.list_gpus(_run=boom) == []


def test_list_gpus_with_memory_parses_capacity_for_role_selection():
    rows = gpus.list_gpus_with_memory(_run=lambda *a, **k: CAPACITY_CSV)

    assert rows == [
        {
            "index": 0,
            "uuid": "GPU-33333333-3333-3333-3333-333333333333",
            "name": "NVIDIA GeForce RTX 5090",
            "memory_total_mib": 32607,
        },
        {
            "index": 1,
            "uuid": "GPU-11111111-1111-1111-1111-111111111111",
            "name": "NVIDIA RTX PRO 6000 Blackwell",
            "memory_total_mib": 97887,
        },
    ]


def test_list_gpus_with_memory_ignores_malformed_rows():
    output = "bad row\n0, GPU-valid, GPU Name, not-a-number\n"

    assert gpus.list_gpus_with_memory(_run=lambda *a, **k: output) == []


# ---- gpu_uuid ------------------------------------------------------------------

def test_gpu_uuid_maps_index_to_uuid():
    assert gpus.gpu_uuid(1, _run=_run_ok) == "GPU-11111111-1111-1111-1111-111111111111"


def test_gpu_uuid_none_when_index_not_found():
    assert gpus.gpu_uuid(9, _run=_run_ok) is None


def test_gpu_uuid_none_when_nvidia_smi_missing():
    assert gpus.gpu_uuid(0, _run=_run_missing) is None


# ---- resolve_gpu ---------------------------------------------------------------

def test_resolve_gpu_index_present_no_warning():
    uuid, warning = gpus.resolve_gpu(1, _run=_run_ok)
    assert uuid == "GPU-11111111-1111-1111-1111-111111111111"
    assert warning is None


def test_resolve_gpu_index_as_string():
    uuid, warning = gpus.resolve_gpu("0", _run=_run_ok)
    assert uuid == "GPU-33333333-3333-3333-3333-333333333333"
    assert warning is None


def test_resolve_gpu_uuid_spec_passthrough():
    spec = "GPU-33333333-3333-3333-3333-333333333333"
    uuid, warning = gpus.resolve_gpu(spec, _run=_run_ok)
    assert uuid == spec
    assert warning is None


def test_resolve_gpu_nvidia_smi_absent_falls_back_with_warning():
    uuid, warning = gpus.resolve_gpu(1, _run=_run_missing)
    assert uuid is None
    assert warning and "nvidia-smi" in warning
    assert "1" in warning


def test_resolve_gpu_uuid_spec_nvidia_smi_absent_falls_back_with_warning():
    spec = "GPU-deadbeef-0000-1111-2222-333344445555"
    uuid, warning = gpus.resolve_gpu(spec, _run=_run_missing)
    assert uuid is None
    assert warning and "nvidia-smi" in warning


def test_resolve_gpu_index_not_reported_falls_back_with_warning():
    uuid, warning = gpus.resolve_gpu(9, _run=_run_ok)
    assert uuid is None
    assert warning and "9" in warning


def test_resolve_gpu_never_raises_on_garbage_spec():
    uuid, warning = gpus.resolve_gpu("not-a-gpu", _run=_run_ok)
    assert uuid is None or isinstance(uuid, str)  # no crash either way
    # "not-a-gpu" contains 2 hyphens -> treated as UUID-shaped, unresolved
    assert warning is not None


# ---- resolve_gpu_roles --------------------------------------------------------

def test_canonical_gpu_uuid_normalizes_hexadecimal_case():
    assert gpus.canonical_gpu_uuid(ROLES[0]["uuid"]) == "GPU-33333333-3333-3333-3333-333333333333"


def test_resolve_gpu_roles_keeps_role_ownership_when_indexes_reorder():
    original = gpus.resolve_gpu_roles(ROLES, _run=_run_ok)
    reordered = gpus.resolve_gpu_roles(ROLES, _run=_run_reordered)

    assert [(row["role"], row["uuid"]) for row in original] == [
        ("fast", "GPU-33333333-3333-3333-3333-333333333333"),
        ("heavy", "GPU-11111111-1111-1111-1111-111111111111"),
    ]
    assert [(row["role"], row["uuid"]) for row in reordered] == [
        ("fast", "GPU-33333333-3333-3333-3333-333333333333"),
        ("heavy", "GPU-11111111-1111-1111-1111-111111111111"),
    ]
    assert [row["index"] for row in original] == [0, 1]
    assert [row["index"] for row in reordered] == [1, 0]


def test_resolve_gpu_roles_output_includes_role_uuid_and_runtime_context():
    row = gpus.resolve_gpu_roles(ROLES, _run=_run_ok)[0]

    assert row == {
        "role": "fast",
        "uuid": "GPU-33333333-3333-3333-3333-333333333333",
        "index": 0,
        "name": "NVIDIA GeForce RTX 5090",
    }


def test_resolve_gpu_roles_rejects_missing_uuid_before_gpu_discovery():
    calls = []

    def unexpected_run(*a, **k):
        calls.append((a, k))
        raise AssertionError("nvidia-smi must not run for invalid configuration")

    try:
        gpus.resolve_gpu_roles(({"id": "fast"},), _run=unexpected_run)
    except gpus.GpuRoleResolutionError as exc:
        assert "missing a UUID" in str(exc)
    else:
        raise AssertionError("missing configured UUID must fail")
    assert calls == []


def test_resolve_gpu_roles_rejects_duplicate_configured_uuid_before_gpu_discovery():
    calls = []

    def unexpected_run(*a, **k):
        calls.append((a, k))
        raise AssertionError("nvidia-smi must not run for invalid configuration")

    duplicate_uuid = "GPU-" + ROLES[0]["uuid"][4:].lower()
    roles = (ROLES[0], {"id": "heavy", "uuid": duplicate_uuid})
    try:
        gpus.resolve_gpu_roles(roles, _run=unexpected_run)
    except gpus.GpuRoleResolutionError as exc:
        assert "duplicate configured GPU UUID" in str(exc)
    else:
        raise AssertionError("duplicate configured UUID must fail")
    assert calls == []


def test_resolve_gpu_roles_rejects_missing_observed_uuid():
    roles = ({"id": "fast", "uuid": "GPU-deadbeef-0000-1111-2222-333344445555"},)

    try:
        gpus.resolve_gpu_roles(roles, _run=_run_ok)
    except gpus.GpuRoleResolutionError as exc:
        assert "did not report" in str(exc)
    else:
        raise AssertionError("mismatched UUID must fail")


def test_resolve_gpu_roles_rejects_duplicate_observed_uuid():
    duplicate_csv = CSV + "2, GPU-33333333-3333-3333-3333-333333333333, duplicate\n"

    try:
        gpus.resolve_gpu_roles(ROLES, _run=lambda *a, **k: duplicate_csv)
    except gpus.GpuRoleResolutionError as exc:
        assert "duplicate GPU UUID" in str(exc)
    else:
        raise AssertionError("duplicate observed UUID must fail")

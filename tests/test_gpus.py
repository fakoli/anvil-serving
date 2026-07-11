"""Tests for `anvil_serving.gpus` — shared GPU enumeration + index<->UUID
resolution (genericity:T007). `nvidia-smi` is injected via `_run`, so these
run with no GPU and no `nvidia-smi` on PATH.
"""
from anvil_serving import gpus

CSV = (
    "0, GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1, NVIDIA GeForce RTX 5090\n"
    "1, GPU-d0f446cf-1771-414c-e116-a39138798a8c, NVIDIA RTX PRO 6000 Blackwell\n"
)

REORDERED_CSV = (
    "0, GPU-d0f446cf-1771-414c-e116-a39138798a8c, NVIDIA RTX PRO 6000 Blackwell\n"
    "1, GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1, NVIDIA GeForce RTX 5090\n"
)

ROLES = (
    {"id": "fast", "uuid": "GPU-04D3B6E7-5691-3E86-1D34-C37999440CF1"},
    {"id": "heavy", "uuid": "GPU-D0F446CF-1771-414C-E116-A39138798A8C"},
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
        {"index": 0, "uuid": "GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1",
         "name": "NVIDIA GeForce RTX 5090"},
        {"index": 1, "uuid": "GPU-d0f446cf-1771-414c-e116-a39138798a8c",
         "name": "NVIDIA RTX PRO 6000 Blackwell"},
    ]


def test_list_gpus_empty_when_nvidia_smi_missing():
    assert gpus.list_gpus(_run=_run_missing) == []


def test_list_gpus_empty_on_any_error():
    def boom(*a, **k):
        raise RuntimeError("boom")
    assert gpus.list_gpus(_run=boom) == []


# ---- gpu_uuid ------------------------------------------------------------------

def test_gpu_uuid_maps_index_to_uuid():
    assert gpus.gpu_uuid(1, _run=_run_ok) == "GPU-d0f446cf-1771-414c-e116-a39138798a8c"


def test_gpu_uuid_none_when_index_not_found():
    assert gpus.gpu_uuid(9, _run=_run_ok) is None


def test_gpu_uuid_none_when_nvidia_smi_missing():
    assert gpus.gpu_uuid(0, _run=_run_missing) is None


# ---- resolve_gpu ---------------------------------------------------------------

def test_resolve_gpu_index_present_no_warning():
    uuid, warning = gpus.resolve_gpu(1, _run=_run_ok)
    assert uuid == "GPU-d0f446cf-1771-414c-e116-a39138798a8c"
    assert warning is None


def test_resolve_gpu_index_as_string():
    uuid, warning = gpus.resolve_gpu("0", _run=_run_ok)
    assert uuid == "GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1"
    assert warning is None


def test_resolve_gpu_uuid_spec_passthrough():
    spec = "GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1"
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
    assert gpus.canonical_gpu_uuid(ROLES[0]["uuid"]) == "GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1"


def test_resolve_gpu_roles_keeps_role_ownership_when_indexes_reorder():
    original = gpus.resolve_gpu_roles(ROLES, _run=_run_ok)
    reordered = gpus.resolve_gpu_roles(ROLES, _run=_run_reordered)

    assert [(row["role"], row["uuid"]) for row in original] == [
        ("fast", "GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1"),
        ("heavy", "GPU-d0f446cf-1771-414c-e116-a39138798a8c"),
    ]
    assert [(row["role"], row["uuid"]) for row in reordered] == [
        ("fast", "GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1"),
        ("heavy", "GPU-d0f446cf-1771-414c-e116-a39138798a8c"),
    ]
    assert [row["index"] for row in original] == [0, 1]
    assert [row["index"] for row in reordered] == [1, 0]


def test_resolve_gpu_roles_output_includes_role_uuid_and_runtime_context():
    row = gpus.resolve_gpu_roles(ROLES, _run=_run_ok)[0]

    assert row == {
        "role": "fast",
        "uuid": "GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1",
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
    duplicate_csv = CSV + "2, GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1, duplicate\n"

    try:
        gpus.resolve_gpu_roles(ROLES, _run=lambda *a, **k: duplicate_csv)
    except gpus.GpuRoleResolutionError as exc:
        assert "duplicate GPU UUID" in str(exc)
    else:
        raise AssertionError("duplicate observed UUID must fail")

"""Tests for `anvil_serving.gpus` — shared GPU enumeration + index<->UUID
resolution (genericity:T007). `nvidia-smi` is injected via `_run`, so these
run with no GPU and no `nvidia-smi` on PATH.
"""
from anvil_serving import gpus

CSV = (
    "0, GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1, NVIDIA GeForce RTX 5090\n"
    "1, GPU-d0f446cf-1771-414c-e116-a39138798a8c, NVIDIA RTX PRO 6000 Blackwell\n"
)


def _run_ok(*a, **k):
    return CSV


def _run_missing(*a, **k):
    raise FileNotFoundError("nvidia-smi not found")


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

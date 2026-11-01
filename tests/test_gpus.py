"""Tests for `anvil_serving.gpus` — shared GPU enumeration + index<->UUID
resolution (genericity:T007). `nvidia-smi` is injected via `_run`, so these
run with no GPU and no `nvidia-smi` on PATH.
"""

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


# ---- gpu_uuid ------------------------------------------------------------------


# ---- resolve_gpu ---------------------------------------------------------------


# ---- resolve_gpu_roles --------------------------------------------------------


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
